"""S4 with a handful of fake job ads; the example every stage test follows.

These tests feed sample messages into the stage and check its daily output,
including how it handles late, out-of-order, and invalid messages.
"""

# Generate a unique event_id for each fake job ad.
import uuid

# Use pytest for the Flink marker on the integration-style test below.
import pytest

# Import the local runner, the local Flink-compatible runner, and the S4 stage.
from ace.stage import run_flink_local, run_local
from s4_demand_frequency.stage import DemandFrequency


# Build one input message in the same shape as a skills.demand record.
def ad(country, day, skills, certifications=(), role=None):
    # Start with fields that every test ad needs.
    message = {
        "event_id": str(uuid.uuid4()),
        "source": "test",
        "country": country,
        "posted_date": day,
        "skills": list(skills),
    }
    # Only include certifications when the caller supplied some.
    if certifications:
        message["certifications"] = list(certifications)
    # Only include an ECSF role when one was supplied.
    if role:
        message["ecsf_role"] = role
    # Return the completed fake input message.
    return message


# Create sample ads across countries and days.
# The Oct 3 record advances event time far enough to close Oct 1's window.
ADS = [
    ad("DE", "2026-10-01", ["SIEM", "Incident response"], ["CISSP"], "Cyber Incident Responder"),
    ad("DE", "2026-10-01", ["SIEM"]),
    ad("FR", "2026-10-01", ["Penetration testing"], ["OSCP"]),
    ad("DE", "2026-10-02", ["Cloud security"]),
    ad("DE", "2026-10-03", ["SIEM"]),      # closes Oct 1: its grace period is over
]


# Convert messages into a lookup table keyed by (country, day).
def daily(outputs):
    return {(m["country"], m["day"]): m for m in outputs}


# Check that S4 counts ads, skills, certifications, and roles by country and day.
def test_counts_per_country_and_day():
    # Process all sample ads with the deterministic local stage runner.
    result = run_local(DemandFrequency(), ADS)
    # Keep only demand-frequency output and index it for easy lookup.
    days = daily(result.published("agg.demand_daily"))

    # One output should exist for each country/day that had an input ad.
    assert sorted(days) == [("DE", "2026-10-01"), ("DE", "2026-10-02"),
                            ("DE", "2026-10-03"), ("FR", "2026-10-01")]
    # Get Germany's daily summary for October 1.
    de = days["DE", "2026-10-01"]
    # Two German ads were posted on that date.
    assert de["job_ads"] == 2
    # SIEM appeared in both German ads.
    assert {"kind": "skill", "item": "SIEM", "count": 2} in de["counts"]
    # CISSP appeared once as a certification.
    assert {"kind": "certification", "item": "CISSP", "count": 1} in de["counts"]
    # The ECSF role appeared once.
    assert {"kind": "role", "item": "Cyber Incident Responder", "count": 1} in de["counts"]
    # None of the sample records should be late or schema-invalid.
    assert result.late == [] and result.invalid == []


# Check that the stage publishes no more than one summary for each country/day.
def test_each_day_is_published_once():
    # Run the stage and collect the country/day keys of its outputs.
    result = run_local(DemandFrequency(), ADS)
    keys = [(m["country"], m["day"]) for m in result.published("agg.demand_daily")]
    # A set removes duplicates, so equal lengths mean every key is unique.
    assert len(keys) == len(set(keys))


# A record for a closed day must be marked late and must not change its result.
def test_record_after_its_day_closed_is_late_and_not_counted():
    # This ad belongs to Oct 1, but will arrive after the sample has closed that day.
    late_ad = ad("DE", "2026-10-01", ["Forensics"])
    # Add it after the Oct 3 record, which advances the watermark past Oct 1's grace period.
    result = run_local(DemandFrequency(), ADS + [late_ad])

    # The late record is reported separately instead of being aggregated.
    assert result.late == [late_ad]
    # Germany's Oct 1 total stays at the two on-time ads.
    de = daily(result.published("agg.demand_daily"))["DE", "2026-10-01"]
    assert de["job_ads"] == 2


# Event-time order may differ from arrival order while a window is still open.
def test_out_of_order_record_within_grace_is_counted():
    # The Oct 2 ad arrives first; then an Oct 1 ad arrives within the grace period.
    early = [ad("DE", "2026-10-02", ["SIEM"]), ad("DE", "2026-10-01", ["IAM"])]
    result = run_local(DemandFrequency(), early)
    # The earlier ad is still included in its own day's summary.
    assert daily(result.published("agg.demand_daily"))["DE", "2026-10-01"]["job_ads"] == 1
    # Neither record arrived too late for its window.
    assert result.late == []


# Invalid input should be routed to the shared errors.invalid topic.
def test_invalid_input_goes_to_errors_invalid():
    # "Germany" is not a valid country code for the skills.demand schema.
    bad = ad("Germany", "2026-10-01", ["SIEM"])
    result = run_local(DemandFrequency(), [bad])

    # Invalid data must not produce a normal aggregated result.
    assert result.published("agg.demand_daily") == []
    # Exactly one invalid-message record should have been produced.
    [invalid] = result.invalid
    # The invalid record identifies the rejected input topic.
    assert invalid["topic"] == "skills.demand"
    # The invalid record identifies S4 as the producer that rejected it.
    assert invalid["producer"] == "s4-demand-count"


# Mark this test so it can be selected or skipped as part of the Flink tests.
@pytest.mark.flink
def test_flink_gives_the_same_result_as_local():
    # Run the stage through the local Flink test harness and keep its normal outputs.
    flink = [o.value for o in run_flink_local(DemandFrequency(), ADS)
             if o.topic == "agg.demand_daily"]
    # The Flink path should produce the same daily summaries as the plain local runner.
    assert daily(flink) == daily(run_local(DemandFrequency(), ADS).published("agg.demand_daily"))
