"""S6: detect country and EU Skill-demand drift with River ADWIN."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, timedelta
from typing import Any, Iterable

import river

from ace import contracts
from ace.stage import RecordStage

from .detector import ADWINSeries, DriftChange, DriftConfig

_STATE_VERSION = 1
_GLOBAL_KEY = "s6-global"
_COUNTERS = ("duplicate", "conflicting", "already_finalized", "late_top10")


class DemandDrift(RecordStage):
    """Join S3 eligibility with S4 observations and emit ADWIN alerts.

    All input records deliberately use one internal Flink key. That gives S6
    one checkpointed view of the country observations needed for an EU sum;
    emitted Kafka records are still keyed by their alert ``country`` field.
    """

    name = "s6-demand-drift"
    inputs = ("agg.demand_daily", "agg.top10_demand")
    output = "alerts.drift"

    def __init__(self, config: DriftConfig | None = None) -> None:
        self.config = config or DriftConfig()
        self._config_signature = tuple(
            (field.name, getattr(self.config, field.name))
            for field in fields(self.config)
        )

    def key(self, topic: str, record: dict[str, Any]) -> str:
        """Keep all countries together so an EU daily sum can be formed."""
        return _GLOBAL_KEY

    def process(
        self, topic: str, record: dict[str, Any], memory: dict
    ) -> Iterable[tuple[str, dict[str, Any]]]:
        """Apply one valid aggregate without revising finalized history."""
        if topic not in self.inputs:
            invalid = contracts.invalid_record(
                topic,
                self.name,
                f"unexpected input topic for {self.name}",
                record,
            )
            return [(contracts.INVALID_TOPIC, invalid)]

        self._ensure_state(memory)
        if topic == "agg.top10_demand":
            self._record_eligibility(record, memory)
            return []

        self._buffer_demand(record, memory)
        return [(self.output, alert) for alert in self._finalize_ready(memory)]

    def _ensure_state(self, memory: dict) -> None:
        expected = {
            "state_version": _STATE_VERSION,
            "river_version": river.__version__,
            "config_signature": self._config_signature,
        }
        if not memory:
            memory.update(
                **expected,
                eligibility_dates={},
                eu_eligibility_dates={},
                pending_demand={},
                country_detectors={},
                eu_detectors={},
                last_finalized_day=None,
                latest_demand_day=None,
                replay_counters={name: 0 for name in _COUNTERS},
            )
            return

        incompatible = [
            name for name, value in expected.items() if memory.get(name) != value
        ]
        if incompatible:
            joined = ", ".join(incompatible)
            raise RuntimeError(f"incompatible S6 checkpoint state: {joined}")

        required = {
            "eligibility_dates",
            "eu_eligibility_dates",
            "pending_demand",
            "country_detectors",
            "eu_detectors",
            "last_finalized_day",
            "latest_demand_day",
            "replay_counters",
        }
        missing = sorted(required.difference(memory))
        if missing:
            raise RuntimeError(
                "incompatible S6 checkpoint state: missing " + ", ".join(missing)
            )

    def _record_eligibility(self, record: dict[str, Any], memory: dict) -> None:
        if record["kind"] != "skill":
            return

        record_day = date.fromisoformat(record["day"])
        last_finalized = _optional_day(memory["last_finalized_day"])
        next_unfinalized = (
            last_finalized + timedelta(days=1) if last_finalized else record_day
        )
        eligible_day = max(record_day, next_unfinalized)
        if eligible_day != record_day:
            memory["replay_counters"]["late_top10"] += 1

        country = record["country"]
        regional = memory["eligibility_dates"].setdefault(country, {})
        eu = memory["eu_eligibility_dates"]
        for item in record["items"]:
            skill = item["item"]
            if not skill.strip():
                continue
            _keep_earliest(regional, skill, eligible_day)
            _keep_earliest(eu, skill, eligible_day)

    def _buffer_demand(self, record: dict[str, Any], memory: dict) -> None:
        observed_day = date.fromisoformat(record["day"])
        day_key = observed_day.isoformat()
        country = record["country"]
        last_finalized = _optional_day(memory["last_finalized_day"])

        if last_finalized is not None and observed_day <= last_finalized:
            memory["replay_counters"]["already_finalized"] += 1
            return

        skills: dict[str, int] = {}
        for count in record["counts"]:
            if count["kind"] == "skill" and count["item"].strip():
                skills[count["item"]] = skills.get(count["item"], 0) + count["count"]

        by_country = memory["pending_demand"].setdefault(day_key, {})
        if country in by_country:
            category = "duplicate" if by_country[country] == skills else "conflicting"
            memory["replay_counters"][category] += 1
            return
        by_country[country] = skills

        latest = _optional_day(memory["latest_demand_day"])
        if latest is None or observed_day > latest:
            memory["latest_demand_day"] = day_key

    def _finalize_ready(self, memory: dict) -> list[dict[str, Any]]:
        latest = _optional_day(memory["latest_demand_day"])
        if latest is None:
            return []
        cutoff = latest - timedelta(days=self.config.finalization_lag_days)
        ready = sorted(
            date.fromisoformat(day)
            for day in memory["pending_demand"]
            if date.fromisoformat(day) <= cutoff
        )

        alerts: list[dict[str, Any]] = []
        for day in ready:
            observations = memory["pending_demand"].pop(day.isoformat())
            alerts.extend(self._finalize_day(day, observations, memory))
            previous = _optional_day(memory["last_finalized_day"])
            if previous is None or day > previous:
                memory["last_finalized_day"] = day.isoformat()
        return alerts

    def _finalize_day(
        self,
        day: date,
        observations: dict[str, dict[str, int]],
        memory: dict,
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        country_detectors = memory["country_detectors"]

        for country in sorted(observations):
            counts = observations[country]
            eligibility = memory["eligibility_dates"].get(country, {})
            for skill in sorted(eligibility):
                if date.fromisoformat(eligibility[skill]) > day:
                    continue
                detector = country_detectors.setdefault(
                    (country, skill), ADWINSeries(self.config)
                )
                change = detector.update(counts.get(skill, 0))
                if change is not None:
                    alerts.append(
                        self._alert(country, day, skill, change, scope="country")
                    )

        coverage = len(observations)
        eu_detectors = memory["eu_detectors"]
        for skill in sorted(memory["eu_eligibility_dates"]):
            eligible_day = date.fromisoformat(memory["eu_eligibility_dates"][skill])
            if eligible_day > day:
                continue
            value = sum(counts.get(skill, 0) for counts in observations.values())
            detector = eu_detectors.setdefault(skill, ADWINSeries(self.config))
            change = detector.update(value)
            if change is not None:
                alerts.append(
                    self._alert(
                        "EU",
                        day,
                        skill,
                        change,
                        scope="EU",
                        coverage=coverage,
                    )
                )
        return alerts

    def _alert(
        self,
        country: str,
        day: date,
        skill: str,
        change: DriftChange,
        *,
        scope: str,
        coverage: int | None = None,
    ) -> dict[str, Any]:
        details = (
            f"direction={change.direction}; "
            f"observation={_number(change.observation)}; "
            f"previous_mean={_number(change.previous_mean)}; "
            f"new_mean={_number(change.new_mean)}; "
            f"width={change.width}; delta={_number(self.config.delta)}; "
            f"scope={scope}"
        )
        if coverage is not None:
            details += f"; contributing_countries={coverage}"
        return {
            "country": country,
            "day": day.isoformat(),
            "created_at": f"{day.isoformat()}T00:00:00Z",
            "alert_type": "drift",
            "subject": skill,
            "score": round(change.score, 6),
            "details": details,
        }


def _keep_earliest(target: dict[str, str], skill: str, eligible_day: date) -> None:
    existing = target.get(skill)
    if existing is None or eligible_day < date.fromisoformat(existing):
        target[skill] = eligible_day.isoformat()


def _optional_day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _number(value: int | float) -> str:
    return f"{value:.12g}"
