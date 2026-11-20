# S2 Skill extraction (`s2_skill_extraction`)

`jobs.clean`, `raw.profiles` -> `skills.demand`, `skills.supply`. Maps job advertisements and professional profiles to canonical Skills, Certifications, and ECSF roles from `shared/taxonomy/` via `ace.taxonomy` and `ace.llm`. Owner: M1.

## How it works

1. **Taxonomy baseline (`ace.taxonomy`):** Reads canonical or contribution CSVs from `shared/taxonomy/` (or `ACE_TAXONOMY_DIR`) and performs boundary-aware matching across `job_title` + `description` (`jobs.clean`) or `headline` + `text` (`raw.profiles`).
2. **LLM extraction (`ace.llm`):** When enabled (`llm_client`, `use_llm=True`, or `ACE_S2_USE_LLM=1`), `LLMSkillExtractor` calls `LLMClient.generate(task="skill_extraction", prompt_version="v1", response_model=ExtractedMentions)` with Cassandra caching, claim leases, and budget enforcement.
3. **Closed-taxonomy grounding & emerging candidates:** Maps LLM-extracted mentions against `ace.taxonomy` and forwards novel unmapped terms in `unmapped` (incrementing `unmapped_skills_total`) so S9 (`agg.emerging_skills`) can detect emerging skills. If the LLM raises an `LLMError` (such as budget exhaustion), S2 degrades gracefully to deterministic taxonomy matching.
4. **Schema validation:** Emits validated `skills.demand` and `skills.supply` messages and skips records with zero matched cybersecurity skills.

## Running Tests
```powershell
pytest streaming/s2_skill_extraction -m "not flink"