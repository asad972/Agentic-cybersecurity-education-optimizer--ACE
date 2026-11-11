# ACE monthly Demand forecast

This Python 3.11 batch job reads daily canonical Skill Demand from Cassandra,
builds complete monthly country and `EU` series, and upserts twelve forecast
months into the existing `forecasts` table. `EU` is the sum of the fixed
32-country Skills Scope (EU27, Iceland, Liechtenstein, Norway, `GB`, and
Switzerland); it is a Region, not a country code.

## Data policy

- The current incomplete UTC month is excluded.
- Missing months between a series' first and last observation are zero-filled.
- Countries without a Skill's history contribute zero to that Skill's EU sum.
- Fewer than 15 months are skipped. From 15 through 26 months the job uses a
  seasonal-naive model. At 27 months it begins comparing SARIMA and LightGBM.
- Advanced candidates are evaluated on the final three known months. The lower
  MAE wins and SARIMA wins an exact tie. MAPE excludes zero actuals.
- SARIMA is `(1,1,1) × (1,0,0,12)`. LightGBM uses fixed lag, rolling, calendar,
  and time features with deterministic settings. A failed candidate does not
  hide a working candidate; seasonal naive is the final fallback.
- Forecasts and lower bounds are clamped to zero, and every 95% interval obeys
  `lower <= predicted <= upper`.

The taxonomy loader prefers `shared/taxonomy/skills.csv`. Until M1 publishes
that canonical file, it loads `shared/taxonomy/contributions/*_skills.csv` and
rejects case-insensitive canonical-name collisions.

## Run and test

```bash
python -m pip install -e "batch/forecast[dev]"
python -m pytest -q batch/forecast/tests
python -m ace_forecast --as-of 2026-11-01
```

`--as-of` is optional and defaults to the current UTC date. Configuration uses
the same variables as the API and sink:

- `CASSANDRA_HOSTS` (comma-separated, default `localhost`)
- `CASSANDRA_PORT` (default `9042`)
- `CASSANDRA_KEYSPACE` (default `ace`)
- `CASSANDRA_LOCAL_DC` (default `datacenter1`)
- `CASSANDRA_USERNAME` and `CASSANDRA_PASSWORD` (both optional, but paired)
- `ACE_TAXONOMY_DIR` (default is the repository's `shared/taxonomy`)

Each series emits structured JSON for its history length, selection or skip
reason, MAE and MAPE. A final summary contains input-series, forecast, skip, and
row counts. Empty input is a successful no-op with a warning. Cassandra read or
write failures fail the job. Prepared upserts make reruns idempotent, and one
UTC `trained_at` timestamp is shared by the entire run.

The normal tests use injected repositories. The optional live smoke test is:

```powershell
$env:ACE_CASSANDRA_INTEGRATION = "1"
python -m pytest -q -m cassandra batch/forecast/tests
$env:ACE_CASSANDRA_INTEGRATION = $null
```

## Kubernetes

The CronJob runs every Monday at 02:00 UTC. A drift alert can request the same
image and CLI as an ad-hoc job without adding a Kafka consumer:

```bash
kubectl create job --from=cronjob/ace-demand-forecast ace-demand-forecast-drift
```
