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
pip install -e "shared/python[flink,llm,dev]"
python -m pytest shared/python/tests streaming
```

The `stages` extra pins River to **0.23.0**. Both `dev` and `flink` include
`ace[stages]`, so the existing setup and CI commands install it automatically;
no separate S8 requirements install is needed. Contracts-only installations
remain lightweight. For stage logic without Java, install
`pip install -e "shared/python[dev]"` and run `pytest streaming -m "not flink"`.

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

## LLM client (`ace.llm`, M3)

`ace.llm` is the only supported way to call a language model. It synchronously
selects Ollama/Qwen or Anthropic/Claude from `LLM_PROVIDER`, requires structured
Pydantic output, caches validated answers in Cassandra, retries temporary
failures, suppresses duplicate concurrent calls with a Cassandra lease, and
enforces one hosted-model budget across all agent processes per UTC day.

```python
from pydantic import BaseModel

from ace.llm import BudgetExceededError, LLMClient


class SkillExtraction(BaseModel):
    skills: list[str]
    certifications: list[str]
    ecsf_role: str | None = None


with LLMClient.from_env() as client:
    try:
        result = client.generate(
            task="skill_extraction",
            prompt_version="v1",
            system_prompt="Extract only cybersecurity taxonomy candidates.",
            prompt="Analyse this job advertisement: ...",
            response_model=SkillExtraction,
        )
        extraction = result.value
    except BudgetExceededError:
        # The caller owns its non-LLM fallback; the client never changes provider.
        extraction = None
```

M1's S2 agent and M5's education/platform agents create one client per process
and reuse it. They handle `BudgetExceededError` explicitly; the module never
silently changes provider. Cache hits remain available after the hosted budget
is exhausted.

Configuration is documented in `.env.example`. Hosted mode requires
`LLM_API_KEY`, `LLM_HOSTED_INPUT_EUR_PER_MTOK`, and
`LLM_HOSTED_OUTPUT_EUR_PER_MTOK`; client construction fails if any is missing.
Set the two prices to the provider's current rates converted to EUR per million
tokens. Model defaults are `qwen3:4b` and `claude-haiku-5-5`, and both are
configurable.

The UTC budget row can increase when a worker starts with a higher
`LLM_DAILY_BUDGET_EUR`. It never decreases during that UTC day; a lower limit
takes effect with the next day's row, while a worker still respects its own
lower configured cap.

Tests inject `InMemoryLLMStore` and `RecordedProvider`; production configuration
accepts only `local` or `hosted`. A missing recording fails immediately instead
of accessing the network. Public failures are typed as `LLMConfigurationError`,
`LLMProviderError`, `LLMResponseError`, `BudgetExceededError`, `CacheBusyError`,
and `RecordingNotFoundError`.

Install the LLM and test dependencies through the shared package metadata, then
run the offline suite from the repository root:

```bash
python -m pip install -e "shared/python[llm,dev]"
python -m pytest shared/python/tests -q
```

The ordinary test suite uses recorded or injected providers and requires no
Kafka, Cassandra, Ollama, Anthropic, or network access. The real Cassandra
contention and budget checks are optional integration tests:

```powershell
$env:ACE_CASSANDRA_INTEGRATION = "1"
python -m pytest shared/python/tests/test_llm_cassandra.py -q
$env:ACE_CASSANDRA_INTEGRATION = $null
```
