"""Test S5 through the shared event-time runner and message contracts."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from ace import contracts
from ace.stage import Window, WindowedStage, run_flink_local, run_local

from ..stage import SupplyFrequency


def profile(
    country: str,
    day: str,
    skills: list[str],
    *,
    certifications: list[str] | None = None,
    role: str | None = None,
    unmapped: list[str] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": country,
        "profile_date": day,
        "skills": skills,
    }
    if certifications is not None:
        message["certifications"] = certifications
    if role is not None:
        message["ecsf_role"] = role
    if unmapped is not None:
        message["unmapped"] = unmapped
    return message


def snapshots(outputs):
    return {(message["country"], message["day"]): message for message in outputs}


def closed_window(day: str) -> Window:
    start = datetime.combine(
        date.fromisoformat(day), datetime.min.time(), timezone.utc
    )
    return Window(start=start, end=start + timedelta(days=1))


def test_uses_shared_stage_and_emits_contract_valid_skill_counts() -> None:
    stage = SupplyFrequency()
    records = [
        profile(
            "DE",
            "2026-01-01",
            ["SIEM", "Linux", "SIEM"],
            certifications=["CISSP"],
            role="Cybersecurity Implementer",
            unmapped=["Future Skill"],
        ),
        profile("DE", "2026-01-01", ["SIEM"]),
        profile("FR", "2026-01-01", ["Linux"]),
        profile("DE", "2026-01-02", ["Cloud Security"]),
    ]

    assert isinstance(stage, WindowedStage)
    result = run_local(stage, records)
    output = result.published("agg.supply_daily")
    days = snapshots(output)

    assert result.invalid == [] and result.late == []
    assert days["DE", "2026-01-01"]["counts"] == [
        {"kind": "skill", "item": "Linux", "count": 1},
        {"kind": "skill", "item": "SIEM", "count": 2},
    ]
    assert days["FR", "2026-01-01"]["counts"] == [
        {"kind": "skill", "item": "Linux", "count": 1}
    ]
    assert days["DE", "2026-01-02"]["counts"] == [
        {"kind": "skill", "item": "Cloud Security", "count": 1},
        {"kind": "skill", "item": "Linux", "count": 1},
        {"kind": "skill", "item": "SIEM", "count": 2},
    ]
    assert all(
        contracts.check("agg.supply_daily", message) is None
        for message in output
    )
    assert not any(
        item["item"] in {"CISSP", "Cybersecurity Implementer", "Future Skill"}
        for message in output
        for item in message["counts"]
    )


def test_365_day_window_includes_boundary_and_expires_older_profiles() -> None:
    result = run_local(
        SupplyFrequency(),
        [
            profile("DE", "2025-01-01", ["Expired"]),
            profile("DE", "2025-01-02", ["Boundary"]),
            profile("DE", "2026-01-01", ["Current"]),
        ],
    )

    current = snapshots(result.published("agg.supply_daily"))[
        "DE", "2026-01-01"
    ]
    assert current["counts"] == [
        {"kind": "skill", "item": "Boundary", "count": 1},
        {"kind": "skill", "item": "Current", "count": 1},
    ]


def test_emits_only_event_days_without_backfilling_gaps() -> None:
    result = run_local(
        SupplyFrequency(),
        [
            profile("DE", "2026-01-01", ["SIEM"]),
            profile("DE", "2026-01-03", ["Linux"]),
        ],
    )

    days = [
        message["day"] for message in result.published("agg.supply_daily")
    ]
    assert days == ["2026-01-01", "2026-01-03"]


def test_skips_stale_closed_window_without_changing_state(caplog) -> None:
    stage = SupplyFrequency()
    memory: dict = {}
    stage.result(
        "DE", closed_window("2026-01-02"), Counter({"SIEM": 2}), memory
    )
    counter = memory["supply_frequency"]
    before = (counter.last_day, counter.active_skills, counter.bucket_count)

    with caplog.at_level("WARNING", logger="streaming.s5_supply_frequency.stage"):
        skipped = stage.result(
            "DE",
            closed_window("2026-01-01"),
            Counter({"Old Skill": 4}),
            memory,
        )

    assert skipped == []
    assert (
        counter.last_day,
        counter.active_skills,
        counter.bucket_count,
    ) == before
    assert "Skipping stale S5 window" in caplog.text
    assert "country=DE" in caplog.text
    assert "day=2026-01-01" in caplog.text
    assert "last_day=2026-01-02" in caplog.text

    [third] = stage.result(
        "DE",
        closed_window("2026-01-03"),
        Counter({"Current Skill": 1}),
        memory,
    )
    assert third["counts"] == [
        {"kind": "skill", "item": "Current Skill", "count": 1},
        {"kind": "skill", "item": "SIEM", "count": 2},
    ]


def test_skips_duplicate_closed_window_without_double_counting(caplog) -> None:
    stage = SupplyFrequency()
    memory: dict = {}
    [first] = stage.result(
        "DE", closed_window("2026-01-01"), Counter({"SIEM": 1}), memory
    )

    with caplog.at_level("WARNING", logger="streaming.s5_supply_frequency.stage"):
        duplicate = stage.result(
            "DE",
            closed_window("2026-01-01"),
            Counter({"Duplicate Skill": 5}),
            memory,
        )

    assert duplicate == []
    assert "Skipping duplicate S5 window" in caplog.text
    [second] = stage.result(
        "DE", closed_window("2026-01-02"), Counter({"Linux": 1}), memory
    )
    assert first["counts"] == [
        {"kind": "skill", "item": "SIEM", "count": 1}
    ]
    assert second["counts"] == [
        {"kind": "skill", "item": "Linux", "count": 1},
        {"kind": "skill", "item": "SIEM", "count": 1},
    ]


def test_uses_gb_country_code_and_ignores_blank_skills() -> None:
    result = run_local(
        SupplyFrequency(),
        [profile("GB", "2026-01-01", ["Cloud Security", "", "   "])],
    )

    [output] = result.published("agg.supply_daily")
    assert output == {
        "country": "GB",
        "day": "2026-01-01",
        "counts": [
            {"kind": "skill", "item": "Cloud Security", "count": 1}
        ],
    }


def test_each_country_day_is_published_once() -> None:
    result = run_local(
        SupplyFrequency(),
        [
            profile("DE", "2026-01-01", ["SIEM"]),
            profile("DE", "2026-01-01", ["Linux"]),
            profile("FR", "2026-01-01", ["SIEM"]),
        ],
    )

    keys = [
        (message["country"], message["day"])
        for message in result.published("agg.supply_daily")
    ]
    assert len(keys) == len(set(keys)) == 2


def test_record_after_its_day_closed_is_late_and_excluded() -> None:
    late = profile("DE", "2026-01-01", ["Late Skill"])
    result = run_local(
        SupplyFrequency(),
        [profile("DE", "2026-01-03", ["Current"]), late],
    )

    assert result.late == [late]
    output = result.published("agg.supply_daily")
    assert [message["day"] for message in output] == ["2026-01-03"]
    assert output[0]["counts"] == [
        {"kind": "skill", "item": "Current", "count": 1}
    ]


def test_out_of_order_record_inside_grace_period_is_included() -> None:
    result = run_local(
        SupplyFrequency(),
        [
            profile("DE", "2026-01-02", ["New Day"]),
            profile("DE", "2026-01-01", ["Previous Day"]),
        ],
    )

    assert result.late == []
    days = snapshots(result.published("agg.supply_daily"))
    assert days["DE", "2026-01-01"]["counts"] == [
        {"kind": "skill", "item": "Previous Day", "count": 1}
    ]
    assert days["DE", "2026-01-02"]["counts"] == [
        {"kind": "skill", "item": "New Day", "count": 1},
        {"kind": "skill", "item": "Previous Day", "count": 1},
    ]


def test_invalid_input_routes_to_errors_invalid() -> None:
    bad = profile("Germany", "2026-01-01", ["SIEM"])
    result = run_local(SupplyFrequency(), [bad])

    assert result.published("agg.supply_daily") == []
    [invalid] = result.invalid
    assert invalid["topic"] == "skills.supply"
    assert invalid["producer"] == "s5-supply-frequency"


@pytest.mark.flink
def test_flink_matches_local_for_on_time_records() -> None:
    pytest.importorskip(
        "pyflink", reason="install the optional flink extra to run this test"
    )
    records = [
        profile("DE", "2026-01-01", ["SIEM", "Linux"]),
        profile("DE", "2026-01-02", ["SIEM"]),
        profile("FR", "2026-01-02", ["Cloud Security"]),
    ]

    flink = [
        outgoing.value
        for outgoing in run_flink_local(SupplyFrequency(), records)
        if outgoing.topic == "agg.supply_daily"
    ]
    local = run_local(SupplyFrequency(), records).published("agg.supply_daily")
    assert snapshots(flink) == snapshots(local)
