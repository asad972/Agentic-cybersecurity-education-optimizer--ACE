# Job Ads Agent (`agents/job_ads`)

Replays cybersecurity job advertisement datasets onto Kafka (`raw.jobs`) in global event-time order using `ace.replay.EventReplayer` and validates every message through `ace.contracts`. Owner: M1.

## Features

- **Event-time replay & checkpointing:** Uses `ace.replay.EventReplayer` with `REPLAY_SECONDS_PER_DAY` pacing and resumes safely from Cassandra's `ace.replay_position` table (`CassandraPositionStore`).
- **Schema & geography normalization:** Maps pre-formatted ACE JSONL files (`shared/testdata/jobs_sample.jsonl`), CSV files, and Hugging Face Parquet datasets (`mischeiwiller/german-job-postings`) into `ace/raw_job`, normalizing ISO 3166-1 alpha-2 country codes (`UK` -> `GB`, `EL` -> `GR`, NUTS prefixes such as `DEF` -> `DE`, and DACH region names such as `Wien` -> `AT` and `Zürich` -> `CH`) within the European Skills Scope (EU27, EEA, UK, CH).
- **Synthesized ESCO descriptions & provenance tagging:** None of the rows in `mischeiwiller/german-job-postings` contain free-text `description_derived` values (`null` across all 70,584 rows), so descriptions are built from `esco_occupation` and `esco_skills` tags and marked via `source` (e.g. `bundesagentur:esco_tags`) so downstream stages and analytics can distinguish them from full-text job descriptions.
- **Contract validation & skipped-row logging:** Routes every valid message to `raw.jobs` (keyed by `country`), sends malformed/incomplete rows to `errors.invalid`, and logs the total count of skipped rows (outside the European Skills Scope or missing country/region metadata) at the end of each replay.

## Running Locally (Sample Dataset)

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m job_ads --input shared/testdata/jobs_sample.jsonl --seconds-per-day 0 --dry-run
```

## Real Dataset (`mischeiwiller/german-job-postings`, 2015–2026)

Per `CONTRIBUTING.md`, large datasets are kept outside this repository. Download the Parquet split from Hugging Face into a local directory (for example `data/german-job-postings/`):

```powershell
New-Item -ItemType Directory -Force -Path data/german-job-postings | Out-Null
Invoke-WebRequest `
  -Uri "[https://huggingface.co/api/datasets/mischeiwiller/german-job-postings/parquet/default/train/0.parquet](https://huggingface.co/api/datasets/mischeiwiller/german-job-postings/parquet/default/train/0.parquet)" `
  -OutFile "data/german-job-postings/0.parquet"
```

Verify the full dataset (70,584 rows) with a dry-run (`nuts_code` maps German postings to `DE`, `region` maps 355 Austrian postings to `AT` and 2 Swiss postings to `CH`, and 116 rows with missing/unknown region are skipped):

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m job_ads `
  --input data/german-job-postings/0.parquet `
  --dataset german_job_postings `
  --seconds-per-day 0 `
  --dry-run
```

To replay into the local `docker-compose` Kafka and Cassandra stack, set `REPLAY_SECONDS_PER_DAY` (at the default `5.0` s/day, ~11 years of history takes about **5.5 hours**, whereas `0.05` s/day replays the full dataset in about **3.3 minutes**):

```powershell
$env:PYTHONPATH = "shared/python;agents"
$env:REPLAY_SECONDS_PER_DAY = "0.05"
python -m job_ads `
  --input data/german-job-postings/0.parquet `
  --dataset german_job_postings `
  --bootstrap-servers localhost:9092 `
  --cassandra-hosts localhost
```

## Running Tests

```powershell
$env:PYTHONPATH = "shared/python;agents"
python -m pytest agents/job_ads -v
```