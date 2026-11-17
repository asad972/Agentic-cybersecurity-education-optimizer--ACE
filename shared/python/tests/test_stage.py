import threading
import uuid
from datetime import timedelta

import pytest

from ace.stage import RecordStage, Window, WindowedStage, event_time_ms, run_flink_local, run_local


def threat(country, observed_at, threat_type="malware"):
    return {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": country,
        "observed_at": observed_at,
        "threat_type": threat_type,
    }


class ThreatsPerHour(WindowedStage):
    """Like S7: hourly windows with a 15-minute grace period."""

    name = "test-threats"
    input = "threats.events"
    output = "agg.threats_hourly"
    time_field = "observed_at"
    window = timedelta(hours=1)
    grace = timedelta(minutes=15)

    def new(self):
        return 0

    def add(self, count, record):
        return count + 1

    def result(self, key, window, count, memory):
        memory["total"] = memory.get("total", 0) + count  # kept per country across windows
        return [{"country": key, "hour": window.start.isoformat(), "count": count,
                 "total": memory["total"]}]


def windows_of(stage, records):
    """Run locally and capture what result() returned, without checking the output schema."""
    seen = []
    original = stage.result

    def capture(key, window, state, memory):
        seen.append(original(key, window, state, memory)[0])
        return []

    stage.result = capture
    result = run_local(stage, records)
    return seen, result


def test_event_time_of_dates_and_timestamps():
    assert event_time_ms("2026-10-01") == event_time_ms("2026-10-01T00:00:00Z")
    assert event_time_ms("2026-10-01T02:00:00+02:00") == event_time_ms("2026-10-01T00:00:00Z")


def test_window_day():
    window = Window.from_ms(event_time_ms("2026-10-01"), event_time_ms("2026-10-02"))
    assert window.day == "2026-10-01"


def test_hourly_window_closes_after_15_minutes_of_grace():
    records = [
        threat("DE", "2026-10-01T10:05:00Z"),
        threat("DE", "2026-10-01T11:10:00Z"),   # 10:00 window still open (grace until 11:15)
        threat("DE", "2026-10-01T10:50:00Z"),   # so this one still counts
        threat("DE", "2026-10-01T11:20:00Z"),   # now 10:00 window closes
        threat("DE", "2026-10-01T10:55:00Z"),   # late
    ]
    seen, result = windows_of(ThreatsPerHour(), records)
    assert [(w["hour"], w["count"]) for w in seen] == [
        ("2026-10-01T10:00:00+00:00", 2),
        ("2026-10-01T11:00:00+00:00", 2),
    ]
    assert [r["observed_at"] for r in result.late] == ["2026-10-01T10:55:00Z"]


def test_memory_is_kept_per_key_across_windows():
    records = [threat("DE", "2026-10-01T10:00:00Z"), threat("FR", "2026-10-01T10:00:00Z"),
               threat("DE", "2026-10-01T11:00:00Z"), threat("DE", "2026-10-01T13:00:00Z")]
    seen, _ = windows_of(ThreatsPerHour(), records)
    de = [w["total"] for w in seen if w["country"] == "DE"]
    fr = [w["total"] for w in seen if w["country"] == "FR"]
    assert de == [1, 2, 3] and fr == [1]


def test_state_that_flink_cannot_save_fails_in_tests():
    class Unpicklable(ThreatsPerHour):
        def new(self):
            return threading.Lock()

        def add(self, state, record):
            return state

    with pytest.raises(TypeError):
        windows_of(Unpicklable(), [threat("DE", "2026-10-01T10:00:00Z")])


class FirstSeen(RecordStage):
    """Like S1: forwards a job ad only the first time its title is seen in a country."""

    name = "test-first-seen"
    inputs = ("raw.jobs",)

    def process(self, topic, record, memory):
        titles = memory.setdefault("titles", set())
        if record["job_title"] not in titles:
            titles.add(record["job_title"])
            yield "jobs.clean", record


def job(country, title):
    return {"event_id": str(uuid.uuid4()), "source": "test", "country": country,
            "posted_date": "2026-10-01", "job_title": title, "description": "..."}


JOBS = [job("DE", "SOC Analyst"), job("DE", "SOC Analyst"), job("FR", "SOC Analyst"),
        {"country": "DE"}]


def test_record_stage_keeps_memory_per_key_and_rejects_invalid_input():
    result = run_local(FirstSeen(), JOBS)
    assert [(r["country"], r["job_title"]) for r in result.published("jobs.clean")] == [
        ("DE", "SOC Analyst"), ("FR", "SOC Analyst")]
    assert len(result.invalid) == 1


class OpenedFirstSeen(FirstSeen):
    """Forwards nothing unless open() was called, and counts what it forwards in a Flink metric."""

    name = "test-opened-first-seen"
    forwarded = None

    def open(self, runtime_context):
        self.forwarded = runtime_context.get_metrics_group().counter("test_forwarded_total")

    def process(self, topic, record, memory):
        if self.forwarded is None:
            return
        for out in super().process(topic, record, memory):
            self.forwarded.inc()
            yield out


class OpenedThreats(WindowedStage):
    """Publishes a valid hourly count per country, but only if open() was called."""

    name = "test-opened-threats"
    input = "threats.events"
    output = "agg.threats_hourly"
    time_field = "observed_at"
    window = timedelta(hours=1)
    grace = timedelta(minutes=15)
    windows = None

    def open(self, runtime_context):
        self.windows = runtime_context.get_metrics_group().counter("test_windows_total")

    def new(self):
        return 0

    def add(self, count, record):
        return count + 1

    def result(self, key, window, count, memory):
        if self.windows is None:
            return []
        self.windows.inc()
        return [{"country": key, "day": window.day, "hour": window.start.hour,
                 "counts": {"malware": count}}]


def test_open_does_nothing_by_default_and_run_local_never_calls_it():
    assert RecordStage().open(object()) is None
    assert WindowedStage().open(object()) is None
    assert run_local(OpenedFirstSeen(), JOBS).published("jobs.clean") == []


# Keep this file at two Flink tests: a third local Flink job in the same pytest
# process hangs. Each test below therefore checks two things.

@pytest.mark.flink
def test_record_stage_on_flink_matches_local_and_flink_calls_open():
    # OpenedFirstSeen forwards nothing unless Flink called open(), so matching
    # the plain stage run locally proves both.
    flink = run_flink_local(OpenedFirstSeen(), JOBS)
    local = run_local(FirstSeen(), JOBS)
    assert sorted((o.topic, o.key) for o in flink) == sorted((o.topic, o.key) for o in local.outputs)


@pytest.mark.flink
def test_flink_calls_open_on_a_windowed_stage():
    records = [threat("DE", "2026-10-01T10:05:00Z"), threat("FR", "2026-10-01T10:10:00Z")]
    flink = run_flink_local(OpenedThreats(), records)
    assert sorted(o.key for o in flink if o.topic == "agg.threats_hourly") == ["DE", "FR"]
