"""S8 with real River models and the team's current ace runners."""

import json
import pickle
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from ace import contracts
from ace.stage import RecordStage, run_flink_local, run_local
from s8_threat_bursts import BurstConfig, ThreatBurstStage
from s8_threat_bursts.__main__ import main
from s8_threat_bursts.fixtures import hourly_fixture
from s8_threat_bursts.stage import TYPES


def hourly(index, country="HU", counts=None):
    moment = datetime(2026, 10, 12, tzinfo=timezone.utc) + timedelta(hours=index)
    return {"country": country, "day": moment.date().isoformat(), "hour": moment.hour,
            "counts": {"malware": 3} if counts is None else counts}


def small_config(**changes):
    return BurstConfig(**{**dict(window_size=4, warmup_hours=4, n_trees=3, height=3,
                                threshold=0), **changes})


def advance(stage, memory, records):
    """Continue one Country's keyed state, routing all emitted rows via ace."""
    outputs = []
    for record in records:
        for topic, value in stage.process(stage.inputs[0], record, memory):
            outputs.append(contracts.route(topic, value, producer=stage.name))
    return outputs


def test_uses_current_shared_stage_and_country_keys():
    stage = ThreatBurstStage()
    assert isinstance(stage, RecordStage)
    assert stage.inputs == (contracts.topic("agg.threats_hourly").name,)
    assert stage.output == contracts.topic("alerts.threats").name
    assert stage.key(stage.inputs[0], hourly(0)) == "HU"
    assert stage.key(stage.inputs[0], hourly(0, "DE")) == "DE"
    assert not any(name in vars(stage) for name in ("models", "history", "baselines"))


@pytest.mark.parametrize("change", [
    {"threshold": -0.1}, {"threshold": 1.1}, {"threshold": float("nan")},
    {"window_size": 1}, {"warmup_hours": 47}, {"n_trees": 0}, {"height": 13},
    {"max_feature_count": 0}, {"upward_ratio": 1}, {"upward_ratio": float("inf")},
    {"min_total": 0}, {"baseline_alpha": 0}, {"baseline_alpha": float("nan")},
    {"seed": 1.5}, {"height": True},
    {"cooldown_hours": -1}, {"cooldown_hours": True},
])
def test_rejects_invalid_configuration(change):
    with pytest.raises(ValueError):
        BurstConfig(**change)


def test_features_have_fixed_bounds_and_unknown_types_share_other():
    stage = ThreatBurstStage()
    row = hourly(23, counts={"malware": 10**1000, "unknown-A": 3, "unknown-B": 4, "other": 2})
    features = stage.features(row)
    assert set(features) == {*TYPES, "hour"}
    assert all(0 <= value <= 1 for value in features.values())
    assert features["hour"] == features["malware"] == 1
    assert stage.grouped_counts(row)["other"] == 9


def test_default_fixture_finds_only_the_known_country_spike():
    outputs = run_local(ThreatBurstStage(), hourly_fixture()).outputs
    assert len(outputs) == 1
    [out] = outputs
    contracts.validate(out.topic, out.value)
    assert out.topic == "alerts.threats" and out.key == "HU"
    assert (out.value["created_at"], out.value["subject"]) == ("2026-10-17T12:00:00Z", "phishing")
    assert 0.5 <= out.value["score"] <= 1


def test_warmup_upward_gate_and_country_isolation():
    records = [hourly(i) for i in range(4)]
    records += [hourly(4, "DE", {"phishing": 100}), hourly(4, counts={"phishing": 100})]
    [alert] = run_local(ThreatBurstStage(small_config()), records).outputs
    assert alert.key == "HU"  # DE has no history, even though HU is warmed up
    for counts in ({"malware": 0}, {}, {"malware": 10}, {"malware": 14}):
        assert run_local(ThreatBurstStage(small_config()),
                         [hourly(i) for i in range(4)] + [hourly(4, counts=counts)]).outputs == []
    assert run_local(ThreatBurstStage(small_config()),
                     [hourly(i, counts={"malware": 100}) for i in range(4)]
                     + [hourly(4, counts={"malware": 200})]).outputs == []


