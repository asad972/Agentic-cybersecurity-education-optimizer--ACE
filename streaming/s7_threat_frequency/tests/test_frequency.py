"""S7 regression tests using fake threats and the unchanged shared runtime."""

import json
import pickle
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from ace import contracts
from ace.stage import WindowedStage, run_flink_local, run_local
from s7_threat_frequency import ThreatFrequencyStage
from s7_threat_frequency.__main__ import main
from s7_threat_frequency.job import run_s7_flink_local, run_s7_local


def event(timestamp, country="HU", kind="malware", identity=None):
    return {
        "event_id": str(uuid5(NAMESPACE_URL, identity or timestamp + country + kind)),
        "source": "fixture", "country": country,
        "observed_at": timestamp, "threat_type": kind,
    }


def rows(result, period):
    branch = getattr(result, period)
    return branch.published(contracts.topic(f"agg.threats_{period}").name)


@pytest.mark.parametrize("period,hours", [("hourly", 1), ("daily", 24)])
def test_stage_uses_current_shared_contract(period, hours):
    stage = ThreatFrequencyStage(period)
    assert isinstance(stage, WindowedStage)
    assert stage.window == timedelta(hours=hours)
    assert stage.grace == timedelta(minutes=15)
    assert stage.time_field == "observed_at"
    assert stage.input == contracts.topic("threats.events").name


def test_unsupported_period_is_rejected():
    with pytest.raises(ValueError, match="period"):
        ThreatFrequencyStage("weekly")


def test_counts_by_country_type_hour_and_day_and_country_keys():
    result = run_s7_local([
        event("2026-11-01T00:00:00Z"),
        event("2026-11-01T00:10:00Z", kind="phishing"),
        event("2026-11-01T00:20:00Z", "DE", "scanning"),
        event("2026-11-01T01:00:00Z"),
        event("2026-11-02T00:00:00Z", kind="ddos"),
    ])
    hourly = {(row["country"], row["day"], row["hour"]): row["counts"] for row in rows(result, "hourly")}
    assert hourly == {
        ("HU", "2026-11-01", 0): {"malware": 1, "phishing": 1},
        ("DE", "2026-11-01", 0): {"scanning": 1},
        ("HU", "2026-11-01", 1): {"malware": 1},
        ("HU", "2026-11-02", 0): {"ddos": 1},
    }
    daily = {(row["country"], row["day"]): row["counts"] for row in rows(result, "daily")}
    assert daily == {
        ("HU", "2026-11-01"): {"malware": 2, "phishing": 1},
        ("DE", "2026-11-01"): {"scanning": 1},
        ("HU", "2026-11-02"): {"ddos": 1},
    }
    assert all("hour" not in row for row in rows(result, "daily"))
    for out in result.outputs:
        contracts.validate(out.topic, out.value)
        assert out.key == out.value["country"]


def test_exact_hour_grace_boundary_and_independent_daily_lateness():
    late = event("2026-11-01T00:03:00Z")
    result = run_s7_local([
        event("2026-11-01T00:01:00Z"),
        event("2026-11-01T01:14:59.999Z"),
        event("2026-11-01T00:02:00Z"),  # 1 ms before hour closes
        event("2026-11-01T01:15:00Z"),
        late,  # hour closed, day still open
    ])
    assert rows(result, "hourly")[0]["counts"] == {"malware": 2}
    assert rows(result, "daily")[0]["counts"] == {"malware": 5}
    assert result.hourly.late == [late]
    assert result.daily.late == []


def test_exact_day_grace_boundary_and_closed_windows_do_not_reopen():
    late = event("2026-11-01T23:59:00Z")
    result = run_s7_local([
        event("2026-11-01T23:01:00Z"),
        event("2026-11-02T00:14:59.999Z"),
        event("2026-11-01T23:02:00Z"),
        event("2026-11-02T00:15:00Z"), late, late,
    ])
    assert result.hourly.late == [late, late]
    assert result.daily.late == [late, late]
    assert rows(result, "daily")[0]["counts"] == {"malware": 2}
    for period in ("hourly", "daily"):
        keys = [(row["country"], row["day"], row.get("hour")) for row in rows(result, period)]
        assert len(keys) == len(set(keys))


def test_watermark_is_shared_across_countries_as_in_ace_stage():
    late_hu = event("2026-11-01T00:10:00Z", "HU")
    result = run_s7_local([
        event("2026-11-01T00:00:00Z", "HU"),
        event("2026-11-03T00:00:00Z", "DE"), late_hu,
    ])
    assert result.hourly.late == result.daily.late == [late_hu]
    assert rows(result, "daily")[0]["counts"] == {"malware": 1}


def test_duplicates_and_timezone_normalization():
    record = event("2026-11-02T00:10:00+02:00")
    result = run_s7_local([record, record, {**record, "observed_at": "2026-11-01T22:10:00Z"}])
    assert rows(result, "hourly") == [{"country": "HU", "day": "2026-11-01", "hour": 22, "counts": {"malware": 1}}]
    assert rows(result, "daily") == [{"country": "HU", "day": "2026-11-01", "counts": {"malware": 1}}]


def test_duplicate_after_hour_closes_is_not_counted_again_in_open_day():
    record = event("2026-11-01T00:00:00Z")
    result = run_s7_local([record, event("2026-11-01T01:15:00Z"), record])
    assert result.hourly.late == [record]
    assert result.daily.late == []
    assert rows(result, "daily")[0]["counts"] == {"malware": 2}


