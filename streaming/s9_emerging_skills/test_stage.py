"""Tests for mapping skills.demand messages into S9 daily output."""

import unittest

from stage import EmergingSkillsStage


class EmergingSkillsStageTests(unittest.TestCase):
    """Check S9 message mapping and stateful rolling-window aggregation."""

    def test_aggregates_only_target_day_unmapped_mentions(self) -> None:
        stage = EmergingSkillsStage()
        output = stage.process_day(
            [
                {
                    "country": "DE",
                    "posted_date": "2026-01-31",
                    "unmapped": ["Zero Trust", "zero trust"],
                },
                {
                    "country": "FR",
                    "posted_date": "2026-01-31",
                    "unmapped": ["Cloud Security"],
                },
                {
                    "country": "DE",
                    "posted_date": "2026-01-30",
                    "unmapped": ["Not included"],
                },
            ],
            "2026-01-31",
        )
        self.assertEqual([row["country"] for row in output], ["DE", "FR"])
        self.assertEqual(
            output[0]["items"],
            [{"mention": "zero trust", "count": 2, "max_error": 0}],
        )

    def test_preserves_previous_days_for_rolling_30_day_count(self) -> None:
        stage = EmergingSkillsStage()
        first = stage.process_day(
            [{"country": "DE", "posted_date": "2026-01-30", "unmapped": ["SIEM"]}],
            "2026-01-30",
        )
        second = stage.process_day(
            [{"country": "DE", "posted_date": "2026-01-31", "unmapped": ["SIEM"]}],
            "2026-01-31",
        )
        self.assertEqual(first[0]["items"][0]["count"], 1)
        self.assertEqual(second[0]["items"][0]["count"], 2)

    def test_rejects_invalid_unmapped_payload(self) -> None:
        with self.assertRaises(ValueError):
            EmergingSkillsStage().process_day(
                [{"country": "DE", "posted_date": "2026-01-31", "unmapped": "SIEM"}],
                "2026-01-31",
            )


if __name__ == "__main__":
    unittest.main()
