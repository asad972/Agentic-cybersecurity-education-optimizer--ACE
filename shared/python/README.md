# Shared Python package `ace` (shared contract)

Code every member needs, installed in every Python container (agents, stream stages, sink, batch jobs, API). Owners fix bugs freely; changes to how it is used are agreed by the whole team.

| Module | What it does | Owner |
| --- | --- | --- |
| `ace.stage` | Stage base: runs a stage as a PyFlink job; consumes, checks message formats, applies event time and the grace period, emits once per window, publishes, counts late and invalid records. Windowed and per-record stages. Runs on Kafka or on an in-memory list of records for tests. | M2 |
| `ace.contracts` | Reads `shared/schemas/topics.csv`, validates every message before publishing, sends invalid ones to `errors.invalid`, and provides topic names. | M2 |
| `ace.taxonomy` | Skill matcher: maps a mention to a Skill, Certification or Role from `shared/taxonomy/` by exact name or alias, ignoring case; anything else is unmapped. | M1 |
| `ace.llm` | The only path to an LLM. Local open-source model (Qwen via Ollama) or hosted model (Claude Haiku 5.5), switched by `LLM_PROVIDER`, answers cached in the `llm_cache` table, retries, JSON answers checked, daily budget (`LLM_DAILY_BUDGET_EUR`). Recorded answers in tests. | M3 |
| `ace.replay` | Replays a dataset into Kafka in event-time order at `REPLAY_SECONDS_PER_DAY`, resuming from `replay_position` after a restart. Each agent supplies only the row → message mapping. | M1 |

Timeline:

- **Oct 22:** fake test data in `shared/testdata/` (about 200 job ads and 200 profiles, several countries, one JSON message per line), owner M1.
- **Oct 27:** the package, with S4 (`streaming/s4_demand_frequency`) as the first stage built on it.

## Setup

Stream stages run on Apache Flink with its Python API (PyFlink 2.3), which needs Python 3.11 and Java 11 or 17.

```powershell
py -3.11 -m venv C:\venvs\ace          # outside the repo: see the Windows note
C:\venvs\ace\Scripts\Activate.ps1
pip install -e "shared/python[flink,dev]"
python -m pytest shared/python/tests streaming
```

**Windows:** PyFlink starts its Python workers through a batch file that breaks when the virtual environment's path has spaces or brackets, as our repo folder name does. Keep the virtual environment in a folder without them, and don't put the repo in `PYTHONPATH`; `pip install -e` is enough. `ace.stage` stops with a clear message if either is wrong. Linux containers are not affected.

## Writing a stage

Subclass `WindowedStage` (counts per Country and window) or `RecordStage` (one record at a time) from `ace.stage` and fill in only the algorithm; [`streaming/s4_demand_frequency`](../../streaming/s4_demand_frequency/) is the example.

- Test with `run_local(stage, records)` on a list of fake records. It follows Flink's event-time rules exactly (watermark = latest event time − grace period), so late records can be tested too.
- Add one test marked `@pytest.mark.flink` that runs the same records through `run_flink_local` and compares the result. Flink only moves its watermark every 200 ms, so that test uses on-time records only.
- Window state and the per-key `memory` dict must be picklable: Flink saves them in checkpoints. `run_local` fails if they are not.

Rules for everyone:

- Publish to Kafka only through `ace.contracts`, never with a hand-typed topic name.
- Call an LLM only through `ace.llm`.
- Every stream stage uses `ace.stage` and ships at least one test with fake records.
