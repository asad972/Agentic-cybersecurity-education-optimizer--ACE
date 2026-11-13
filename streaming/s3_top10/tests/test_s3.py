"""S3 with fake job ads: daily 30-day Top 10s per Country, for Skills and Certifications."""

import uuid
from datetime import date, timedelta

import pytest

from ace import contracts
from ace.stage import run_flink_local, run_local
from s3_top10.stage import TopTen


def ad(country, day, skills, certifications=()):
    message = {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": country,
        "posted_date": day,
        "skills": list(skills),
    }
    if certifications:
        message["certifications"] = list(certifications)
    return message


def day_after(start, days):
    return (date.fromisoformat(start) + timedelta(days=days)).isoformat()


ADS = [
    ad("DE", "2026-10-01", ["SIEM", "Incident response"], ["CISSP"]),
    ad("DE", "2026-10-01", ["SIEM", "GDPR"], ["CISSP", "CISM"]),
    ad("FR", "2026-10-01", ["Penetration testing"], ["OSCP"]),
    ad("DE", "2026-10-02", ["GDPR", "Cloud security"]),
    ad("DE", "2026-10-03", ["GDPR"]),      # closes Oct 1: its grace period is over
]


def top10s(outputs):
    """(country, day, kind) -> [(item, count), ...] in rank order."""
    return {(m["country"], m["day"], m["kind"]): [(i["item"], i["count"]) for i in m["items"]]
            for m in outputs}


def test_top10_per_country_day_and_kind():
    result = run_local(TopTen(), ADS)
    tops = top10s(result.published("agg.top10_demand"))

    assert tops["DE", "2026-10-01", "skill"] == [("SIEM", 2), ("GDPR", 1), ("Incident response", 1)]
    assert tops["DE", "2026-10-01", "certification"] == [("CISSP", 2), ("CISM", 1)]
    assert tops["FR", "2026-10-01", "certification"] == [("OSCP", 1)]
    # Oct 2 and 3 cover the 30 days up to that day, so Oct 1 still counts.
    assert tops["DE", "2026-10-02", "skill"] == [
        ("GDPR", 2), ("SIEM", 2), ("Cloud security", 1), ("Incident response", 1)]
    assert tops["DE", "2026-10-03", "skill"][0] == ("GDPR", 3)
    assert result.late == [] and result.invalid == []


def test_a_job_ad_counts_once_per_item_even_if_listed_twice():
    ads = [
        ad("DE", "2026-10-01", ["SIEM", "SIEM", "GDPR"], ["CISSP", "CISSP"]),
        ad("DE", "2026-10-01", ["GDPR"]),
        ad("DE", "2026-10-03", ["GDPR"]),  # closes Oct 1
    ]
    tops = top10s(run_local(TopTen(), ads).published("agg.top10_demand"))

    assert tops["DE", "2026-10-01", "skill"] == [("GDPR", 2), ("SIEM", 1)]
    assert tops["DE", "2026-10-01", "certification"] == [("CISSP", 1)]


def test_both_kinds_are_published_once_per_country_and_day():
    result = run_local(TopTen(), ADS)
    keys = [(m["country"], m["day"], m["kind"]) for m in result.published("agg.top10_demand")]

    assert len(keys) == len(set(keys))
    assert sorted(keys) == sorted(
        (country, day, kind)
        for country, day in [("DE", "2026-10-01"), ("DE", "2026-10-02"),
                             ("DE", "2026-10-03"), ("FR", "2026-10-01")]
        for kind in ("skill", "certification"))
    # A day with no Certifications still gets its (empty) Certification Top 10.
    assert top10s(result.published("agg.top10_demand"))["DE", "2026-10-03", "certification"] == [
        ("CISSP", 2), ("CISM", 1)]


