"""Test S10 through the shared event-time runner and message contracts."""

import uuid
import unittest

import pytest

from ace.stage import WindowedStage, run_flink_local, run_local
from ..stage import HiringCompaniesStage


def job(day: str, company: str | None = None) -> dict[str, object]:
    """Build one valid jobs.clean input record for a test."""
    record: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": "DE",
        "posted_date": day,
        "job_title": "Security Engineer",
        "description": "A test job ad.",
    }
    if company is not None:
        record["company"] = company
    return record


class HiringCompaniesStageTests(unittest.TestCase):
    """Check S10 windowing, company estimates, missing names, and validation."""

    def test_is_a_shared_windowed_stage_and_emits_contract_valid_output(self) -> None:
        stage = HiringCompaniesStage(precision=8)
        records = [
            job("2026-01-30", "Example GmbH"),
            job("2026-01-30", "example gmbh"),
            job("2026-01-30"),
            job("2026-01-31", "Example SAS"),
        ]

        self.assertIsInstance(stage, WindowedStage)
        result = run_local(stage, records)

        self.assertEqual(result.invalid, [])
        outputs = result.published("agg.companies_daily")
        self.assertEqual(len(outputs), 2)
        self.assertEqual(outputs[0]["companies_day"], 1)
        self.assertEqual(outputs[0]["missing_company"], 1)
        self.assertEqual(outputs[1]["companies_30d"], 2)

    def test_drops_previous_day_records_that_arrive_after_the_grace_period(self) -> None:
        late_record = job("2026-01-30", "Earlier Inc")
        result = run_local(
            HiringCompaniesStage(precision=8),
            [job("2026-02-01", "Later Inc"), late_record],
        )

        self.assertEqual(result.late, [late_record])
        self.assertEqual(
            result.published("agg.companies_daily")[-1]["companies_30d"], 1
        )

    def test_routes_schema_invalid_input_to_errors_invalid(self) -> None:
        bad = job("2026-01-31")
        bad["company"] = 123

        result = run_local(HiringCompaniesStage(), [bad])

        self.assertEqual(result.published("agg.companies_daily"), [])
        self.assertEqual(result.invalid[0]["topic"], "jobs.clean")

    def test_skips_non_text_company_if_it_reaches_the_stage(self) -> None:
        stage = HiringCompaniesStage(precision=8)
        state = stage.new()

        stage.add(state, {"company": 123})

        self.assertEqual(state["missing_company"], 1)

    @pytest.mark.flink
    def test_flink_gives_the_same_result_as_local(self) -> None:
        records = [
            job("2026-01-30", "Example GmbH"),
            job("2026-01-30", "example gmbh"),
            job("2026-01-30"),
            job("2026-01-31", "Example SAS"),
            job("2026-02-01", "Another Ltd"),
        ]

        flink = [
            result.value
            for result in run_flink_local(HiringCompaniesStage(precision=8), records)
            if result.topic == "agg.companies_daily"
        ]
        local = run_local(
            HiringCompaniesStage(precision=8), records
        ).published("agg.companies_daily")

        self.assertEqual(flink, local)


if __name__ == "__main__":
    unittest.main()
