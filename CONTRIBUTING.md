# Contributing

## Branches

- `main` is protected: changes go in only through pull requests.
- Create one branch per task, prefixed with your member ID:
  `m1/job-agent`, `m2/s3-top10`, `m3/forecast`, `m4/threat-map`.

```bash
git checkout main && git pull
git checkout -b m2/s3-top10
# ...work, commit...
git push -u origin m2/s3-top10
```

Then open a pull request on GitHub and ask one teammate to review it.

## Shared contracts

These files are used by more than one member. Changing them can break someone else's code, so changes to them are agreed by the whole team. Mention all teammates on any pull request that touches them:

- `shared/schemas/` – Kafka message formats
- `storage/cql/schema.cql` – Cassandra tables
- `deploy/kafka/create-topics.sh` – topic names
- API endpoints listed in `docs/ARCHITECTURE.md`

## Rules

- Never commit secrets. API keys go in `.env`, which git ignores.
- Every service gets its own `Dockerfile` in its folder.
- Large datasets do not go in this repo; keep them in the data repo and document the download step.
- Write commit messages in the imperative mood: "Add S3 top-k stage", not "added stuff".
- Credit your own work in your pull requests; the course grades each member on what they did.
