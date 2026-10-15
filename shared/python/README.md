# Shared Python package `ace` (shared contract)

Code every member needs, installed in every Python container (agents, stream stages, sink, batch jobs, API). Owners fix bugs freely; changes to how it is used are agreed by the whole team.

| Module | What it does | Owner |
| --- | --- | --- |
| `ace.stage` | Stage base: consumes, checks message formats, applies event time and the grace period, emits once per window, publishes, counts late and invalid records. Windowed and per-record stages. Runs on Kafka or on an in-memory list of records for tests. | M2 |
| `ace.contracts` | Reads `shared/schemas/topics.csv`, validates every message before publishing, sends invalid ones to `errors.invalid`, and provides topic names. | M2 |
| `ace.taxonomy` | Skill matcher: maps a mention to a Skill, Certification or Role from `shared/taxonomy/` by exact name or alias, ignoring case; anything else is unmapped. | M1 |
| `ace.llm` | The only path to an LLM. Local open-source model or hosted model (`LLM_PROVIDER`), answers cached in the `llm_cache` table, retries, JSON answers checked, daily budget (`LLM_DAILY_BUDGET_EUR`). Recorded answers in tests. | M3 |
| `ace.replay` | Replays a dataset into Kafka in event-time order at `REPLAY_SECONDS_PER_DAY`, resuming from `replay_position` after a restart. Each agent supplies only the row → message mapping. | M1 |

Timeline:

- **Oct 22:** fake test data in `shared/testdata/` (about 200 job ads and 200 profiles, several countries, one JSON message per line), owner M1.
- **Oct 27:** the package, with S4 (`streaming/s4_demand_frequency`) as the first stage built on it.

Rules for everyone:

- Publish to Kafka only through `ace.contracts`, never with a hand-typed topic name.
- Call an LLM only through `ace.llm`.
- Every stream stage uses `ace.stage` and ships at least one test with fake records.
