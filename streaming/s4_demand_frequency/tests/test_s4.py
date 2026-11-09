"""S4 with a handful of fake job ads; the example every stage test follows."""

import random
import uuid
from collections import Counter

import pytest

from ace import contracts
from ace.stage import run_flink_local, run_local
from s4_demand_frequency.stage import UNKNOWN_LIMIT, DemandFrequency, mentions
from s4_demand_frequency.taxonomy_keys import key_set


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


def exact_counts(ads):
    """What the sketch estimates should be: plain counts per Country and day."""
    exact = {}
    for a in ads:
        exact.setdefault((a["country"], a["posted_date"]), Counter()).update(mentions(a))
    return exact


def as_counter(message):
    return Counter({(c["kind"], c["item"]): c["count"] for c in message["counts"]})


def many_ads(n, seed):
    """n job ads over a few countries and days, using real taxonomy entries."""
    rng = random.Random(seed)
    keys = sorted(key_set())
    by_kind = {kind: [item for k, item in keys if k == kind] for kind in ("skill", "certification", "role")}
    return [
        ad(rng.choice(["DE", "FR", "NL"]), f"2026-10-0{1 + i * 3 // n}",
           rng.sample(by_kind["skill"], rng.randint(1, 6)),
           rng.sample(by_kind["certification"], rng.randint(0, 2)),
           rng.choice(by_kind["role"]))
        for i in range(n)
    ]


def test_counts_match_exact_counts_on_fake_ads():
    result = run_local(DemandFrequency(), ADS)
    published = {k: as_counter(m) for k, m in daily(result.published("agg.demand_daily")).items()}
    assert published == exact_counts(ADS)


def test_sketch_is_exact_for_the_taxonomy_with_default_width():
    ads = many_ads(600, seed=1)
    days = daily(run_local(DemandFrequency(), ads).published("agg.demand_daily"))
    assert {k: as_counter(m) for k, m in days.items()} == exact_counts(ads)


def test_narrow_sketch_never_under_counts_and_job_ads_stay_exact():
    ads = many_ads(600, seed=2)
    days = daily(run_local(DemandFrequency(width=16, depth=2), ads).published("agg.demand_daily"))
    exact = exact_counts(ads)
    assert sorted(days) == sorted(exact)
    over = 0
    for day, counts in exact.items():
        published = as_counter(days[day])
        assert all(published[k] >= count for k, count in counts.items())
        over += sum(published[k] > count for k, count in counts.items())
        assert days[day]["job_ads"] == sum(1 for a in ads if (a["country"], a["posted_date"]) == day)
    assert over > 0   # so narrow it really over-counts


def test_only_items_above_zero_are_published():
    keys = {("skill", "SIEM"), ("skill", "Threat hunting"), ("certification", "CISSP")}
    [message] = run_local(DemandFrequency(keys), [ad("DE", "2026-10-01", ["SIEM"])]).published("agg.demand_daily")
    assert message["counts"] == [{"kind": "skill", "item": "SIEM", "count": 1}]


def test_item_missing_from_the_taxonomy_is_still_published():
    keys = {("skill", "SIEM")}
    [message] = run_local(DemandFrequency(keys), [ad("DE", "2026-10-01", ["SIEM", "Quantum security"])]
                          ).published("agg.demand_daily")
    assert {"kind": "skill", "item": "Quantum security", "count": 1} in message["counts"]


def test_unknown_items_are_capped():
    flood = [ad("DE", "2026-10-01", [f"unknown {i}" for i in range(UNKNOWN_LIMIT + 50)])]
    [message] = run_local(DemandFrequency({("skill", "SIEM")}), flood).published("agg.demand_daily")
    assert len(message["counts"]) == UNKNOWN_LIMIT


def test_output_fits_agg_demand_daily():
    for message in run_local(DemandFrequency(), many_ads(200, seed=3)).published("agg.demand_daily"):
        contracts.validate("agg.demand_daily", message)


@pytest.mark.flink
def test_flink_gives_the_same_result_as_local():
    flink = [o.value for o in run_flink_local(DemandFrequency(), ADS)
             if o.topic == "agg.demand_daily"]
    assert daily(flink) == daily(run_local(DemandFrequency(), ADS).published("agg.demand_daily"))
