# S2 Skill extraction (`s2_skill_extraction`)

`jobs.clean`, `raw.profiles` -> `skills.demand`, `skills.supply`. Maps job advertisements and professional profiles to canonical Skills, Certifications, and ECSF roles from `shared/taxonomy/` via `ace.taxonomy` and `ace.llm`. Owner: M1.

## How it works

1. **Taxonomy baseline (`ace.taxonomy`):** Reads canonical or contribution CSVs from `shared/taxonomy/` (or `ACE_TAXONOMY_DIR`) and performs boundary-aware matching across `job_title` + `description` (`jobs.clean`) or `headline` + `text` (`raw.profiles`).
2. **LLM extraction (`ace.llm`):** Disabled by default (`ACE_S2_USE_LLM=0`) so deterministic runs work without an LLM backend. When enabled (`ACE_S2_USE_LLM=1`, `use_llm=True`, or `llm_client`), `S2SkillExtractionStage` instantiates `LLMSkillExtractor.from_env()` inside `open()` (keeping the stage picklable for Flink) and calls `LLMClient.generate(task="skill_extraction", prompt_version="v1", response_model=ExtractedMentions)` with Cassandra caching, claim leases, and budget enforcement.
3. **Closed-taxonomy grounding & emerging candidates:** Maps LLM-extracted mentions against `ace.taxonomy` and forwards novel unmapped terms in `unmapped` (incrementing `unmapped_skills_total`) so S9 (`agg.emerging_skills`) can detect emerging skills. If the LLM raises an `LLMError` (such as budget exhaustion), S2 degrades gracefully to deterministic taxonomy matching.
4. **Schema validation:** Emits validated `skills.demand` and `skills.supply` messages and skips records with zero matched cybersecurity skills.

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
| `CASSANDRA_HOSTS` | `localhost` | Comma-separated Cassandra contact points for `llm_cache` (e.g. `cassandra`). |
| `CASSANDRA_PORT` | `9042` | Cassandra native transport port. |
| `CASSANDRA_KEYSPACE` | `ace` | Cassandra keyspace containing `llm_cache`. |

## Running Tests

```powershell
$env:PYTHONPATH = "shared/python;streaming"
pytest streaming/s2_skill_extraction -m "not flink" -v
```