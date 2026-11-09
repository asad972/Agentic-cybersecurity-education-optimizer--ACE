-- ACE Cassandra schema. Shared contract: changes are agreed by the whole team.
-- Development uses replication factor 1; the Kubernetes cluster uses 3
-- (NetworkTopologyStrategy). Query-first design: one table per question.
-- Counts use kind ('skill', 'certification' or 'role') and item (the taxonomy entry).

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
  company     text,
  description text,
  ingested_at timestamp,
  PRIMARY KEY ((country, month), posted_date, event_id)
) WITH CLUSTERING ORDER BY (posted_date DESC, event_id ASC)
  AND default_time_to_live = 63072000;

CREATE TABLE IF NOT EXISTS profiles_raw (
  country      text,
  month        text,
  event_id     uuid,
  source       text,
  profile_date date,
  headline     text,
  skills       list<text>,
  ingested_at  timestamp,
  PRIMARY KEY ((country, month), event_id)
) WITH default_time_to_live = 63072000;

-- Reference data (education programmes, workforce baseline)

CREATE TABLE IF NOT EXISTS education_programmes (
  country      text,
  programme_id text,
  name         text,
  institution  text,
  level        text,         -- 'bachelor', 'master', 'phd', 'other'
  ecsf_roles   set<text>,
  url          text,
  updated_at   timestamp,
  PRIMARY KEY (country, programme_id)
);

CREATE TABLE IF NOT EXISTS workforce_baseline (
  country        text,
  year           int,
  workforce_size int,
  workforce_gap  int,
  source         text,
  PRIMARY KEY (country, year)
) WITH CLUSTERING ORDER BY (year DESC);

-- Stream aggregates (written by the sink from agg.* topics; upserts, so replays are safe)

CREATE TABLE IF NOT EXISTS job_ads_by_day (
  day     date,
  country text,
  count   int,
  PRIMARY KEY (day, country)
);

CREATE TABLE IF NOT EXISTS demand_by_day (
  country text,
  day     date,
  kind    text,
  item    text,
  count   int,
  PRIMARY KEY ((country, day), kind, item)
);

CREATE TABLE IF NOT EXISTS demand_map (
  kind    text,
  item    text,
  day     date,
  country text,
  count   int,
  PRIMARY KEY ((kind, item, day), country)
);

CREATE TABLE IF NOT EXISTS demand_trend (
  country text,
  kind    text,
  item    text,
  day     date,
  count   int,
  PRIMARY KEY ((country, kind, item), day)
) WITH CLUSTERING ORDER BY (day ASC);

CREATE TABLE IF NOT EXISTS supply_by_day (
  country text,
  day     date,              -- Supply as of this day (365-day window)
  kind    text,
  item    text,
  count   int,
  PRIMARY KEY ((country, day), kind, item)
);

CREATE TABLE IF NOT EXISTS supply_map (
  kind    text,
  item    text,
  day     date,
  country text,
  count   int,
  PRIMARY KEY ((kind, item, day), country)
);

CREATE TABLE IF NOT EXISTS top10 (
  country    text,
  day        date,           -- last day of the 30-day window
  kind       text,           -- 'skill' or 'certification'
  rank       int,
  item       text,
  count      int,
  PRIMARY KEY ((country, day, kind), rank)
);

CREATE TABLE IF NOT EXISTS threats_by_day (
  day         date,
  country     text,
  threat_type text,
  count       int,
  PRIMARY KEY (day, country, threat_type)
);

CREATE TABLE IF NOT EXISTS threats_by_hour (
  country     text,
  day         date,
  hour        int,           -- 0-23, UTC
  threat_type text,
  count       int,
  PRIMARY KEY ((country, day), hour, threat_type)
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

CREATE TABLE IF NOT EXISTS emerging_skills (
  country    text,
  day        date,           -- last day of the 30-day window
  mention    text,           -- Unmapped Skill mention (S9)
  count      int,
  max_error  int,
  PRIMARY KEY ((country, day), mention)
);

CREATE TABLE IF NOT EXISTS companies_by_day (
  day           date,
  country       text,
  companies_day int,         -- HyperLogLog estimate (S10)
  companies_30d int,
  missing_company int,       -- Job ads with no company field (S10)
  PRIMARY KEY (day, country)
);

CREATE TABLE IF NOT EXISTS platform_analysis (
  platform    text,
  feature     text,
  supported   boolean,
  notes       text,
  analysed_at timestamp,
  PRIMARY KEY (platform, feature)
);

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
  country   text,
  month     text,
  skill     text,
  demand    int,
  supply    int,
  gap_ratio double,          -- demand / supply; null when supply < GAP_MIN_SUPPLY
  PRIMARY KEY ((country, month), skill)
);

CREATE TABLE IF NOT EXISTS role_relations (
  role         text,
  related_role text,
  overlap      double,       -- share of top skills in common, 0-1
  PRIMARY KEY (role, related_role)
);

CREATE TABLE IF NOT EXISTS orchestrator_decisions (
  day         date,
  created_at  timestamp,
  task_id     uuid,
  agent       text,
  action      text,
  params      text,          -- JSON
  reason      text,
  decided_by  text,          -- 'llm' or 'rules'
  PRIMARY KEY (day, created_at, task_id)
) WITH CLUSTERING ORDER BY (created_at DESC, task_id ASC);

-- Shared code state

CREATE TABLE IF NOT EXISTS llm_cache (
  task        text,          -- e.g. 'skill_extraction', 'programme_roles'
  input_hash  text,          -- hash of prompt version + input text
  provider    text,
  model       text,
  answer      text,          -- validated JSON answer
  cost_eur    double,
  created_at  timestamp,
  PRIMARY KEY ((task, input_hash))
);

CREATE TABLE IF NOT EXISTS replay_position (
  dataset     text,          -- e.g. 'de_job_postings', 'stackoverflow_survey', 'testdata_profiles'
  row_number  bigint,        -- last row published
  event_date  date,          -- event time of that row
  updated_at  timestamp,
  PRIMARY KEY (dataset)
);