def test_threshold_still_gates_an_upward_spike():
    records = [hourly(i) for i in range(4)] + [hourly(4, counts={"phishing": 100})]
    assert run_local(ThreatBurstStage(small_config(threshold=1)), records).outputs == []


def test_scores_before_learning_current_observation():
    class Model:
        def score_one(self, features):
            calls.append("score")
            return 0.99

        def learn_one(self, features):
            calls.append("learn")

    stage, memory, calls = ThreatBurstStage(small_config()), {}, []
    advance(stage, memory, [hourly(i) for i in range(4)])
    memory["model"] = Model()
    [alert] = advance(stage, memory, [hourly(4, counts={"malware": 100})])
    assert calls == ["score", "learn"]
    assert alert.value["score"] == 0.99
    assert "prior EWMA=3.00" in alert.value["details"]


def test_duplicate_and_older_hours_do_not_change_model_or_baseline():
    stage, memory = ThreatBurstStage(small_config()), {}
    records = [hourly(i) for i in range(4)] + [hourly(4, counts={"phishing": 100})]
    assert len(advance(stage, memory, records)) == 1
    before = pickle.dumps((memory["model"], memory["baseline"], memory["samples"], memory["last_hour"]))
    assert advance(stage, memory, [records[-1], hourly(4, counts={"phishing": 999}), records[0]]) == []
    after = pickle.dumps((memory["model"], memory["baseline"], memory["samples"], memory["last_hour"]))
    assert before == after
    assert memory["duplicate_records"] == 2 and memory["out_of_order_records"] == 1


