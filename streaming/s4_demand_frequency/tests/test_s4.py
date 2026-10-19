"""S4 with a handful of fake job ads; the example every stage test follows."""

import uuid

import pytest

from ace.stage import run_flink_local, run_local
from s4_demand_frequency.stage import DemandFrequency


def ad(country, day, skills, certifications=(), role=None):
    message = {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": country,
        "posted_date": day,
        "skills": list(skills),
    }
    if certifications:
        message["certifications"] = list(certifications)
    if role:
        message["ecsf_role"] = role
    return message


ADS = [
    ad("DE", "2026-10-01", ["SIEM", "Incident response"], ["CISSP"], "Cyber Incident Responder"),
    ad("DE", "2026-10-01", ["SIEM"]),
    ad("FR", "2026-10-01", ["Penetration testing"], ["OSCP"]),
    ad("DE", "2026-10-02", ["Cloud security"]),
    ad("DE", "2026-10-03", ["SIEM"]),      # closes Oct 1: its grace period is over
]


def daily(outputs):
    return {(m["country"], m["day"]): m for m in outputs}


def test_counts_per_country_and_day():
    result = run_local(DemandFrequency(), ADS)
    days = daily(result.published("agg.demand_daily"))

    assert sorted(days) == [("DE", "2026-10-01"), ("DE", "2026-10-02"),
                            ("DE", "2026-10-03"), ("FR", "2026-10-01")]
    de = days["DE", "2026-10-01"]
    assert de["job_ads"] == 2
    assert {"kind": "skill", "item": "SIEM", "count": 2} in de["counts"]
    assert {"kind": "certification", "item": "CISSP", "count": 1} in de["counts"]
    assert {"kind": "role", "item": "Cyber Incident Responder", "count": 1} in de["counts"]
    assert result.late == [] and result.invalid == []


def test_each_day_is_published_once():
    result = run_local(DemandFrequency(), ADS)
    keys = [(m["country"], m["day"]) for m in result.published("agg.demand_daily")]
    assert len(keys) == len(set(keys))


def test_record_after_its_day_closed_is_late_and_not_counted():
    late_ad = ad("DE", "2026-10-01", ["Forensics"])
    result = run_local(DemandFrequency(), ADS + [late_ad])

    assert result.late == [late_ad]
    de = daily(result.published("agg.demand_daily"))["DE", "2026-10-01"]
    assert de["job_ads"] == 2


def test_out_of_order_record_within_grace_is_counted():
    early = [ad("DE", "2026-10-02", ["SIEM"]), ad("DE", "2026-10-01", ["IAM"])]
    result = run_local(DemandFrequency(), early)
    assert daily(result.published("agg.demand_daily"))["DE", "2026-10-01"]["job_ads"] == 1
    assert result.late == []


def test_invalid_input_goes_to_errors_invalid():
    bad = ad("Germany", "2026-10-01", ["SIEM"])
    result = run_local(DemandFrequency(), [bad])

    assert result.published("agg.demand_daily") == []
    [invalid] = result.invalid
    assert invalid["topic"] == "skills.demand"
    assert invalid["producer"] == "s4-demand-count"


@pytest.mark.flink
def test_flink_gives_the_same_result_as_local():
    flink = [o.value for o in run_flink_local(DemandFrequency(), ADS)
             if o.topic == "agg.demand_daily"]
    assert daily(flink) == daily(run_local(DemandFrequency(), ADS).published("agg.demand_daily"))
