# Contributing

## Branches

- `master` is protected: changes go in only through pull requests.
- Create one branch per task, prefixed with your member ID:
  `m1/job-agent`, `m2/s3-top10`, `m3/forecast`, `m4/threat-map`.

```bash
git checkout master && git pull
git checkout -b m2/s3-top10
# ...work, commit...
git push -u origin m2/s3-top10
```

Then open a pull request on GitHub and ask one teammate to review it.

## Shared contracts

These files are used by more than one member. Changing them can break someone else's code, so changes to them are agreed by the whole team. Mention all teammates on any pull request that touches them:

- `shared/schemas/` – Kafka message formats
- `shared/taxonomy/` – Skill Taxonomy (skills, certifications, ECSF roles)
- `storage/cql/schema.cql` – Cassandra tables
- `shared/schemas/topics.csv` – topic names, schemas and keys
- `shared/python/ace/` – shared code: stage base, contracts, skill matcher
- API endpoints listed in `docs/ARCHITECTURE.md`

Use the terms from `docs/ARCHITECTURE.md` (Demand, Supply, Skill, …) in code, topic and table names. Never "needed" or "offered".

## Rules

- Never commit secrets. API keys go in `.env`, which git ignores.
- Every service gets its own `Dockerfile` in its folder.
- Every stream stage uses the shared stage base and ships at least one test with fake records.
- Publish to Kafka only through the shared `ace` package, never with a hand-typed topic name.
- Call an LLM only through `ace.llm`, so answers are cached and the daily budget applies.
- Large datasets do not go in this repo; keep them in the data repo and document the download step.
- Write commit messages in the imperative mood: "Add S3 top-k stage", not "added stuff".
- Credit your own work in your pull requests; the course grades each member on what they did.
