import json
from datetime import datetime, timezone

import pytest

from ace import contracts


def demand_message(**changes):
    message = {
        "event_id": "0b6c1f4e-2f0a-4c4e-9d43-7a1e2b3c4d5e",
        "source": "adzuna",
        "country": "DE",
        "posted_date": "2026-10-01",
        "skills": ["SIEM", "Incident response"],
    }
    message.update(changes)
    return message


def test_every_topic_has_a_valid_schema_that_requires_its_key():
    for t in contracts.topics().values():
        validator = contracts._validator(t.schema)
        assert t.key_field in validator.schema["required"], t.name


def test_topic_names_come_from_topics_csv():
    assert contracts.topic("skills.demand").schema == "skills_demand"
    assert contracts.topic("agg.demand_daily").key_field == "country"


def test_unknown_topic_fails_with_a_suggestion():
    with pytest.raises(contracts.UnknownTopic, match="did you mean 'skills.demand'"):
        contracts.topic("skill.demand")


def test_valid_message_passes():
    assert contracts.check("skills.demand", demand_message()) is None
    contracts.validate("skills.demand", demand_message())


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"country": "Germany"}, "country"),       # must be a two-letter code such as DE
        ({"posted_date": "01.10.2026"}, "posted_date"),
        ({"skills": []}, "skills"),
        ({"salary": 50000}, "salary"),             # fields outside the schema are rejected
    ],
)
def test_invalid_message_names_the_field(changes, expected):
    reason = contracts.check("skills.demand", demand_message(**changes))
    assert reason is not None and expected in reason


def test_missing_field_is_reported():
    message = demand_message()
    del message["posted_date"]
    assert "posted_date" in contracts.check("skills.demand", message)


def test_validate_raises():
    with pytest.raises(contracts.InvalidMessage) as error:
        contracts.validate("skills.demand", demand_message(country="de"))
    assert error.value.topic == "skills.demand"


def test_key_is_the_field_named_in_topics_csv():
    assert contracts.key_of("skills.demand", demand_message(country="FR")) == "FR"


def test_route_sends_a_valid_message_to_its_topic():
    out = contracts.route("skills.demand", demand_message(), producer="s2-extract")
    assert (out.topic, out.key) == ("skills.demand", "DE")
    assert out.value == demand_message()


def test_route_sends_an_invalid_message_to_errors_invalid_unchanged():
    bad = demand_message(country="de")
    out = contracts.route("skills.demand", bad, producer="s2-extract")
    assert out.topic == contracts.INVALID_TOPIC
    assert out.key == "skills.demand"
    assert out.value["producer"] == "s2-extract"
    assert "country" in out.value["reason"]
    assert json.loads(out.value["payload"]) == bad
    contracts.validate(contracts.INVALID_TOPIC, out.value)


def test_invalid_record_keeps_text_payload_and_time():
    when = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    record = contracts.invalid_record("raw.jobs", "job-agent", "not JSON", "{oops", detected_at=when)
    assert record["payload"] == "{oops"
    assert record["detected_at"] == "2026-10-18T12:00:00Z"


def test_schemas_dir_can_be_set_for_containers(monkeypatch, tmp_path):
    monkeypatch.setenv("ACE_SCHEMAS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="ACE_SCHEMAS_DIR"):
        contracts.schemas_dir()
