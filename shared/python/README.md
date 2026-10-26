# Shared Python package `ace` (shared contract)

Code every member needs, installed in every Python container (agents, stream stages, sink, batch jobs, API). Owners fix bugs freely; changes to how it is used are agreed by the whole team.

| Module | What it does | Owner |
| --- | --- | --- |
| `ace.stage` | Stage base: consumes, checks message formats, applies event time and the grace period, emits once per window, publishes, counts late and invalid records. Windowed and per-record stages. Runs on Kafka or on an in-memory list of records for tests. | M2 |
| `ace.contracts` | Reads `shared/schemas/topics.csv`, validates every message before publishing, sends invalid ones to `errors.invalid`, and provides topic names. | M2 |
| `ace.taxonomy` | Skill matcher: maps a mention to a Skill, Certification or Role from `shared/taxonomy/` by exact name or alias, ignoring case; anything else is unmapped. | M1 |
| `ace.llm` | The only path to an LLM. Local open-source model (Qwen via Ollama) or hosted model (Claude Haiku 5.5), switched by `LLM_PROVIDER`, answers cached in the `llm_cache` table, retries, JSON answers checked, daily budget (`LLM_DAILY_BUDGET_EUR`). Recorded answers in tests. | M3 |
| `ace.replay` | Replays a dataset into Kafka in event-time order at `REPLAY_SECONDS_PER_DAY`, resuming from `replay_position` after a restart. Each agent supplies only the row → message mapping. | M1 |

Timeline:

- **Oct 22:** fake test data in `shared/testdata/` (about 200 job ads and 200 profiles, several countries, one JSON message per line), owner M1.
- **Oct 27:** the package, with S4 (`streaming/s4_demand_frequency`) as the first stage built on it.

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

Configuration is documented in `.env.example`. Hosted mode requires the API
key and current input/output prices expressed in EUR per million tokens. Model
defaults are `qwen3:4b` and `claude-haiku-5-5`, and both are configurable.

Tests inject `InMemoryLLMStore` and `RecordedProvider`; production configuration
accepts only `local` or `hosted`. A missing recording fails immediately instead
of accessing the network. Public failures are typed as `LLMConfigurationError`,
`LLMProviderError`, `LLMResponseError`, `BudgetExceededError`, `CacheBusyError`,
and `RecordingNotFoundError`.

Until M2 merges the shared package metadata, install the temporary dependency
manifest and run the offline suite from the repository root:

```bash
python -m pip install -r shared/python/requirements-llm.txt
PYTHONPATH=shared/python python -m pytest shared/python/tests -q
```
