# S2 Skill extraction (`s2_skill_extraction`)

`jobs.clean`, `raw.profiles` -> `skills.demand`, `skills.supply`. Maps job advertisements and professional profiles to canonical Skills, Certifications, and ECSF roles from `shared/taxonomy/` via `ace.taxonomy`. Owner: M1.

## Scope of this version

This initial version matches mentions by **exact name and alias** against `ace.taxonomy` only: there is no LLM extractor integrated yet, so `unmapped` stays empty (`[]`) and S9 receives no emerging skill candidates until the follow-up `ace.llm` extractor PR is added.

## How it works

1. **Taxonomy loading (`ace.taxonomy`):** Reads canonical or contribution CSVs from `shared/taxonomy/` (or `ACE_TAXONOMY_DIR`) and indexes every Skill, Certification, and ECSF role along with its case-insensitive aliases.
2. **Boundary-aware matching:** Scans `job_title` + `description` (for `jobs.clean`) or `headline` + `text` (for `raw.profiles`) for canonical names and aliases, infers the ECSF role from the title or matched skills, and increments `unmapped_skills_total` whenever candidate unmapped mentions are supplied.
3. **Schema validation:** Emits validated `skills.demand` and `skills.supply` messages and skips records with zero matched cybersecurity skills.

## Running Tests
```powershell
python -m pytest streaming/s2_skill_extraction -m "not flink"
```