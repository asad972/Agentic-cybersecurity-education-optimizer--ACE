"""S6 joining, replay, checkpoint, and shared-contract tests."""

from __future__ import annotations

import pickle
from datetime import date, timedelta

import pytest

from ace import contracts
from ace.stage import RecordStage, run_flink_local, run_local

from ..detector import DriftConfig
from ..stage import DemandDrift

DEMAND = "agg.demand_daily"
TOP10 = "agg.top10_demand"
ALERTS = "alerts.drift"


def top10(
    day: str,
    *,
    country: str = "DE",
    kind: str = "skill",
    items: tuple[str, ...] = ("Cloud Security",),
) -> dict:
    return {
        "country": country,
        "day": day,
        "kind": kind,
        "items": [
            {"rank": rank, "item": item, "count": 20 - rank}
            for rank, item in enumerate(items, start=1)
        ],
    }


def demand(
    day: str,
    counts: dict[str, int] | None = None,
    *,
    country: str = "DE",
) -> dict:
    return {
        "country": country,
        "day": day,
        "job_ads": sum((counts or {}).values()),
        "counts": [
            {"kind": "skill", "item": skill, "count": count}
            for skill, count in sorted((counts or {}).items())
        ],
    }


def apply(stage: DemandDrift, memory: dict, records: list[tuple[str, dict]]):
    output = []
    for topic, record in records:
        output.extend(stage.process(topic, record, memory))
    return [message for topic, message in output if topic == ALERTS]


def state_view(memory: dict) -> dict:
    return {
        "eligibility": memory["eligibility_dates"],
        "eu_eligibility": memory["eu_eligibility_dates"],
        "pending": memory["pending_demand"],
        "last": memory["last_finalized_day"],
        "latest": memory["latest_demand_day"],
        "country_models": {
            key: (model.width, model.mean)
            for key, model in memory["country_detectors"].items()
        },
        "eu_models": {
            key: (model.width, model.mean)
            for key, model in memory["eu_detectors"].items()
        },
        "eu_values": memory["eu_country_values"],
        "counters": memory["replay_counters"],
    }


def test_uses_record_stage_and_one_global_checkpoint_key() -> None:
    stage = DemandDrift()

    assert isinstance(stage, RecordStage)
    assert stage.key(TOP10, top10("2026-01-01", country="DE")) == "s6-global"
    assert stage.key(TOP10, top10("2026-01-01", country="FR")) == "s6-global"
    assert stage.inputs == (DEMAND, TOP10)
    assert stage.output == ALERTS


def test_skill_top10_establishes_eligibility_but_certification_does_not() -> None:
    stage = DemandDrift()
    memory: dict = {}

    apply(
        stage,
        memory,
        [
            (TOP10, top10("2026-01-01", kind="certification", items=("CCSP",))),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 3})),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 4})),
        ],
    )
    assert memory["country_detectors"] == {}
    assert memory["eu_detectors"] == {}

    apply(stage, memory, [(TOP10, top10("2026-01-02"))])
    assert memory["eligibility_dates"] == {
        "DE": {"Cloud Security": "2026-01-02"}
    }


def test_s3_and_s4_arrival_order_within_buffer_is_equivalent() -> None:
    first_stage, second_stage = DemandDrift(), DemandDrift()
    first: dict = {}
    second: dict = {}

    first_output = apply(
        first_stage,
        first,
        [
            (TOP10, top10("2026-01-01")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 8})),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 8})),
        ],
    )
    second_output = apply(
        second_stage,
        second,
        [
            (DEMAND, demand("2026-01-01", {"Cloud Security": 8})),
            (TOP10, top10("2026-01-01")),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 8})),
        ],
    )

    assert first_output == second_output == []
    assert state_view(first) == state_view(second)


def test_missing_skill_is_zero_but_missing_country_is_not_an_observation() -> None:
    stage = DemandDrift()
    memory: dict = {}
    apply(
        stage,
        memory,
        [
            (TOP10, top10("2026-01-01", country="DE")),
            (TOP10, top10("2026-01-01", country="FR")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 5}, country="DE")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 2}, country="FR")),
            (DEMAND, demand("2026-01-02", {}, country="DE")),
            (DEMAND, demand("2026-01-03", {"Cloud Security": 1}, country="DE")),
        ],
    )

    de = memory["country_detectors"]["DE", "Cloud Security"]
    fr = memory["country_detectors"]["FR", "Cloud Security"]
    eu = memory["eu_detectors"]["Cloud Security"]
    assert (de.width, de.mean) == (2, 2.5)  # 5, then an explicit zero
    assert (fr.width, fr.mean) == (1, 2)  # no FR snapshot on the second day
    assert (eu.width, eu.mean) == (2, 4.5)  # EU values are 7, then DE=0 + carried FR=2
    assert memory["eu_country_values"] == {"Cloud Security": {"DE": 0, "FR": 2}}


