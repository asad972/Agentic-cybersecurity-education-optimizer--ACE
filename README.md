# ACE – Agentic Cybersecurity Education Optimizer

ACE collects cybersecurity job ads, professional profiles, education reports and threat data with separate AI agents, streams them through Kafka, stores them in Cassandra, and shows skills demand, skills supply and threats per country on choropleth maps and dashboards, similar to [CyberSeek](https://www.cyberseek.org).

Joint project for the **Open Source Technologies (OST)** and **Stream Mining (SM)** courses, supervised by Imre Lendak.

- Architecture and work plan: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- How we work (branches, pull requests): [CONTRIBUTING.md](CONTRIBUTING.md)

![ACE architecture](docs/images/architecture.png)

## Team

| Member | Area | Stream stages | Dashboard |
| --- | --- | --- | --- |
| M1 – _name_ | Data acquisition and AI agents | S1 near-duplicate filter, S2 skill extraction | Data pipeline health |
| M2 – _name_ | Kafka, Cassandra, deployment, integration | S3 Top 10 (Space-Saving), S4 need frequency (Count-Min) | Skills demand |
| M3 – _name_ | Batch forecasting and gap analysis | S5 offered frequency, S6 drift detection (ADWIN) | Supply, gap and forecast |
| M4 – _name_ | API and visualization | S7 threat frequency, S8 burst detection (Half-Space Trees) | Threat landscape |

## Repository layout

```
agents/          M1  collector agents, orchestrator, skill extraction (S2)
streaming/       all stream stages S1–S8, one folder per stage
storage/cql/     M2  Cassandra schema
batch/           M3  forecasting and gap analysis jobs
api/             M4  FastAPI service
dashboard/       M4  React app (one dashboard per member)
deploy/          M2  Kafka topic setup, Kubernetes manifests
shared/schemas/  all Kafka message contracts (JSON Schema)
docs/            architecture, report, slides
```

## Getting started

Requirements: Docker Desktop (with Compose v2) and Git.

```bash
git clone https://github.com/asad972/Agentic-cybersecurity-education-optimizer--ACE.git
cd Agentic-cybersecurity-education-optimizer--ACE
cp .env.example .env          # add your own API keys; never commit .env
docker compose up -d          # Kafka, Kafka UI, Cassandra + topics and schema
```

Once it is up:

| Service | Address |
| --- | --- |
| Kafka (from your machine) | `localhost:9092` |
| Kafka (from other containers) | `kafka:29092` |
| Kafka UI | http://localhost:8080 |
| Cassandra (CQL) | `localhost:9042`, keyspace `ace` |

Cassandra takes 1–2 minutes to start the first time. Check progress with `docker compose logs -f cassandra-init`.

Stop everything with `docker compose down`. Add `-v` to also delete the stored data.

## Key dates

| Date (2026) | Milestone |
| --- | --- |
| Oct 19 | Architecture and per-member work plan due |
| Nov 26, 12:00 CET | Early submission (secures December exam slots) |
| Nov 30 – Dec 4 | Early project defenses |
| Dec 6, 12:00 CET | Late submission deadline |
