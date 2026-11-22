# S2 Skill extraction (`s2_skill_extraction`)

`jobs.clean`, `raw.profiles` -> `skills.demand`, `skills.supply`. Maps job advertisements and professional profiles to canonical Skills, Certifications, and ECSF roles from `shared/taxonomy/` via `ace.taxonomy` and `ace.llm`. Owner: M1.

## How it works

1. **Taxonomy baseline (`ace.taxonomy`):** Reads canonical or contribution CSVs from `shared/taxonomy/` (or `ACE_TAXONOMY_DIR`) and performs boundary-aware matching across `job_title` + `description` (`jobs.clean`) or `headline` + `text` (`raw.profiles`).
2. **LLM extraction (`ace.llm`):** Disabled by default (`ACE_S2_USE_LLM=0`) so deterministic runs work without an LLM backend. When enabled (`ACE_S2_USE_LLM=1`, `use_llm=True`, or `llm_client`), `S2SkillExtractionStage` instantiates `LLMSkillExtractor.from_env()` inside `open()` (keeping the stage picklable for Flink) and calls `LLMClient.generate(task="skill_extraction", prompt_version="v2", response_model=ExtractedMentions)` with Cassandra caching, claim leases, and budget enforcement.
3. **Grounding, bounds & graceful fallback:** Record text is wrapped in `<record_data>` tags as untrusted input data. LLM-returned mentions are capped (`MAX_MENTIONS = 25`, `MAX_MENTION_LENGTH = 80`), verified to appear in the input text, and mapped against `ace.taxonomy`. Novel terms are emitted in `unmapped` (incrementing `unmapped_skills_total`) for S9 (`agg.emerging_skills`). Note: `skills.demand` and `skills.supply` schemas currently require `skills` `minItems >= 1`, so records with zero canonical skills are not emitted. If LLM initialization in `open()` fails (e.g., Cassandra unreachable or missing `LLM_API_KEY`) or `generate()` raises `LLMError`, S2 logs a warning, increments `llm_fallbacks_total`, and continues with deterministic taxonomy matching so Flink tasks do not enter a restart loop.

## Deployment Environment Variables

Set these variables on the Flink TaskManager / S2 runner environment when enabling LLM extraction:

| Variable | Default | Description |
| --- | --- | --- |
| `ACE_S2_USE_LLM` | `0` (off) | Set to `1` (or `true`) to enable `LLMSkillExtractor` in `S2SkillExtractionStage.open()`. |
| `LLM_PROVIDER` | `local` | `local` (Ollama) or `hosted` (Anthropic-compatible API). |
| `LLM_LOCAL_URL` | `http://localhost:11434` | Ollama base URL when `LLM_PROVIDER=local` (e.g. `http://ollama:11434` in Docker/K8s). |
| `LLM_LOCAL_MODEL` | `qwen3:4b` | Local Ollama model tag. |
| `LLM_HOSTED_MODEL` | `claude-haiku-5-5` | Hosted model name when `LLM_PROVIDER=hosted`. |
| `LLM_API_KEY` | *(required if hosted)* | API key for hosted LLM calls. |
| `LLM_HOSTED_INPUT_EUR_PER_MTOK` | *(required if hosted)* | Input token rate in EUR per 1M tokens. |
| `LLM_HOSTED_OUTPUT_EUR_PER_MTOK` | *(required if hosted)* | Output token rate in EUR per 1M tokens. |
| `LLM_DAILY_BUDGET_EUR` | `2` | Shared UTC daily spend cap in EUR across all tasks. |
| `LLM_TIMEOUT_SECONDS` | `60` | Per-attempt provider timeout in seconds. |
| `LLM_MAX_ATTEMPTS` | `3` | Maximum retry attempts per LLM request. |
| `LLM_MAX_TOKENS` | `2048` | Upper output-token cap (`LLMSkillExtractor` requests `512` tokens). |
| `CASSANDRA_HOSTS` | `localhost` | Comma-separated Cassandra contact points for `llm_cache` (e.g. `cassandra`). |
| `CASSANDRA_PORT` | `9042` | Cassandra native transport port. |
| `CASSANDRA_KEYSPACE` | `ace` | Cassandra keyspace containing `llm_cache`. |
| `CASSANDRA_USERNAME` | *(optional)* | Plain-text authentication username for Cassandra. |
| `CASSANDRA_PASSWORD` | *(optional)* | Plain-text authentication password for Cassandra. |

## Re-recording Offline LLM Fixtures

`streaming/s2_skill_extraction/tests/recordings/s2_skill_extraction.json` stores exact-hash responses keyed by `ace.llm.request_hash(...)`. If `SYSTEM_PROMPT`, `build_extraction_prompt()`, `ExtractedMentions`, or the sample inputs change, regenerate the fixture against a running local Ollama instance (`ollama pull qwen3:4b`):

```powershell
$env:PYTHONPATH = "shared/python;streaming"
python -c "
import json
from pathlib import Path
from ace.llm import LLMClient, InMemoryLLMStore, request_hash
from s2_skill_extraction.llm_extractor import (
    DEFAULT_MAX_TOKENS, ExtractedMentions, PROMPT_VERSION, SYSTEM_PROMPT, TASK_NAME, build_extraction_prompt
)
from s2_skill_extraction.tests.test_s2 import _sample_inputs

client = LLMClient.from_env(environ={'LLM_PROVIDER': 'local', 'LLM_LOCAL_MODEL': 'qwen3:4b'}, store=InMemoryLLMStore())
recordings = {}
for topic, rec in _sample_inputs():
    kind = 'job_ad' if topic == 'jobs.clean' else 'profile'
    title = rec.get('job_title') or rec.get('headline') or ''
    text = rec.get('description') or rec.get('text') or ''
    prompt = build_extraction_prompt(kind, title=title, text=text)
    digest = request_hash(
        task=TASK_NAME, prompt_version=PROMPT_VERSION, provider='local', model='qwen3:4b',
        system_prompt=SYSTEM_PROMPT, prompt=prompt, response_model=ExtractedMentions, max_tokens=DEFAULT_MAX_TOKENS
    )
    res = client.generate(
        task=TASK_NAME, prompt_version=PROMPT_VERSION, system_prompt=SYSTEM_PROMPT,
        prompt=prompt, response_model=ExtractedMentions, max_tokens=DEFAULT_MAX_TOKENS
    )
    recordings[digest] = {
        'answer': res.value.model_dump(),
        'input_tokens': res.input_tokens or 0,
        'output_tokens': res.output_tokens or 0,
    }
Path('streaming/s2_skill_extraction/tests/recordings/s2_skill_extraction.json').write_text(
    json.dumps({'recordings': recordings}, indent=2) + '\n', encoding='utf-8'
)