def test_late_top10_begins_on_next_unfinalized_day() -> None:
    stage = DemandDrift()
    memory: dict = {}
    apply(
        stage,
        memory,
        [
            (DEMAND, demand("2026-01-01", {"Cloud Security": 10})),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 10})),
            (TOP10, top10("2026-01-01")),
            (DEMAND, demand("2026-01-03", {"Cloud Security": 10})),
        ],
    )

    assert memory["eligibility_dates"]["DE"]["Cloud Security"] == "2026-01-02"
    assert memory["country_detectors"]["DE", "Cloud Security"].width == 1
    assert memory["replay_counters"]["late_top10"] == 1


def test_stable_observations_do_not_emit_alerts() -> None:
    start = date(2026, 1, 1)
    records = [(TOP10, top10(start.isoformat()))]
    records.extend(
        (DEMAND, demand((start + timedelta(days=offset)).isoformat(), {"Cloud Security": 10}))
        for offset in range(51)
    )

    result = run_local(DemandDrift(), records)
    assert result.published(ALERTS) == []
    assert result.invalid == []


def test_upward_and_downward_shifts_emit_country_and_eu_contract_alerts() -> None:
    start = date(2026, 1, 1)
    values = [10] * 40 + [40] * 60 + [2] * 80
    records = [(TOP10, top10(start.isoformat()))]
    records.extend(
        (DEMAND, demand((start + timedelta(days=offset)).isoformat(), {"Cloud Security": value}))
        for offset, value in enumerate(values)
    )
    records.append(
        (DEMAND, demand((start + timedelta(days=len(values))).isoformat(), {"Cloud Security": 2}))
    )

    alerts = run_local(DemandDrift(DriftConfig(delta=0.01)), records).published(ALERTS)
    assert alerts
    assert {alert["country"] for alert in alerts} == {"DE", "EU"}
    for scope in ("DE", "EU"):
        details = " ".join(
            alert["details"] for alert in alerts if alert["country"] == scope
        )
        assert "direction=up" in details
        assert "direction=down" in details
    for alert in alerts:
        assert alert["created_at"] == f"{alert['day']}T00:00:00Z"
        assert alert["subject"] == "Cloud Security"
        assert contracts.check(ALERTS, alert) is None
        if alert["country"] == "EU":
            assert "scope=EU" in alert["details"]
            assert "observed_countries=1" in alert["details"]
            assert "carried_countries=0" in alert["details"]
            assert "contributing_countries=1" in alert["details"]


def test_eu_carries_missing_country_without_an_artificial_drop() -> None:
    class AlwaysChange:
        def __init__(self, config):
            self.width = 0
            self.mean = 0.0

        def update(self, value):
            from ..detector import DriftChange

            self.width += 1
            self.mean = float(value)
            return DriftChange(float(value), 0.0, float(value), self.width, "up", float(value))

    stage = DemandDrift()
    memory: dict = {}
    apply(
        stage,
        memory,
        [
            (TOP10, top10("2026-01-01", country="DE")),
            (TOP10, top10("2026-01-01", country="FR")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 5}, country="DE")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 7}, country="FR")),
        ],
    )
    # Substitute a deterministic detector only to inspect the exact finalized EU value.
    memory["country_detectors"] = {
        key: AlwaysChange(stage.config) for key in memory["country_detectors"]
    }
    memory["eu_detectors"]["Cloud Security"] = AlwaysChange(stage.config)
    first_alerts = apply(
        stage,
        memory,
        [(DEMAND, demand("2026-01-02", {"Cloud Security": 5}, country="DE"))],
    )

    [eu] = [alert for alert in first_alerts if alert["country"] == "EU"]
    assert "observation=12" in eu["details"]
    assert "observed_countries=2" in eu["details"]
    assert "carried_countries=0" in eu["details"]
    assert "contributing_countries=2" in eu["details"]

    second_alerts = apply(
        stage,
        memory,
        [(DEMAND, demand("2026-01-03", {"Cloud Security": 5}, country="DE"))],
    )
    [eu] = [alert for alert in second_alerts if alert["country"] == "EU"]
    assert "observation=12" in eu["details"]
    assert "observed_countries=1" in eu["details"]
    assert "carried_countries=1" in eu["details"]
    assert "contributing_countries=2" in eu["details"]
    assert memory["country_detectors"]["FR", "Cloud Security"].width == 1


def test_explicit_zero_replaces_carried_value_and_unseen_country_is_excluded() -> None:
    stage = DemandDrift()
    memory: dict = {}
    apply(
        stage,
        memory,
        [
            (TOP10, top10("2026-01-01", country="DE")),
            (TOP10, top10("2026-01-01", country="FR")),
            (TOP10, top10("2026-01-01", country="IT")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 5}, country="DE")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 7}, country="FR")),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 5}, country="DE")),
            (DEMAND, demand("2026-01-03", {}, country="FR")),
            (DEMAND, demand("2026-01-04", {"Cloud Security": 5}, country="DE")),
        ],
    )

    assert memory["eu_country_values"] == {"Cloud Security": {"DE": 5, "FR": 0}}
    assert "IT" not in memory["eu_country_values"]["Cloud Security"]


