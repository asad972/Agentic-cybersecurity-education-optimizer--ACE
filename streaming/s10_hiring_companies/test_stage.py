"""Tests for mapping jobs.clean messages into S10 daily output."""

import unittest

from stage import HiringCompaniesStage


class HiringCompaniesStageTests(unittest.TestCase):
    """Check S10 message mapping and stateful rolling-window aggregation."""

    def test_aggregates_companies_and_missing_names_for_the_target_day(self) -> None:
        output = HiringCompaniesStage(precision=8).process_day(
            [
                {
                    "country": "DE",
                    "posted_date": "2026-01-31",
                    "company": "Example GmbH",
                },
                {
                    "country": "DE",
                    "posted_date": "2026-01-31",
                    "company": "example gmbh",
                },
                {"country": "DE", "posted_date": "2026-01-31"},
                {
                    "country": "FR",
                    "posted_date": "2026-01-31",
                    "company": "Example SAS",
                },
                {
                    "country": "DE",
                    "posted_date": "2026-01-30",
                    "company": "Old company",
                },
            ],
            "2026-01-31",
        )
        self.assertEqual([row["country"] for row in output], ["DE", "FR"])
        self.assertEqual(output[0]["companies_day"], 1)
        self.assertEqual(output[0]["companies_30d"], 1)
        self.assertEqual(output[0]["missing_company"], 1)

    def test_preserves_previous_daily_sketches_for_30_day_estimate(self) -> None:
        stage = HiringCompaniesStage(precision=8)
        stage.process_day(
            [
                {
                    "country": "DE",
                    "posted_date": "2026-01-30",
                    "company": "First GmbH",
                }
            ],
            "2026-01-30",
        )
        second = stage.process_day(
            [
                {
                    "country": "DE",
                    "posted_date": "2026-01-31",
                    "company": "Second GmbH",
                }
            ],
            "2026-01-31",
        )
        self.assertEqual(second[0]["companies_day"], 1)
        self.assertEqual(second[0]["companies_30d"], 2)

    def test_rejects_non_text_company_values(self) -> None:
        with self.assertRaises(ValueError):
            HiringCompaniesStage().process_day(
                [
                    {
                        "country": "DE",
                        "posted_date": "2026-01-31",
                        "company": 123,
                    }
                ],
                "2026-01-31",
            )


if __name__ == "__main__":
    unittest.main()
