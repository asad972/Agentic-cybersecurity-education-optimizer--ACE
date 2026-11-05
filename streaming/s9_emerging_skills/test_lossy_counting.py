"""Tests for Lossy Counting and the S9 emerging-skills aggregation."""

from datetime import date
import unittest

from lossy_counting import EmergingSkillsCounter, LossyCounting


class LossyCountingTests(unittest.TestCase):
    """Verify estimates, rolling-window behavior, and input validation."""

    def test_estimates_are_lower_bounds_with_a_bounded_error(self) -> None:
        """Returned counts never exceed the true count or the error bound."""
        counter = LossyCounting(epsilon=0.2)
        true_counts: dict[str, int] = {}
        for item in ["siem"] * 8 + ["soar"] * 4 + ["ueba"] * 2 + ["xdr"]:
            counter.add(item)
            true_counts[item] = true_counts.get(item, 0) + 1

        for item, estimate in counter.estimates().items():
            self.assertLessEqual(estimate.count, true_counts[item])
            self.assertLessEqual(
                true_counts[item] - estimate.count, estimate.max_error
            )
            self.assertLessEqual(
                estimate.max_error, int(counter.epsilon * counter.total)
            )

    def test_normalizes_mentions_and_rejects_empty_items(self) -> None:
        """Equivalent casing is grouped and blank mentions are invalid."""
        counter = LossyCounting(epsilon=0.1)
        counter.add("  SIEM  ")

        self.assertIn("siem", counter.estimates())
        with self.assertRaises(ValueError):
            counter.add("   ")

    def test_emits_separate_country_results_for_the_30_day_window(self) -> None:
        """The output uses a 30-day window without mixing countries."""
        counter = EmergingSkillsCounter(epsilon=0.1)
        counter.add("DE", "2026-01-02", "SIEM")
        counter.add("DE", "2026-01-31", "SOAR")
        counter.add("FR", "2026-01-31", "XDR")

        result = counter.emit("DE", "2026-01-31")

        self.assertEqual(result["country"], "DE")
        self.assertEqual(result["day"], "2026-01-31")
        self.assertEqual(
            result["items"],
            [
                {"mention": "siem", "count": 1, "max_error": 0},
                {"mention": "soar", "count": 1, "max_error": 0},
            ],
        )
        self.assertEqual(
            counter.emit("FR", "2026-01-31")["items"],
            [{"mention": "xdr", "count": 1, "max_error": 0}],
        )

    def test_expires_data_older_than_the_30_day_window(self) -> None:
        """Events before the first day in the window are not included."""
        counter = EmergingSkillsCounter(epsilon=0.1)
        counter.add("DE", date(2026, 1, 1), "expired")
        counter.add("DE", date(2026, 1, 2), "included")

        result = counter.emit("DE", date(2026, 1, 31))

        self.assertEqual(
            result["items"],
            [{"mention": "included", "count": 1, "max_error": 0}],
        )

    def test_limits_and_sorts_output_deterministically(self) -> None:
        """Only the requested number of candidates is returned, in order."""
        counter = EmergingSkillsCounter(epsilon=0.1, limit=2)
        for mention in ["xdr", "soar", "siem", "siem", "soar"]:
            counter.add("DE", "2026-01-31", mention)

        self.assertEqual(
            counter.emit("DE", "2026-01-31")["items"],
            [
                {"mention": "siem", "count": 2, "max_error": 0},
                {"mention": "soar", "count": 2, "max_error": 0},
            ],
        )

    def test_includes_error_bound_for_a_daily_summary_that_pruned_a_mention(self) -> None:
        """A missing daily estimate contributes to the candidate error bound."""
        counter = EmergingSkillsCounter(epsilon=0.5)
        counter.add("DE", "2026-01-30", "siem")
        counter.add("DE", "2026-01-30", "siem")
        counter.add("DE", "2026-01-31", "siem")
        counter.add("DE", "2026-01-31", "soar")

        result = counter.emit("DE", "2026-01-31")

        self.assertEqual(
            result["items"],
            [{"mention": "siem", "count": 2, "max_error": 1}],
        )

    def test_rejects_invalid_country_and_day(self) -> None:
        """Malformed countries and dates fail explicitly."""
        counter = EmergingSkillsCounter()
        with self.assertRaises(ValueError):
            counter.add("de", "2026-01-31", "siem")
        with self.assertRaises(ValueError):
            counter.add("DE", "not-a-date", "siem")
        with self.assertRaises(ValueError):
            counter.emit("DE", "not-a-date")


if __name__ == "__main__":
    unittest.main()