def test_missing_hours_are_zero_filled_and_midnight_orders_correctly():
    stage, memory = ThreatBurstStage(small_config()), {}
    assert advance(stage, memory, [hourly(23), hourly(24), hourly(48)]) == []
    assert memory["samples"] == 26
    assert memory["last_hour"] == int(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp() // 3600)


def test_sparse_input_matches_explicit_zero_hours_and_warms_up_in_clock_hours():
    stage, sparse, dense = ThreatBurstStage(small_config()), {}, {}
    end = 24 * 14
    spike = hourly(end, counts={"phishing": 100})
    actual = advance(stage, sparse, [hourly(0), spike])
    expected = advance(stage, dense, [hourly(0)] + [hourly(i, counts={}) for i in range(1, end)] + [spike])
    assert actual == expected and len(actual) == 1
    assert sparse["samples"] == dense["samples"] == end + 1
    assert sparse["baseline"] == dense["baseline"]
    assert pickle.dumps(sparse["model"]) == pickle.dumps(dense["model"])
    assert "prior EWMA=0.00" in actual[0].value["details"]


@pytest.mark.parametrize("change", [
    {"threshold": 1.0}, {"upward_ratio": 100.0}, {"min_total": 1000},
    {"baseline_alpha": 1.0}, {"warmup_hours": 10}, {"cooldown_hours": 12},
])
def test_alert_policy_can_change_after_checkpoint_restore(change):
    memory = {}
    advance(ThreatBurstStage(small_config()), memory, [hourly(i) for i in range(4)])
    restored = pickle.loads(pickle.dumps(memory))
    stage = ThreatBurstStage(small_config(**change))
    outputs = advance(stage, restored, [hourly(4, counts={"phishing": 100})])
    if any(name in change for name in ("threshold", "upward_ratio", "min_total", "warmup_hours")):
        assert outputs == []
    else:
        assert len(outputs) == 1
    if "baseline_alpha" in change:
        assert restored["baseline"]["phishing"] == 100
    assert restored["samples"] == 5


@pytest.mark.parametrize("change", [
    {"n_trees": 4}, {"height": 4}, {"window_size": 3},
    {"max_feature_count": 500}, {"seed": 99},
])
def test_model_shape_changes_still_require_migration(change):
    memory = {}
    advance(ThreatBurstStage(small_config()), memory, [hourly(0)])
    with pytest.raises(ValueError, match="migration"):
        advance(ThreatBurstStage(small_config(**change)), memory, [hourly(1)])


def test_original_checkpoint_upgrades_with_changed_alert_policy():
    config, memory = small_config(), {}
    advance(ThreatBurstStage(config), memory, [hourly(i) for i in range(4)])
    memory["signature"].pop("model_config")
    memory["signature"].update(state_version=1, config=asdict(config))
    memory["signature"]["config"].pop("cooldown_hours")
    memory.pop("last_alerts")
    memory["last_hour"] = "2026-10-12T03:00:00Z"
    restored = pickle.loads(pickle.dumps(memory))
    assert advance(ThreatBurstStage(small_config(threshold=1)), restored,
                   [hourly(4, counts={"phishing": 100})]) == []
    assert restored["signature"]["state_version"] == 2
    assert isinstance(restored["last_hour"], int)


def test_long_burst_cooldown_survives_restore_and_expires():
    stage, memory = ThreatBurstStage(small_config(baseline_alpha=0.001)), {}
    advance(stage, memory, [hourly(i) for i in range(4)])
    [first] = advance(stage, memory, [hourly(4, counts={"ddos": 100})])
    restored = pickle.loads(pickle.dumps(memory))
    assert advance(stage, restored, [hourly(i, counts={"ddos": 100}) for i in range(5, 10)]) == []
    [next_alert] = advance(stage, restored, [hourly(10, counts={"ddos": 100})])
    assert first.value["subject"] == next_alert.value["subject"] == "ddos"
    assert next_alert.value["created_at"] == "2026-10-12T10:00:00Z"


def test_cooldown_is_per_country_and_subject_and_can_be_disabled():
    rows = [hourly(i, country) for i in range(4) for country in ("HU", "DE")]
    rows += [hourly(4, country, {"ddos": 100}) for country in ("HU", "DE")]
    rows += [hourly(5, counts={"phishing": 200}), hourly(6, counts={"phishing": 200})]
    active = run_local(ThreatBurstStage(small_config()), rows).outputs
    disabled = run_local(ThreatBurstStage(small_config(cooldown_hours=0)), rows).outputs
    assert [(out.key, out.value["subject"]) for out in active] == [
        ("HU", "ddos"), ("DE", "ddos"), ("HU", "phishing")]
    assert len(disabled) == 4


@pytest.mark.parametrize("label", ["c2", "botnet"])
def test_unknown_type_keeps_source_label_in_alert(label):
    rows = [hourly(i) for i in range(4)] + [hourly(4, counts={label: 100, "other": 2})]
    [alert] = run_local(ThreatBurstStage(small_config()), rows).outputs
    assert alert.value["subject"] == label
    contracts.validate(alert.topic, alert.value)


def test_duplicate_and_late_drops_are_visible_in_logs(caplog):
    stage, memory = ThreatBurstStage(small_config()), {}
    advance(stage, memory, [hourly(2), hourly(2), hourly(0)])
    assert "late_records_total" in caplog.text
    assert "country=HU reason=duplicate_records" in caplog.text
    assert "country=HU reason=out_of_order_records" in caplog.text


def test_pickle_restore_preserves_model_and_subsequent_alerts():
    stage, memory = ThreatBurstStage(small_config()), {}
    advance(stage, memory, [hourly(i) for i in range(6)])
    restored = pickle.loads(pickle.dumps(memory))
    tail = [hourly(6, counts={"phishing": 100}), hourly(7), hourly(8), hourly(9, counts={"malware": 500})]
    assert advance(stage, memory, tail) == advance(ThreatBurstStage(small_config()), restored, tail)
    assert memory["samples"] == restored["samples"] == 10
    assert memory["baseline"] == restored["baseline"]
    assert memory["model"].score_one(stage.features(hourly(10))) == restored["model"].score_one(stage.features(hourly(10)))


@pytest.mark.parametrize("mismatch", ["model_config", "river_version", "state_version", "features"])
def test_incompatible_checkpoint_requires_explicit_migration(mismatch):
    stage, memory = ThreatBurstStage(small_config()), {}
    advance(stage, memory, [hourly(0)])
    memory["signature"][mismatch] = "old"
    with pytest.raises(ValueError, match="migration"):
        advance(stage, memory, [hourly(1)])


def test_state_does_not_accumulate_hour_history_or_unknown_type_names():
    stage, memory = ThreatBurstStage(small_config()), {}
    advance(stage, memory, [hourly(i, counts={f"unknown-{i}": 3}) for i in range(10)])
    initial_size = len(pickle.dumps(memory))
    advance(stage, memory, [hourly(i, counts={f"unknown-{i}": 3}) for i in range(10, 200)])
    assert len(pickle.dumps(memory)) < initial_size * 1.25
    assert set(memory["baseline"]) == set(TYPES)
    assert "history" not in memory


def test_cooldown_does_not_accumulate_unknown_subjects():
    stage, memory = ThreatBurstStage(small_config(baseline_alpha=0.001)), {}
    advance(stage, memory, [hourly(i) for i in range(4)])
    for i in range(4, 200):
        advance(stage, memory, [hourly(i, counts={f"new-type-{i}": 100 * (i + 1)})])
        assert len(memory["last_alerts"]) <= stage.config.cooldown_hours


@pytest.mark.parametrize("bad", [
    {}, None, "not JSON", hourly(0, "Germany"), hourly(0, counts={"malware": -1}),
    {"country": "HU", "day": "2026-10-12", "counts": {"malware": 3}},
    {**hourly(0), "hour": 24},
    ("agg.threats_daily", hourly(0)),
])
def test_invalid_records_are_routed_without_training(bad):
    stage = ThreatBurstStage(small_config())
    result = run_local(stage, [bad, hourly(0)])
    assert len(result.invalid) == len(result.outputs) == 1
    contracts.validate(contracts.INVALID_TOPIC, result.invalid[0])


def test_json_schema_integer_valued_float_hour_is_supported():
    row = {**hourly(4, counts={"phishing": 100}), "hour": 4.0}
    [out] = run_local(ThreatBurstStage(small_config()), [hourly(i) for i in range(4)] + [row]).outputs
    assert out.value["created_at"] == "2026-10-12T04:00:00Z"


def test_cli_routes_malformed_json_and_clears_previous_output(tmp_path, capsys):
    source, target = tmp_path / "input.jsonl", tmp_path / "output"
    source.write_text(json.dumps(hourly(0)) + "\nnot-json\n{}\n\n", encoding="utf-8")
    main(["--input", str(source), "--output-dir", str(target)])
    assert capsys.readouterr().out == "alerts.jsonl: 0 rows\ninvalid.jsonl: 2 rows\n"
    for line in (target / "invalid.jsonl").read_text().splitlines():
        contracts.validate(contracts.INVALID_TOPIC, json.loads(line))
    source.write_text(json.dumps(hourly(0)), encoding="utf-8")
    main(["--input", str(source), "--output-dir", str(target)])
    assert (target / "invalid.jsonl").read_text() == (target / "alerts.jsonl").read_text() == ""


def test_cli_rejects_bad_threshold_before_running(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--demo", "--threshold", "nan"])
    assert error.value.code == 2
    assert "threshold must be between" in capsys.readouterr().err


def test_cli_requires_explicit_source_instead_of_running_demo(capsys):
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    assert "--input --demo" in capsys.readouterr().err


@pytest.mark.flink
@pytest.mark.parametrize("sparse", [False, True], ids=["demo", "quiet-hours-and-cooldown"])
def test_real_flink_matches_local_with_country_models_and_invalid_records(sparse):
    # The unchanged shared runner executes in a fresh test process. No private
    # Flink environment or repository-root shipping is needed.
    if sparse:
        stage = ThreatBurstStage(small_config(baseline_alpha=0.001))
        records = [hourly(0, country) for country in ("HU", "DE")]
        records += [hourly(i, country, {"botnet": 100})
                    for i in range(48, 55) for country in ("HU", "DE")]
    else:
        stage = ThreatBurstStage()
        records = list(hourly_fixture())
    records += [records[-1], records[0], {},
                {"country": "HU", "day": "2026-10-19", "counts": {"malware": 3}}]
    actual = run_flink_local(stage, records)
    expected = run_local(stage, records).outputs

    def normalized(outputs):
        return sorted((out.topic, out.key, json.dumps(
            {key: value for key, value in out.value.items() if key != "detected_at"}, sort_keys=True,
        )) for out in outputs)

    assert normalized(actual) == normalized(expected)
    assert sum(out.topic == "alerts.threats" for out in actual) == (4 if sparse else 1)
    assert sum(out.topic == contracts.INVALID_TOPIC for out in actual) == 2
