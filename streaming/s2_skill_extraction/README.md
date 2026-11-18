# S2 Skill extraction (`s2_skill_extraction`)

`jobs.clean`, `raw.profiles` -> `skills.demand`, `skills.supply`. Maps job advertisements and professional profiles to canonical Skills, Certifications, and ECSF roles from `shared/taxonomy/` via `ace.taxonomy`. Owner: M1.

## How it works

1. **Taxonomy loading (`ace.taxonomy`):** Reads canonical or contribution CSVs from `shared/taxonomy/` (or `ACE_TAXONOMY_DIR`) and indexes every Skill, Certification, and ECSF role along with its case-insensitive aliases.
2. **Boundary-aware matching:** Scans `job_title` + `description` (for `jobs.clean`) or `headline` + `text` (for `raw.profiles`) for canonical names and aliases, infers the ECSF role from the title or matched skills, and records any unmapped candidate mentions.
3. **Schema validation:** Emits validated `skills.demand` and `skills.supply` messages and skips records with zero matched cybersecurity skills.

## Running Tests
```powershell
python -m pytest shared/python/tests/test_taxonomy.py streaming/s2_skill_extraction -m "not flink"
```