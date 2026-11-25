# Profile Agent (agents/profiles)

Replays anonymised practitioner profile datasets (such as the Stack Overflow Developer Survey) onto Kafka (raw.profiles) in global event-time order using ace.replay.EventReplayer and validates every message through ace.contracts. Owner: M1.

## Features

- Event-time replay & checkpointing: Uses ace.replay.EventReplayer with REPLAY_SECONDS_PER_DAY pacing and resumes from Cassandra's ace.replay_position table (CassandraPositionStore).
- Anonymisation & schema normalization: Maps pre-formatted ACE JSONL files (shared/testdata/profiles_sample.jsonl) and Stack Overflow Developer Survey CSV exports into ace/raw_profile, stripping personal identifiers (names, emails, compensation fields) and generating deterministic UUIDv5 event_id keys.
- European Skills Scope & security filtering: Normalizes country names (e.g. United Kingdom -> GB, Germany -> DE) to ISO 3166-1 alpha-2 codes within EU27, EEA, UK, and CH, and filters Stack Overflow survey rows to security-relevant roles and skills by default.
- Contract validation: Routes every valid message to raw.profiles (keyed by country) and sends malformed records to errors.invalid.

## Running Locally (Sample Dataset)

    $env:PYTHONPATH = "shared/python;agents"
    python -m profiles --input shared/testdata/profiles_sample.jsonl --seconds-per-day 0 --dry-run

## Running with Stack Overflow Developer Survey CSVs

Keep large survey CSVs outside the repository (e.g. data/stackoverflow/survey_results_public_2025.csv):

    $env:PYTHONPATH = "shared/python;agents"
    python -m profiles --input data/stackoverflow/survey_results_public_2025.csv --dataset stackoverflow_2025 --default-profile-date 2025-06-01 --seconds-per-day 0 --dry-run

## Running Tests

    $env:PYTHONPATH = "shared/python;agents"
    python -m pytest agents/profiles -v
