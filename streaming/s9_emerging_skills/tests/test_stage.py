"""Test S9 through the shared event-time runner and message contracts."""

import uuid
import unittest

import pytest

from ace.stage import WindowedStage, run_flink_local, run_local
from ..stage import EmergingSkillsStage


def demand(day: str, unmapped: list[str]) -> dict[str, object]:
    """Build one valid skills.demand input record for a test."""
    return {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": "DE",
        "posted_date": day,
        "skills": ["SIEM"],
        "unmapped": unmapped,
    }


class EmergingSkillsStageTests(unittest.TestCase):
    """Check S9 windowing, rolling memory, and schema validation."""

    def test_is_a_shared_windowed_stage_and_emits_contract_valid_output(self) -> None:
        stage = EmergingSkillsStage(epsilon=0.1)
        records = [
            demand("2026-01-30", ["Zero Trust"]),
            demand("2026-01-31", ["zero trust"]),
        ]

        self.assertIsInstance(stage, WindowedStage)
        result = run_local(stage, records)

        self.assertEqual(result.invalid, [])
        output = result.published("agg.emerging_skills")
        self.assertEqual(len(output), 2)
        self.assertEqual(
            output[-1]["items"],
            [{"mention": "zero trust", "count": 2, "max_error": 0}],
        )

    def test_drops_previous_day_records_that_arrive_after_the_grace_period(self) -> None:
        late_record = demand("2026-01-30", ["IAM"])
        result = run_local(
            EmergingSkillsStage(epsilon=0.1),
            [demand("2026-02-01", ["SIEM"]), late_record],
        )

        self.assertEqual(result.late, [late_record])
        self.assertEqual(
            result.published("agg.emerging_skills")[-1]["day"], "2026-02-01"
        )

    def test_routes_schema_invalid_input_to_errors_invalid(self) -> None:
        bad = demand("2026-01-31", ["SIEM"])
        bad["unmapped"] = "not-a-list"

        result = run_local(EmergingSkillsStage(), [bad])

        self.assertEqual(result.published("agg.emerging_skills"), [])
        self.assertEqual(result.invalid[0]["topic"], "skills.demand")

    def test_skips_blank_mentions_instead_of_failing_the_stage(self) -> None:
        result = run_local(
            EmergingSkillsStage(epsilon=0.1),
            [demand("2026-01-31", ["", " ", "SIEM"])],
        )

        self.assertEqual(result.invalid, [])
        self.assertEqual(
            result.published("agg.emerging_skills")[0]["items"],
            [{"mention": "siem", "count": 1, "max_error": 0}],
        )

    @pytest.mark.flink
    def test_flink_gives_the_same_result_as_local(self) -> None:
        records = [
            demand("2026-01-30", ["SIEM", "Zero Trust"]),
            demand("2026-01-31", ["SIEM"]),
            demand("2026-02-01", ["Cloud security"]),
        ]

        flink = [
            result.value
            for result in run_flink_local(EmergingSkillsStage(epsilon=0.1), records)
            if result.topic == "agg.emerging_skills"
        ]
        local = run_local(
            EmergingSkillsStage(epsilon=0.1), records
        ).published("agg.emerging_skills")

        self.assertEqual(flink, local)


if __name__ == "__main__":
    unittest.main()
