"""Raw records: job ads, profiles, education programmes and workforce figures.

Raw tables keep their data for 2 years (default_time_to_live in schema.cql).
ingested_at and updated_at are the time the sink received the message. The
Kafka record time is not used: producers such as Flink stages set it to the
event time, e.g. a job ad's posting date.
"""

from uuid import UUID

from ace_sink.rows import Row, rows_for
from ace_sink.values import day, month


@rows_for("jobs.clean")
def job_ad(message, received_at):
    return [Row("job_ads_raw", {
        "country": message["country"],
        "month": month(message["posted_date"]),
        "posted_date": day(message["posted_date"]),
        "event_id": UUID(message["event_id"]),
        "source": message["source"],
        "job_title": message["job_title"],
        "company": message.get("company"),
        "description": message["description"],
        "ingested_at": received_at,
    })]


@rows_for("raw.profiles")
def profile(message, received_at):
    # profiles_raw has no column for the profile text, and its skills column
    # is not part of raw.profiles (Skills arrive later on skills.supply).
    return [Row("profiles_raw", {
        "country": message["country"],
        "month": month(message["profile_date"]),
        "event_id": UUID(message["event_id"]),
        "source": message["source"],
        "profile_date": day(message["profile_date"]),
        "headline": message.get("headline"),
        "ingested_at": received_at,
    })]


@rows_for("raw.programmes")
def programme(message, received_at):
    return [Row("education_programmes", {
        "country": message["country"],
        "programme_id": message["programme_id"],
        "name": message["name"],
        "institution": message["institution"],
        "level": message.get("level"),
        "ecsf_roles": set(message["ecsf_roles"]),
        "url": message.get("url"),
        "updated_at": received_at,
    })]


@rows_for("raw.workforce")
def workforce(message, received_at):
    return [Row("workforce_baseline", {
        "country": message["country"],
        "year": message["year"],
        "workforce_size": message.get("workforce_size"),
        "workforce_gap": message.get("workforce_gap"),
        "source": message["source"],
    })]
