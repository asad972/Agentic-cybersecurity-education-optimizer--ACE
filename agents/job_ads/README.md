# Job Ads Agent (`agents/job_ads`)

Replays cybersecurity job advertisement datasets onto Kafka (`raw.jobs`) in global event-time order using `ace.replay.EventReplayer` and validates every message through `ace.contracts`. Owner: M1.

## Features

- **Event-time replay & checkpointing:** Uses `ace.replay.EventReplayer` with `REPLAY_SECONDS_PER_DAY` pacing and resumes safely from Cassandra's `ace.replay_position` table (`CassandraPositionStore`).
- **Schema & geography normalization:** Maps both pre-formatted ACE JSONL files (`shared/testdata/jobs_sample.jsonl`) and external CSV/JSONL job datasets into `ace/raw_job`, normalizing ISO 3166-1 alpha-2 country codes (`UK` -> `GB`, `EL` -> `GR`) within the European Skills Scope (EU27, EEA, UK, CH).
- **Contract validation:** Routes every valid message to `raw.jobs` (keyed by `country`) and malformed/incomplete rows to `errors.invalid`.

## Running Locally

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m job_ads --input shared/testdata/jobs_sample.jsonl --seconds-per-day 0 --dry-run
```

To replay against the local `docker-compose` Kafka and Cassandra stack:

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m job_ads --input shared/testdata/jobs_sample.jsonl --bootstrap-servers localhost:9092 --cassandra-hosts localhost
```

## Running Tests

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m pytest agents/job_ads -v
```