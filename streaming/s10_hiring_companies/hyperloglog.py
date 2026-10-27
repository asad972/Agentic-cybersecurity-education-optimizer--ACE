"""Mergeable HyperLogLog sketches and daily S10 company-count aggregation."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import blake2b
from math import log


class HyperLogLog:
    """Estimate distinct values with a compact, mergeable register array."""

    def __init__(self, precision: int = 12) -> None:
        if not 4 <= precision <= 16:
            raise ValueError("precision must be between 4 and 16")
        self.precision = precision
        # Precision p uses 2**p registers; higher precision improves accuracy.
        self._registers = [0] * (1 << precision)

    def add(self, value: str) -> None:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("value must not be empty")

        hashed = int.from_bytes(
            blake2b(normalized.encode("utf-8"), digest_size=8).digest(), "big"
        )
        # The leading p hash bits choose a register; trailing zeroes set its rank.
        remaining_bits = 64 - self.precision
        index = hashed >> remaining_bits
        remainder = hashed & ((1 << remaining_bits) - 1)
        rank = remaining_bits + 1 if remainder == 0 else (
            remaining_bits - remainder.bit_length() + 1
        )
        self._registers[index] = max(self._registers[index], rank)

    def merge(self, other: HyperLogLog) -> None:
        """Combine two sketches by keeping the larger rank in each register."""
        if self.precision != other.precision:
            raise ValueError("cannot merge sketches with different precision")
        self._registers = [
            max(left, right)
            for left, right in zip(self._registers, other._registers)
        ]

    def estimate(self) -> int:
        """Apply the HyperLogLog estimate and small-cardinality correction."""
        register_count = len(self._registers)
        alpha = 0.7213 / (1 + 1.079 / register_count)
        raw_estimate = alpha * register_count**2 / sum(
            2.0**-register for register in self._registers
        )
        zero_registers = self._registers.count(0)
        # Linear counting is more accurate while many registers are still empty.
        if raw_estimate <= 2.5 * register_count and zero_registers:
            estimate = register_count * log(register_count / zero_registers)
        else:
            estimate = raw_estimate
        return round(estimate)


class HiringCompaniesCounter:
    """Track daily company sketches and emit estimates for a 30-day window."""

    def __init__(self, precision: int = 12) -> None:
        if not 4 <= precision <= 16:
            raise ValueError("precision must be between 4 and 16")
        self.precision = precision
        self._daily: dict[str, dict[date, HyperLogLog]] = {}
        self._missing: dict[str, dict[date, int]] = {}

    def add(self, country: str, day: date | str, company: str | None) -> None:
        """Add one job ad, counting missing names separately from companies."""
        self._validate_country(country)
        event_day = self._parse_day(day)
        if company is None or not company.strip():
            missing = self._missing.setdefault(country, {})
            missing[event_day] = missing.get(event_day, 0) + 1
            return

        country_daily = self._daily.setdefault(country, {})
        sketch = country_daily.setdefault(
            event_day, HyperLogLog(precision=self.precision)
        )
        sketch.add(company)

    def emit(self, country: str, day: date | str) -> dict[str, object]:
        """Return the daily and rolling distinct-company estimates."""
        self._validate_country(country)
        end = self._parse_day(day)
        start = end - timedelta(days=29)
        daily = self._daily.get(country, {})
        missing_daily = self._missing.get(country, {})
        self._expire_before(daily, start)
        self._expire_before(missing_daily, start)

        daily_sketch = daily.get(end)
        # HyperLogLog sketches merge register-by-register across the window.
        merged = HyperLogLog(precision=self.precision)
        for event_day, sketch in daily.items():
            if start <= event_day <= end:
                merged.merge(sketch)

        return {
            "country": country,
            "day": end.isoformat(),
            "companies_day": daily_sketch.estimate() if daily_sketch else 0,
            "companies_30d": merged.estimate(),
            "missing_company": missing_daily.get(end, 0),
        }

    @staticmethod
    def _validate_country(country: str) -> None:
        """Require the uppercase ISO alpha-2 format used by the topic key."""
        if (
            len(country) != 2
            or not country.isalpha()
            or country != country.upper()
        ):
            raise ValueError("country must be an uppercase ISO alpha-2 code")

    @staticmethod
    def _parse_day(day: date | str) -> date:
        """Accept a date object or an ISO-formatted date string."""
        if isinstance(day, date):
            return day
        try:
            return date.fromisoformat(day)
        except ValueError as error:
            raise ValueError("day must be an ISO 8601 date") from error

    @staticmethod
    def _expire_before(daily: dict[date, object], start: date) -> None:
        """Remove state that can no longer contribute to a 30-day result."""
        for event_day in [event_day for event_day in daily if event_day < start]:
            del daily[event_day]
