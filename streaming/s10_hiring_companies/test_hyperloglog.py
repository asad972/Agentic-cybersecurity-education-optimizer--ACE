"""Tests for HyperLogLog and the S10 hiring-companies counter."""

from datetime import date
import unittest

from hyperloglog import HiringCompaniesCounter, HyperLogLog


class HyperLogLogTests(unittest.TestCase):
    """Check approximation, merge behavior, windowing, and input validation."""

    def test_estimate_is_zero_for_an_empty_sketch(self) -> None:
        """An empty sketch should report no distinct companies."""
        self.assertEqual(HyperLogLog(precision=8).estimate(), 0)

    def test_estimate_tracks_cardinality_within_expected_error(self) -> None:
        """The estimate stays close to the known number of unique values."""
        sketch = HyperLogLog(precision=12)
        for number in range(10_000):
            sketch.add(f"company-{number}")

        estimate = sketch.estimate()
        self.assertLess(abs(estimate - 10_000), 600)

    def test_normalizes_company_names_and_rejects_empty_values(self) -> None:
        """Company-name case and surrounding spaces do not create duplicates."""
        sketch = HyperLogLog(precision=8)
        sketch.add("  Example Corp ")
        sketch.add("example corp")

        self.assertEqual(sketch.estimate(), 1)
        with self.assertRaises(ValueError):
            sketch.add(" ")

    def test_merge_matches_a_sketch_of_the_combined_values(self) -> None:
        """Merging sketches gives the same result as sketching all values."""
        left = HyperLogLog(precision=10)
        right = HyperLogLog(precision=10)
        combined = HyperLogLog(precision=10)
        for number in range(500):
            value = f"company-{number}"
            (left if number < 250 else right).add(value)
            combined.add(value)

        left.merge(right)

        self.assertEqual(left.estimate(), combined.estimate())
        with self.assertRaises(ValueError):
            left.merge(HyperLogLog(precision=9))

    def test_emits_daily_and_30_day_estimates_and_missing_count(self) -> None:
        """The output counts unique companies and missing names per day."""
        counter = HiringCompaniesCounter(precision=10)
        counter.add("DE", "2026-01-02", "Company A")
        counter.add("DE", "2026-01-02", "Company B")
        counter.add("DE", "2026-01-31", "Company B")
        counter.add("DE", "2026-01-31", "Company C")
        counter.add("DE", "2026-01-31", None)
        counter.add("DE", "2026-01-31", "")
        counter.add("FR", "2026-01-31", "Company D")

        result = counter.emit("DE", "2026-01-31")

        self.assertEqual(
            result,
            {
                "country": "DE",
                "day": "2026-01-31",
                "companies_day": 2,
                "companies_30d": 3,
                "missing_company": 2,
            },
        )
        self.assertEqual(
            counter.emit("FR", "2026-01-31")["companies_30d"], 1
        )

    def test_expires_sketches_outside_the_30_day_window(self) -> None:
        """Companies from outside the rolling window are excluded."""
        counter = HiringCompaniesCounter(precision=10)
        counter.add("DE", date(2026, 1, 1), "Expired Inc")
        counter.add("DE", date(2026, 1, 2), "Included Inc")

        self.assertEqual(
            counter.emit("DE", date(2026, 1, 31))["companies_30d"], 1
        )

    def test_rejects_invalid_country_and_day(self) -> None:
        """Invalid keys and dates raise errors instead of being silently used."""
        counter = HiringCompaniesCounter()
        with self.assertRaises(ValueError):
            counter.add("de", "2026-01-31", "Company")
        with self.assertRaises(ValueError):
            counter.add("DE", "not-a-date", "Company")
        with self.assertRaises(ValueError):
            counter.emit("DE", "not-a-date")

    def test_rejects_invalid_precision(self) -> None:
        """Precision is limited to the supported HyperLogLog range."""
        with self.assertRaises(ValueError):
            HyperLogLog(precision=3)


if __name__ == "__main__":
    unittest.main()