@pytest.mark.parametrize("bad", [{}, event("2026-11-01T00:00:00Z", "Germany"), "not JSON", None])
def test_invalid_input_is_routed_once_without_changing_windows(bad):
    good = event("2026-11-01T00:00:00Z")
    result = run_s7_local([bad, good])
    errors = [out for out in result.outputs if out.topic == contracts.INVALID_TOPIC]
    assert len(errors) == 1
    assert errors[0].key == "threats.events"
    contracts.validate(errors[0].topic, errors[0].value)
    assert rows(result, "hourly")[0]["counts"] == {"malware": 1}


def test_empty_input_produces_no_zero_observation_rows():
    assert run_s7_local([]).outputs == []


def test_accumulator_survives_checkpoint_serialization():
    stage = ThreatFrequencyStage()
    record = event("2026-11-01T00:00:00Z")
    state = stage.add(stage.new(), record)
    restored = pickle.loads(pickle.dumps(state))
    restored = stage.add(restored, record)
    restored = stage.add(restored, event("2026-11-01T00:01:00Z", kind="phishing"))
    assert restored["counts"] == {"malware": 1, "phishing": 1}
    assert len(restored["seen"]) == 2
    assert stage.new()["seen"] == set()


def test_daily_output_errors_are_not_hidden(monkeypatch):
    original = ThreatFrequencyStage.result

    def invalid_daily(self, *args):
        return [{"bad": True}] if self.period == "daily" else original(self, *args)

    monkeypatch.setattr(ThreatFrequencyStage, "result", invalid_daily)
    result = run_s7_local([event("2026-11-01T00:00:00Z")])
    [error] = [out.value for out in result.outputs if out.topic == contracts.INVALID_TOPIC]
    assert error["topic"] == "agg.threats_daily"


def test_cli_writes_both_aggregates_and_invalid_json(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps(event("2026-11-01T00:00:00Z")) + "\n{bad json\n\n", encoding="utf-8")
    target = tmp_path / "output"
    main(["--input", str(source), "--output-dir", str(target)])
    for name in ("hourly", "daily", "invalid"):
        lines = (target / f"{name}.jsonl").read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        if name == "invalid":
            assert row["topic"] == "threats.events"
        else:
            assert row["counts"] == {"malware": 1}


@pytest.mark.flink
def test_combined_flink_graph_matches_local_and_routes_invalid_once():
    # On-time records: Flink advances watermarks periodically, unlike run_local.
    records = [
        event("2026-11-01T00:00:00Z"), event("2026-11-01T00:00:00Z"),
        event("2026-11-01T00:10:00Z", kind="phishing"),
        event("2026-11-01T00:20:00Z", "DE", "scanning"),
        event("2026-11-01T01:00:00Z"), event("2026-11-02T00:00:00Z"), {},
    ]
    actual = run_s7_flink_local(records).outputs
    expected = run_s7_local(records).outputs

    def normalized(outputs):
        return sorted((out.topic, out.key, json.dumps(
            {k: v for k, v in out.value.items() if k != "detected_at"}, sort_keys=True,
        )) for out in outputs)

    assert normalized(actual) == normalized(expected)
    assert sum(out.topic == contracts.INVALID_TOPIC for out in actual) == 1


@pytest.mark.flink
@pytest.mark.parametrize("period", ["hourly", "daily"])
def test_branch_uses_shared_flink_runner(period):
    records = [event("2026-11-01T00:00:00Z", kind="malware-árvíz"),
               event("2026-11-01T00:01:00Z", "DE")]
    stage = ThreatFrequencyStage(period)
    assert sorted((o.key, o.value["counts"]) for o in run_flink_local(stage, records)) == sorted(
        (o.key, o.value["counts"]) for o in run_local(stage, records).outputs)


def test_worker_import_root_excludes_repository_secrets_and_dependencies():
    from s7_threat_frequency.job import _stage_import_root
    root = _stage_import_root()
    assert root.name == "streaming"
    assert (root / "s7_threat_frequency" / "stage.py").is_file()
    assert not any((root / name).exists() for name in (".env", ".venv", ".git", "dashboard"))


@pytest.mark.parametrize("engine", ["local", "flink"])
def test_cli_reports_actual_runner_lateness_consistently(tmp_path, monkeypatch, capsys, engine):
    from s7_threat_frequency import __main__ as cli
    records = [event("2026-11-01T00:00:00Z"), event("2026-11-01T01:15:00Z"),
               event("2026-11-01T00:01:00Z")]
    result = run_s7_local(records)
    monkeypatch.setattr(cli, "run_s7_flink_local", lambda _: result)
    source = tmp_path / "events.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in records))
    cli.main(["--input", str(source), "--output-dir", str(tmp_path / "out"), "--engine", engine])
    assert capsys.readouterr().out.splitlines()[0] == '{"late_hourly": 1, "late_daily": 0}'


@pytest.mark.flink
def test_late_side_outputs_are_collected_without_print_sinks():
    from ace.stage import flink_env
    from pyflink.common import Types
    from s7_threat_frequency.job import _collect_fixture_streams
    env = flink_env()
    record = event("2026-11-01T00:00:00Z")
    pair = Types.TUPLE([Types.STRING(), Types.STRING()])
    late = env.from_collection([("threats.events", json.dumps(record))], type_info=pair)
    # Production late streams carry decoded records after shared validation.
    late = late.map(lambda item: (item[0], json.loads(item[1])))
    output = env.from_collection([], type_info=Types.TUPLE([Types.STRING()] * 3))
    result = list(_collect_fixture_streams(output, late, late))
    assert sorted(row[0] for row in result) == ["daily", "hourly"]
    assert all(json.loads(row[3]) == record for row in result)
