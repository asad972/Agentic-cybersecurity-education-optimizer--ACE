from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from ace import contracts
from ace_sink.rows import Row

RECEIVED = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "cd54af39-ffa3-4fac-b0f8-1626d885a2b6"


def job_ad(**changes):
    message = {
        "event_id": EVENT_ID,
        "source": "arbeitsagentur",
        "country": "DE",
        "posted_date": "2026-09-21",
        "job_title": "SOC Analyst",
        "company": "Capgemini",
        "description": "Required skills: SIEM, Incident response.",
        "url": "https://jobs.example.com/de/1",
    }
    message.update(changes)
    return message


def test_job_ad_is_stored_by_country_and_month(stored):
    assert stored("jobs.clean", job_ad(), RECEIVED) == [Row("job_ads_raw", {
        "country": "DE",
        "month": "2026-09",
        "posted_date": date(2026, 9, 21),
        "event_id": UUID(EVENT_ID),
        "source": "arbeitsagentur",
        "job_title": "SOC Analyst",
        "company": "Capgemini",
        "description": "Required skills: SIEM, Incident response.",
        "ingested_at": RECEIVED,
    })]


def test_job_ad_without_company_leaves_it_unset(stored):
    message = job_ad()
    del message["company"]
    [row] = stored("jobs.clean", message, RECEIVED)
    assert row.values["company"] is None


def test_profile_keeps_no_text(stored):
    message = {
        "event_id": EVENT_ID,
        "source": "stackoverflow",
        "country": "FR",
        "profile_date": "2026-08-10",
        "headline": "Cybersecurity Engineer",
        "text": "Firewalls, Docker, Linux",
    }
    [row] = stored("raw.profiles", message, RECEIVED)
    assert row.table == "profiles_raw"
    assert row.values["month"] == "2026-08"
    assert row.values["headline"] == "Cybersecurity Engineer"
    assert "Firewalls, Docker, Linux" not in row.values.values()


def test_programme_roles_become_a_set(stored):
    message = {
        "programme_id": "cyberhead-123",
        "country": "NL",
        "name": "MSc Cyber Security",
        "institution": "TU Delft",
        "level": "master",
        "ecsf_roles": ["Cybersecurity Architect", "Penetration Tester", "Cybersecurity Architect"],
    }
    [row] = stored("raw.programmes", message, RECEIVED)
    assert row.table == "education_programmes"
    assert row.values["ecsf_roles"] == {"Cybersecurity Architect", "Penetration Tester"}
    assert row.values["url"] is None
    assert row.values["updated_at"] == RECEIVED


@pytest.mark.parametrize("scope", [
    {"country": "DE"},
    {"geography_level": "country", "geography": "DE"},
])
def test_workforce_country_messages_write_both_tables(stored, scope):
    message = {**scope, "year": 2023, "workforce_size": 120000, "source": "ISC2 2023"}
    estimates = {
        "year": 2023,
        "workforce_size": 120000,
        "workforce_gap": None,
        "source": "ISC2 2023",
    }
    assert contracts.route("raw.workforce", message, producer="test").key == "DE"
    assert stored("raw.workforce", message, RECEIVED) == [
        Row("workforce_baseline_by_geography", {
            "geography_level": "country",
            "geography": "DE",
            **estimates,
            "workforce_growth_percent": None,
            "workforce_gap_growth_percent": None,
            "source_reference": None,
        }),
        Row("workforce_baseline", {"country": "DE", **estimates}),
    ]


@pytest.mark.parametrize(("level", "geography"), [("region", "Europe"), ("global", "GLOBAL")])
def test_workforce_non_country_messages_do_not_pollute_country_table(stored, level, geography):
    message = {
        "geography_level": level,
        "geography": geography,
        "year": 2024,
        "workforce_gap": 392320,
        "workforce_gap_growth_percent": 12,
        "source": "ISC2 2024",
        "source_reference": "Figure 3",
    }
    assert stored("raw.workforce", message, RECEIVED) == [
        Row("workforce_baseline_by_geography", {
            **message,
            "workforce_size": None,
            "workforce_growth_percent": None,
            "workforce_gap_growth_percent": 12.0,
        }),
    ]


def test_same_message_gives_the_same_rows(stored):
    assert stored("jobs.clean", job_ad(), RECEIVED) == stored("jobs.clean", job_ad(), RECEIVED)
