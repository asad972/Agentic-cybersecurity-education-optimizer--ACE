-- ACE Cassandra schema. Shared contract: changes are agreed by the whole team.
-- Development uses replication factor 1; the Kubernetes cluster uses 3
-- (NetworkTopologyStrategy). Query-first design: one table per question.

CREATE KEYSPACE IF NOT EXISTS ace
  WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

USE ace;

-- Raw data (TTL 2 years = 63072000 s)

CREATE TABLE IF NOT EXISTS job_ads_raw (
  country     text,
  month       text,          -- 'YYYY-MM'
  posted_date date,
  event_id    uuid,
  source      text,
  job_title   text,
  description text,
  ingested_at timestamp,
  PRIMARY KEY ((country, month), posted_date, event_id)
) WITH CLUSTERING ORDER BY (posted_date DESC, event_id ASC)
  AND default_time_to_live = 63072000;

CREATE TABLE IF NOT EXISTS profiles_raw (
  country     text,
  month       text,
  event_id    uuid,
  source      text,
  headline    text,
  skills      list<text>,
  ingested_at timestamp,
  PRIMARY KEY ((country, month), event_id)
) WITH default_time_to_live = 63072000;

-- Stream aggregates (written by the sink from agg.* topics)

CREATE TABLE IF NOT EXISTS skills_needed_by_day (
  country text,
  day     date,
  count   int,
  skill   text,
  PRIMARY KEY ((country, day), count, skill)
) WITH CLUSTERING ORDER BY (count DESC, skill ASC);

CREATE TABLE IF NOT EXISTS skills_offered_by_day (
  country text,
  day     date,
  count   int,
  skill   text,
  PRIMARY KEY ((country, day), count, skill)
) WITH CLUSTERING ORDER BY (count DESC, skill ASC);

CREATE TABLE IF NOT EXISTS top10_needed (
  country    text,
  rank       int,
  skill      text,
  count      int,
  updated_at timestamp,
  PRIMARY KEY (country, rank)
);

CREATE TABLE IF NOT EXISTS skill_trend (
  country text,
  skill   text,
  day     date,
  count   int,
  PRIMARY KEY ((country, skill), day)
) WITH CLUSTERING ORDER BY (day ASC);

CREATE TABLE IF NOT EXISTS threats_by_day (
  day         date,
  country     text,
  threat_type text,
  count       int,
  PRIMARY KEY (day, country, threat_type)
);

CREATE TABLE IF NOT EXISTS alerts (
  country    text,
  day        date,
  created_at timestamp,
  alert_type text,           -- 'drift' (S6) or 'threat_burst' (S8)
  subject    text,           -- skill or threat type
  score      double,
  details    text,
  PRIMARY KEY ((country, day), created_at, alert_type, subject)
) WITH CLUSTERING ORDER BY (created_at DESC, alert_type ASC, subject ASC);

-- Batch results

CREATE TABLE IF NOT EXISTS forecasts (
  region       text,         -- country code or 'EU'
  skill        text,
  target_month text,         -- 'YYYY-MM'
  predicted    double,
  lower        double,
  upper        double,
  model        text,
  trained_at   timestamp,
  PRIMARY KEY ((region, skill), target_month)
);

CREATE TABLE IF NOT EXISTS skills_gap (
  country     text,
  month       text,
  skill       text,
  needed      int,
  offered     int,
  gap_ratio   double,        -- needed / offered
  PRIMARY KEY ((country, month), skill)
);
