# ACE monthly Skills Gap

This Python 3.11 batch job combines one complete calendar month of canonical
Skill Demand with the latest available S5 rolling Supply snapshot for each
country. It writes the existing `skills_gap` and `role_relations` Cassandra
tables; it does not change shared schemas or stream contracts.

## Gap policy

- The default target is the previous complete UTC month; `--month YYYY-MM`
  supports deterministic backfills.
- Demand is summed from every `demand_by_day` partition in the target month.
- Supply is the latest `supply_by_day` snapshot at or before month end, no more
  than 365 inclusive days old. This follows S5's quiet-day snapshot policy.
- A country with Skill Demand or a qualifying Supply snapshot receives one row
  for every canonical Skill. Unobserved Demand and Supply are zero.
- `gap_ratio = demand / supply` only when Supply is at least
  `GAP_MIN_SUPPLY`; otherwise it is null so consumers can show “not enough
  supply data.”
- `EU` is not a country and is never written to `skills_gap`.

The fixed scope is EU27, Iceland, Liechtenstein, Norway, `GB`, and Switzerland.
Canonical Skills and ECSF mappings come from `shared/taxonomy/skills.csv` and
`ecsf_roles.csv`. Contribution files are used until that canonical pair lands.
Unknown historical Skills are skipped. One structured warning reports their
aggregate Demand and Supply row counts without logging names or payloads.

## ECSF role relations

The job sums monthly Demand across the fixed scope. For each ECSF role it ranks
mapped, positive-Demand Skills by count and canonical name and keeps ten. Every
ordered pair of distinct roles is written with Jaccard similarity. Zero-overlap
pairs are retained, which makes reruns overwrite earlier results consistently.
If the month has no Demand input, existing role relationships are left intact.

## Run and test

```bash
python -m pip install -e "batch/gap[dev]"
python -m pytest -q batch/gap/tests
python -m ace_gap --month 2026-10
```

Configuration:

- `CASSANDRA_HOSTS` (comma-separated, default `localhost`)
- `CASSANDRA_PORT` (default `9042`)
- `CASSANDRA_KEYSPACE` (default `ace`)
- `CASSANDRA_LOCAL_DC` (default `datacenter1`)
- `CASSANDRA_USERNAME` and `CASSANDRA_PASSWORD` (optional, but paired)
- `GAP_MIN_SUPPLY` (default `20`)
- `GAP_SUPPLY_LOOKBACK_DAYS` (default and maximum `365`)
- `ACE_TAXONOMY_DIR` (default `shared/taxonomy`)

Logs are structured JSON and contain only coverage, snapshot dates, row counts,
and summary decisions. Empty input is a successful no-op. Cassandra failures,
invalid configuration, and invalid known-Skill counts fail the job.

The CronJob reads optional Cassandra credentials from Secret
`ace-cassandra-credentials`, keys `username` and `password`. When authentication
is enabled, the Secret must contain both keys; when the Secret is absent, both
environment variables remain unset.

Late input can be repaired safely by rerunning the completed month, for example
`python -m ace_gap --month 2026-10`. Writes are row-by-row, so a crash can leave
a month partially written, but prepared upserts are idempotent and the same
rerun repairs it.

The optional live smoke test is:

```powershell
$env:ACE_CASSANDRA_INTEGRATION = "1"
python -m pytest -q -m cassandra batch/gap/tests
$env:ACE_CASSANDRA_INTEGRATION = $null
```

The Kubernetes CronJob runs at 03:00 UTC on the first day of each month.