def test_ranks_run_from_1_and_stop_at_10():
    skills = [f"Skill {i:02d}" for i in range(15)]
    # Skill 00 is in 15 ads, Skill 01 in 14, ... Skill 14 in 1.
    ads = [ad("DE", "2026-10-01", skills[:n]) for n in range(1, 16)]
    [message] = [m for m in run_local(TopTen(), ads).published("agg.top10_demand")
                 if m["kind"] == "skill"]

    assert [i["rank"] for i in message["items"]] == list(range(1, 11))
    assert [i["item"] for i in message["items"]] == skills[:10]
    assert [i["count"] for i in message["items"]] == list(range(15, 5, -1))


def test_every_message_fits_the_schema():
    for message in run_local(TopTen(), ADS).published("agg.top10_demand"):
        contracts.validate("agg.top10_demand", message)


def test_day_31_no_longer_counts_the_first_day():
    start = "2026-10-01"
    ads = [ad("DE", start, ["Forensics"], ["CISA"])]
    ads += [ad("DE", day_after(start, n), ["SIEM"]) for n in range(1, 31)]
    tops = top10s(run_local(TopTen(), ads).published("agg.top10_demand"))

    day_30, day_31 = day_after(start, 29), day_after(start, 30)
    assert ("Forensics", 1) in tops["DE", day_30, "skill"]
    assert tops["DE", day_30, "skill"][0] == ("SIEM", 29)
    assert tops["DE", day_30, "certification"] == [("CISA", 1)]
    assert tops["DE", day_31, "skill"] == [("SIEM", 30)]
    assert tops["DE", day_31, "certification"] == []


def test_memory_keeps_only_the_last_30_days():
    stage = TopTen()
    kept = []
    original = stage.result

    def spy(key, window, state, memory):
        messages = original(key, window, state, memory)
        kept.append(sorted(memory["days"]))
        return messages

    stage.result = spy
    run_local(stage, [ad("DE", day_after("2026-10-01", n), ["SIEM"]) for n in range(40)])

    assert len(kept[-1]) == 30
    assert kept[-1][0] == day_after("2026-10-01", 10)


def test_countries_are_kept_apart():
    ads = [ad("DE", "2026-10-01", ["SIEM"]), ad("FR", "2026-10-01", ["GDPR"]),
           ad("FR", "2026-10-02", ["GDPR"])]
    tops = top10s(run_local(TopTen(), ads).published("agg.top10_demand"))
    assert tops["DE", "2026-10-01", "skill"] == [("SIEM", 1)]
    assert tops["FR", "2026-10-02", "skill"] == [("GDPR", 2)]


def test_record_after_its_day_closed_is_late_and_not_counted():
    late_ad = ad("DE", "2026-10-01", ["Forensics"])
    result = run_local(TopTen(), ADS + [late_ad])

    assert result.late == [late_ad]
    tops = top10s(result.published("agg.top10_demand"))
    assert all(item != "Forensics" for item, _ in tops["DE", "2026-10-03", "skill"])


def test_out_of_order_record_within_grace_is_counted():
    early = [ad("DE", "2026-10-02", ["SIEM"]), ad("DE", "2026-10-01", ["IAM"])]
    result = run_local(TopTen(), early)
    tops = top10s(result.published("agg.top10_demand"))
    assert tops["DE", "2026-10-01", "skill"] == [("IAM", 1)]
    assert tops["DE", "2026-10-02", "skill"] == [("IAM", 1), ("SIEM", 1)]
    assert result.late == []


def test_invalid_input_goes_to_errors_invalid():
    bad = ad("Germany", "2026-10-01", ["SIEM"])
    result = run_local(TopTen(), [bad])

    assert result.published("agg.top10_demand") == []
    [invalid] = result.invalid
    assert invalid["topic"] == "skills.demand"
    assert invalid["producer"] == "s3-top10"


@pytest.mark.flink
def test_flink_gives_the_same_result_as_local():
    flink = [o.value for o in run_flink_local(TopTen(), ADS) if o.topic == "agg.top10_demand"]
    assert top10s(flink) == top10s(run_local(TopTen(), ADS).published("agg.top10_demand"))
