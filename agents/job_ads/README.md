# Job Ads Agent (`agents/job_ads`)

Replays cybersecurity job advertisement datasets onto Kafka (`raw.jobs`) in global event-time order using `ace.replay.EventReplayer` and validates every message through `ace.contracts`. Owner: M1.

## Features

- **Event-time replay & checkpointing:** Uses `ace.replay.EventReplayer` with `REPLAY_SECONDS_PER_DAY` pacing and resumes safely from Cassandra's `ace.replay_position` table (`CassandraPositionStore`).
- **Schema & geography normalization:** Maps pre-formatted ACE JSONL files (`shared/testdata/jobs_sample.jsonl`), CSV files, and Hugging Face Parquet datasets (`mischeiwiller/german-job-postings`) into `ace/raw_job`, normalizing ISO 3166-1 alpha-2 country codes (`UK` -> `GB`, `EL` -> `GR`, NUTS prefixes such as `DEF` -> `DE`) within the European Skills Scope (EU27, EEA, UK, CH).
- **Contract validation & skipped-row logging:** Routes every valid message to `raw.jobs` (keyed by `country`), sends malformed/incomplete rows to `errors.invalid`, and logs the total count of skipped out-of-scope rows at the end of each replay.

## Running Locally (Sample Dataset)

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m job_ads --input shared/testdata/jobs_sample.jsonl --seconds-per-day 0 --dry-run