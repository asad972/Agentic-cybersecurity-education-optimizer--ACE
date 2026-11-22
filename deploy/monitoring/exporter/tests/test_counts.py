import json
import threading

from ace_exporter.counts import TOPICS, Labels, PipelineCounts, mentions, parse


def message(**fields):
    return json.dumps(fields).encode("utf-8")


def test_reads_every_pipeline_health_topic():
    assert set(TOPICS) == {
        "raw.jobs", "raw.profiles", "raw.programmes", "raw.workforce", "threats.events",
        "jobs.clean", "skills.demand", "skills.supply", "errors.invalid", "tasks.collect",
    }


def test_counts_records_per_topic_and_source():
    counts = PipelineCounts()
    counts.add("raw.jobs", message(source="adzuna", country="DE"))
    counts.add("raw.jobs", message(source="adzuna", country="FR"))
    counts.add("raw.jobs", message(source="ba", country="DE"))
    counts.add("jobs.clean", message(source="adzuna", country="DE"))
    counts.add("threats.events", message(source="otx", country="US"))

    assert counts.snapshot().records == {
        ("raw.jobs", "adzuna"): 2,
        ("raw.jobs", "ba"): 1,
        ("jobs.clean", "adzuna"): 1,
        ("threats.events", "otx"): 1,
    }


def test_a_record_without_a_source_counts_as_unknown():
    # raw.programmes has no source field.
    counts = PipelineCounts()
    counts.add("raw.programmes", message(programme_id="p1", country="HU"))
    counts.add("raw.workforce", message(country="HU", year=2024, source="  "))

    assert counts.snapshot().records == {("raw.programmes", "unknown"): 1, ("raw.workforce", "unknown"): 1}


def test_counts_skill_certification_and_unmapped_mentions():
    counts = PipelineCounts()
    counts.add("skills.demand", message(source="adzuna", skills=["SIEM", "Python"],
                                        certifications=["CompTIA Security+"], unmapped=["Splunk dashboards"]))
    counts.add("skills.demand", message(source="adzuna", skills=["SIEM"], unmapped=["Splunk dashboards", "Wazuh"]))
    counts.add("skills.supply", message(source="stackoverflow_survey", skills=["Python"]))

    snap = counts.snapshot()
    assert snap.mentions == {
        ("skills.demand", "skill"): 3,
        ("skills.demand", "certification"): 1,
        ("skills.demand", "unmapped"): 3,
        ("skills.supply", "skill"): 1,
        ("skills.supply", "certification"): 0,
        ("skills.supply", "unmapped"): 0,
    }
    assert snap.records == {("skills.demand", "adzuna"): 2, ("skills.supply", "stackoverflow_survey"): 1}


def test_mentions_ignore_lists_that_are_missing_or_not_lists():
    assert mentions({"skills": ["SIEM", 3, None], "unmapped": "Wazuh"}) == {
        "skill": 1, "certification": 0, "unmapped": 0,
    }


def test_mentions_are_not_counted_on_other_topics():
    counts = PipelineCounts()
    counts.add("jobs.clean", message(source="adzuna", skills=["SIEM"]))
    assert counts.snapshot().mentions == {}


def test_counts_invalid_messages_per_target_topic_and_producer():
    counts = PipelineCounts()
    counts.add("errors.invalid", message(topic="skills.demand", producer="s2-extract", reason="x"))
    counts.add("errors.invalid", message(topic="skills.demand", producer="s2-extract", reason="y"))
    counts.add("errors.invalid", message(topic="jobs.clean", producer="sink", reason="z"))

    assert counts.snapshot().invalid == {("skills.demand", "s2-extract"): 2, ("jobs.clean", "sink"): 1}


def test_an_invalid_message_for_an_unknown_topic_counts_as_other():
    counts = PipelineCounts()
    counts.add("errors.invalid", message(topic="skils.demand", producer="job-agent"))
    counts.add("errors.invalid", message(producer="job-agent"))
    assert counts.snapshot().invalid == {("other", "job-agent"): 2}


def test_counts_orchestrator_tasks_per_agent_action_and_decider():
    counts = PipelineCounts()
    task = dict(agent="job-agent", action="fetch_job_ads", decided_by="llm", reason="few job ads for PL")
    counts.add("tasks.collect", message(**task))
    counts.add("tasks.collect", message(**task))
    counts.add("tasks.collect", message(agent="threat-agent", action="refresh_threats", decided_by="rules"))

    assert counts.snapshot().tasks == {
        ("job-agent", "fetch_job_ads", "llm"): 2,
        ("threat-agent", "refresh_threats", "rules"): 1,
    }


def test_messages_that_are_not_json_objects_are_counted_as_unreadable():
    counts = PipelineCounts()
    counts.add("raw.jobs", b"not json")
    counts.add("raw.jobs", b"[1, 2]")
    counts.add("errors.invalid", None)
    counts.add("tasks.collect", "\"text\"")

    snap = counts.snapshot()
    assert snap.unreadable == {"raw.jobs": 2, "errors.invalid": 1, "tasks.collect": 1}
    assert snap.records == {} and snap.invalid == {} and snap.tasks == {}


def test_topics_the_exporter_does_not_read_are_ignored():
    counts = PipelineCounts()
    counts.add("agg.demand_daily", message(country="DE"))
    counts.add("no.such.topic", b"x")
    assert counts.snapshot() == PipelineCounts().snapshot()


def test_parse_accepts_bytes_and_text():
    assert parse(b'{"a": 1}') == {"a": 1}
    assert parse('{"a": 1}') == {"a": 1}
    assert parse(b"\xff") is None


def test_labels_are_cut_short_and_limited_in_number():
    labels = Labels(max_values=2, max_length=5)
    assert labels.value("source", "adzuna-api") == "adzun"
    assert labels.value("source", "ba") == "ba"
    assert labels.value("source", "otx") == "other"       # a third value
    assert labels.value("source", "ba") == "ba"           # known values keep their own label
    assert labels.value("producer", "otx") == "otx"       # each label has its own limit
    assert labels.value("source", None) == "unknown"
    assert labels.value("source", 7) == "unknown"


def test_snapshot_is_a_copy():
    counts = PipelineCounts()
    counts.add("raw.jobs", message(source="adzuna"))
    snap = counts.snapshot()
    counts.add("raw.jobs", message(source="adzuna"))
    assert snap.records == {("raw.jobs", "adzuna"): 1}


def test_counting_from_several_threads_loses_nothing():
    counts = PipelineCounts()
    value = message(source="adzuna")

    def add_many():
        for _ in range(2000):
            counts.add("raw.jobs", value)

    threads = [threading.Thread(target=add_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counts.snapshot().records == {("raw.jobs", "adzuna"): 8000}