def test_replayed_and_conflicting_aggregates_do_not_retrain() -> None:
    stage = DemandDrift()
    memory: dict = {}
    apply(stage, memory, [(TOP10, top10("2026-01-01"))])
    first = demand("2026-01-01", {"Cloud Security": 5})
    apply(
        stage,
        memory,
        [
            (DEMAND, first),
            (DEMAND, first),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 99})),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 5})),
            (DEMAND, first),
        ],
    )

    detector = memory["country_detectors"]["DE", "Cloud Security"]
    assert (detector.width, detector.mean) == (1, 5)
    assert memory["replay_counters"] == {
        "duplicate": 1,
        "conflicting": 1,
        "already_finalized": 1,
        "late_top10": 0,
    }


def test_invalid_record_routes_to_errors_invalid() -> None:
    result = run_local(
        DemandDrift(),
        [(DEMAND, demand("2026-01-01", {"Cloud Security": 1}, country="Germany"))],
    )

    assert result.published(ALERTS) == []
    [invalid] = result.invalid
    assert invalid["topic"] == DEMAND
    assert invalid["producer"] == "s6-demand-drift"


def test_unexpected_known_topic_is_rejected_without_initializing_state() -> None:
    stage = DemandDrift()
    memory: dict = {}
    supply = demand("2026-01-01", {"Cloud Security": 1})
    supply.pop("job_ads")

    [(topic, invalid)] = stage.process("agg.supply_daily", supply, memory)
    assert topic == contracts.INVALID_TOPIC
    assert invalid["topic"] == "agg.supply_daily"
    assert memory == {}


def test_checkpoint_restores_models_buffer_and_signature() -> None:
    stage = DemandDrift()
    memory: dict = {}
    apply(
        stage,
        memory,
        [
            (TOP10, top10("2026-01-01")),
            (DEMAND, demand("2026-01-01", {"Cloud Security": 4})),
            (DEMAND, demand("2026-01-02", {"Cloud Security": 5})),
        ],
    )

    restored = pickle.loads(pickle.dumps(memory))
    assert restored["state_version"] == 2
    assert restored["river_version"] == "0.23.0"
    assert restored["config_signature"] == stage._config_signature
    assert state_view(restored) == state_view(memory)
    apply(
        stage,
        restored,
        [(DEMAND, demand("2026-01-03", {"Cloud Security": 6}))],
    )
    assert restored["country_detectors"]["DE", "Cloud Security"].width == 2

    with pytest.raises(RuntimeError, match="config_signature"):
        list(DemandDrift(DriftConfig(delta=0.01)).process(TOP10, top10("2026-01-04"), restored))

    restored["state_version"] = 1
    with pytest.raises(RuntimeError, match="state_version"):
        list(stage.process(TOP10, top10("2026-01-04"), restored))


def test_state_is_bounded_by_eligibility_and_one_pending_day() -> None:
    stage = DemandDrift()
    memory: dict = {}
    start = date(2026, 1, 1)
    apply(
        stage,
        memory,
        [(TOP10, top10(start.isoformat(), items=("Cloud Security", "Linux")))],
    )
    for offset in range(30):
        apply(
            stage,
            memory,
            [
                (
                    DEMAND,
                    demand(
                        (start + timedelta(days=offset)).isoformat(),
                        {"Cloud Security": 10, "Linux": 4},
                    ),
                )
            ],
        )
        assert len(memory["pending_demand"]) <= 1

    assert len(memory["country_detectors"]) == 2
    assert len(memory["eu_detectors"]) == 2
    assert len(memory["eu_country_values"]) == 2
    assert all(len(values) == 1 for values in memory["eu_country_values"].values())
    assert memory["last_finalized_day"] == "2026-01-29"
    assert list(memory["pending_demand"]) == ["2026-01-30"]


@pytest.mark.flink
def test_flink_matches_local_for_ordered_records() -> None:
    pytest.importorskip(
        "pyflink", reason="install the optional flink extra to run this test"
    )
    start = date(2026, 1, 1)
    values = [10] * 40 + [40] * 60
    records = [(TOP10, top10(start.isoformat()))]
    records.extend(
        (DEMAND, demand((start + timedelta(days=offset)).isoformat(), {"Cloud Security": value}))
        for offset, value in enumerate(values)
    )
    records.append(
        (DEMAND, demand((start + timedelta(days=len(values))).isoformat(), {"Cloud Security": 40}))
    )
    config = DriftConfig(delta=0.01)

    local = run_local(DemandDrift(config), records).published(ALERTS)
    flink = [
        outgoing.value
        for outgoing in run_flink_local(DemandDrift(config), records)
        if outgoing.topic == ALERTS
    ]
    order = lambda alert: (alert["day"], alert["country"], alert["subject"])
    assert sorted(flink, key=order) == sorted(local, key=order)
