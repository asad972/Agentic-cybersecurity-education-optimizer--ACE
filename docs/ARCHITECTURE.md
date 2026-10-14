# ACE – System Architecture

Last updated: Oct 14, 2026

## Overview

ACE is an agentic, event-driven platform that collects cybersecurity job ads, professional profiles, education reports and threat data, then shows skills supply, demand and threats per country on maps and dashboards, similar to [CyberSeek](https://www.cyberseek.org).

The system has six layers. Each layer is a separate containerized service, and the layers connect only through Kafka topics, Cassandra tables or the REST API:

1. **Agent layer**: separate AI agents collect raw data and extract normalized skills.
2. **Ingestion layer**: Kafka carries all events in real time.
3. **Stream layer**: stream-mining pipelines compute Top 10 lists and frequency counts per country.
4. **Storage layer**: Cassandra stores raw records, aggregates and forecasts.
5. **Batch layer**: a forecasting model predicts skills-need trends per country and for Europe.
6. **Serving layer**: an API and a web dashboard with choropleth maps and time scroll-back.

**Mandatory constraints from the brief:** Kafka for real-time ingest, Cassandra for storage, Docker Swarm or Kubernetes for deployment, and no Apache Spark for analysis.

## Course requirements coverage

ACE meets every OST & SM minimum requirement, and goes beyond it in several places to aim for a higher grade.

| Requirement (minimum) | ACE design |
| --- | --- |
| 2 input datasets | 4 sources: LinkedIn job ads, LinkedIn profiles, CYBERHEAD + ISC2 reports, threat-intelligence feeds |
| 1 streaming system | Apache Kafka |
| 2 stream processing stages per member | 8 stages (S1–S8), 2 per member, each based on a published stream-mining algorithm |
| 1 data storage technology | Apache Cassandra (raw data and aggregates) |
| 1 batch stage on stored data | Skills-need forecasting (Batch 1), plus skills gap analysis |
| 1 visualization; 1 dashboard per member with ≥ 2 graphs | 4 dashboards, one per member, with 2–3 graphs each, including choropleth maps |
| Deployment on Docker containers | One container per component on Kubernetes (deployment diagram below) |
| Research papers for higher grades | Space-Saving, Count-Min Sketch, MinHash LSH, ADWIN, Half-Space Trees (see References) |

## Architecture at a glance

![ACE architecture: 6 layers in one cluster](images/architecture.png)

Data flows top to bottom: agents publish to Kafka, pipelines aggregate into Cassandra, batch jobs read history and write forecasts back, and the API serves everything to the dashboard.

## Data sources and agent layer

Each sub-task runs as its own AI agent in its own container. An orchestrator schedules the agents and retries failures. Every agent publishes to Kafka and never writes to Cassandra directly.

| Agent | Input source | Output topic | What it does |
| --- | --- | --- | --- |
| Job ads collector | LinkedIn job ads (public datasets such as Kaggle LinkedIn job postings), job APIs | `raw.jobs` | Fetches ads and tags country, date and title |
| Profile collector | LinkedIn professional profiles (public or anonymized datasets) | `raw.profiles` | Fetches profiles and strips personal identifiers |
| Reports agent | CYBERHEAD database, ISC2 annual workforce reports | `raw.reports` | Parses PDFs and tables into workforce and education figures |
| Threat collector | Public threat-intelligence feeds (e.g. AlienVault OTX, AbuseIPDB) | `threats.events` | Collects threat events and geolocates them to a country |
| Skill extraction agent | `raw.jobs`, `raw.profiles` | `skills.needed`, `skills.offered` | LLM reads the text and returns skills mapped to the shared taxonomy |
| Platform analysis agent | CyberSeek and similar platforms | `analysis.platforms` | Compares platform features for the higher-grade analysis |

**Skill taxonomy:** every skill is normalized to the [European Cybersecurity Skills Framework (ECSF)](https://www.enisa.europa.eu/topics/education/european-cybersecurity-skills-framework), with NICE as a secondary mapping. A shared taxonomy is what lets needed and offered skills be compared.

**Orchestration:** LangGraph or CrewAI, in Python. Each agent is a separate process, so it can be scaled and restarted on its own.

## Ingestion layer (Kafka)

Kafka is the only path into the system. Messages are JSON, validated against schemas kept in [`shared/schemas/`](../shared/schemas/). Topics are keyed by country code, so all events for one country land in the same partition and stay in order.

| Topic | Producer | Consumers | Key |
| --- | --- | --- | --- |
| `raw.jobs` | Job ads collector | S1, raw sink | country |
| `raw.profiles` | Profile collector | S2, raw sink | country |
| `raw.reports` | Reports agent | Raw sink | source |
| `jobs.clean` | S1 | S2 | country |
| `skills.needed` | S2 | S3, S4 | country |
| `skills.offered` | S2 | S5 | country |
| `threats.events` | Threat collector | S7, S8 | country |
| `agg.*` | S3, S4, S5, S7 | Cassandra sink, S6 | country |
| `alerts.*` | S6, S8 | Cassandra sink, API live feed | country |

Example `skills.needed` message:

```json
{
  "event_id": "uuid",
  "source": "linkedin_jobs",
  "country": "HU",
  "posted_date": "2026-10-01",
  "job_title": "SOC Analyst",
  "ecsf_role": "Cyber Incident Responder",
  "skills": ["SIEM", "Incident response", "Python"]
}
```

## Stream processing

Eight stream stages, two per member, consume from Kafka and publish their results back to Kafka. Each stage runs in its own container, built with Quix Streams or Faust (Python) and the [River](https://riverml.xyz) library for the stream-mining algorithms, with no Spark.

| Stage | Member | Input → output | Algorithm | Purpose |
| --- | --- | --- | --- | --- |
| S1 Near-duplicate filter | M1 | `raw.jobs` → `jobs.clean` | MinHash + LSH | Drop job ads reposted on several sites |
| S2 Skill extraction | M1 | `jobs.clean`, `raw.profiles` → `skills.needed`, `skills.offered` | LLM agent + ECSF mapping | Turn free text into normalized skills |
| S3 Top 10 skills needed | M2 | `skills.needed` → `agg.top10_needed` | Space-Saving (top-k) | Pipeline 1: Top 10 per country, bounded memory |
| S4 Skills-need frequency | M2 | `skills.needed` → `agg.needed_daily` | Count-Min Sketch, daily tumbling windows | Pipeline 2: need heat map |
| S5 Skills-offered frequency | M3 | `skills.offered` → `agg.offered_daily` | Sliding-window counts | Pipeline 3: supply heat map |
| S6 Trend drift detection | M3 | `agg.needed_daily` → `alerts.drift` | ADWIN | Flag skills whose demand shifts, which triggers forecast retraining |
| S7 Threat frequency | M4 | `threats.events` → `agg.threats_daily` | Hourly and daily tumbling windows | World threat map with scroll-back |
| S8 Threat burst detection | M4 | `threats.events` → `alerts.threats` | Half-Space Trees | Mark unusual threat spikes per country |

A Cassandra sink consumes every `agg.*` and `alerts.*` topic. Windowed results are appended, never overwritten, so the maps can scroll back through time. Stage state is kept in Kafka changelog topics, so a restarted stage continues where it stopped.

## Storage (Cassandra)

The tables are designed query-first: there is one table per screen or question, and each is partitioned so a query reads a single partition. Everything lives in keyspace `ace`, with replication factor 3 in the cluster deployment. The schema is in [`storage/cql/schema.cql`](../storage/cql/schema.cql).

| Table | Partition key | Clustering key | Answers |
| --- | --- | --- | --- |
| `job_ads_raw` | (country, month) | posted_date, event_id | Raw job ads, used for retraining |
| `profiles_raw` | (country, month) | event_id | Raw anonymized profiles |
| `skills_needed_by_day` | (country, day) | count DESC, skill | Need heat map for one day |
| `skills_offered_by_day` | (country, day) | count DESC, skill | Supply heat map for one day |
| `top10_needed` | country | rank | Current Top 10 per country |
| `skill_trend` | (country, skill) | day | Time series for one skill, input to forecasting |
| `threats_by_day` | day | country, threat_type | World threat map for one day (scroll-back) |
| `forecasts` | (region, skill) | target_month | Forecast values and confidence band |
| `skills_gap` | (country, month) | skill | Needed vs offered ratio |

Raw tables use a time-to-live (TTL) of 2 years. Aggregate tables are kept with no expiry. An alerts table, partitioned by (country, day), stores the S6 drift and S8 burst alerts.

## Batch processing

Batch jobs run as scheduled containers (Kubernetes CronJobs), read from Cassandra, and write their results back to Cassandra. They use pandas and scikit-learn, not Spark.

**Batch 1: Skills-need forecasting**

- Input: `skill_trend` (monthly counts of each skill per country, from LinkedIn job ads).
- Models: Prophet or ARIMA/ETS (statsmodels) per country and skill, with LightGBM over lag features as a challenger. The Europe-level forecast is trained on the sum across EU countries.
- Evaluation: a time-based train/test split (last 3 months held out), scored by MAE and MAPE.
- Output: `forecasts` with a 6–12 month horizon and confidence bands.
- Schedule: retrained weekly, and early when S6 flags a demand shift.

**Skills gap analysis**: a monthly job computes needed ÷ offered per skill and country and writes it to `skills_gap`. This is the CyberSeek-style supply/demand ratio.

**Platform analysis (higher grade)**: the platform analysis agent's output is summarized into a feature comparison of CyberSeek, ENISA ECSF tools and the ISC2 Workforce Study.

## Serving and visualization

A FastAPI service is the only reader of Cassandra for the frontend. A React dashboard calls it over REST, and a WebSocket pushes live updates. The maps use Leaflet or ECharts with GeoJSON country borders.

| Endpoint | Returns | Used by |
| --- | --- | --- |
| `GET /threats/map?date=` | Threat count per country for one day | World threat map |
| `GET /skills/needed/map?date=&skill=` | Need frequency per country | Skills-need map |
| `GET /skills/offered/map?date=&skill=` | Offer frequency per country | Skills-offered map |
| `GET /skills/top10?country=` | Current Top 10 skills | Country panel |
| `GET /forecast?region=&skill=` | Forecast series with bands | Forecast dashboard |
| `GET /gap?country=&month=` | Needed vs offered per skill | Gap dashboard |
| `WS /live` | New aggregates as they arrive | Live feed |

**Dashboards:** each member builds one dashboard with at least 2 graphs, all in the same React app.

| Dashboard | Member | Graphs |
| --- | --- | --- |
| Data pipeline health | M1 | Ads and profiles ingested per source per day (line); duplicates removed by S1 (bar); extraction success rate (gauge) |
| Skills demand | M2 | Choropleth of skills-need frequency with a skill filter; Top 10 skills per country (bar); click a country for its details |
| Supply, gap and forecast | M3 | Choropleth of skills offered; needed vs offered per skill (grouped bar); forecast with confidence band (line); drift alerts |
| Threat landscape | M4 | World threat choropleth with a time slider for scroll-back; threats per day with S8 burst markers (line); threat types (bar) |

## Deployment

Every component ships as a Docker image. Development runs on Docker Compose, and the final system runs on Kubernetes, with Docker Swarm as a fallback.

![ACE deployment: containers, groups and ports](images/deployment.png)

Every box is one Docker container. Kafka and Cassandra run as 3-node StatefulSets, and each stream stage runs as its own pod so it can scale with its topic partitions.

| Component | Kubernetes object | Replicas |
| --- | --- | --- |
| Kafka brokers (KRaft mode) | StatefulSet | 3 |
| Cassandra nodes | StatefulSet | 3 |
| Collector and extraction agents | Deployment, one per agent | 1–2 each |
| Stream pipelines | Deployment, one per pipeline | 1–3 (up to the partition count) |
| Cassandra sink | Deployment | 2 |
| Forecasting and gap jobs | CronJob | Runs on schedule |
| API | Deployment + Service | 2 |
| Dashboard | Deployment + Ingress | 2 |

- **Configuration:** ConfigMaps hold topic names and schedules. Secrets hold API keys, including the LLM key.
- **Monitoring:** Kafka UI to inspect topics, Prometheus and Grafana for service health.
- **CI:** GitHub Actions builds the images and runs tests on every pull request.

## Technology stack

| Layer | Technology | Why |
| --- | --- | --- |
| Agents | Python, LangGraph or CrewAI, an LLM API | Separate agents per sub-task, as the brief requires |
| Ingestion | Apache Kafka (KRaft) | Required by the brief; real-time and replayable |
| Stream processing | Quix Streams or Faust | Python stream processing on Kafka without Spark |
| Storage | Apache Cassandra | Required by the brief; fast time-series writes |
| Batch / ML | pandas, Prophet, statsmodels, LightGBM | Light forecasting stack, no Spark |
| API | FastAPI | Async Python, auto-generated OpenAPI docs |
| Frontend | React, Leaflet or ECharts | Choropleth maps and dashboards |
| Deployment | Docker, Kubernetes or Docker Swarm | Required by the brief |
| CI / monitoring | GitHub Actions, Prometheus, Grafana | Automated builds and service health |

## Per-member work plan, schedule and risks

Each member is responsible for one component, two stream stages, one dashboard and one report section, so each person can be graded on their own work. There is no team lead: the shared contracts (Kafka schemas, Cassandra tables and API endpoints) belong to the whole team, and changes to them are agreed together.

| Member | Component | Stream stages | Dashboard | Report and slides |
| --- | --- | --- | --- | --- |
| M1 | Collector agents, orchestrator, ECSF taxonomy | S1 near-duplicate filter, S2 skill extraction | Data pipeline health | Datasets section |
| M2 | Kafka, Cassandra, Docker/Kubernetes, CI, integration | S3 Top 10 (Space-Saving), S4 need frequency (Count-Min) | Skills demand | System architecture section |
| M3 | Batch forecasting, gap analysis, platform analysis | S5 offered frequency, S6 drift detection (ADWIN) | Supply, gap and forecast | Testing and experiments section |
| M4 | FastAPI, React app, map components | S7 threat frequency, S8 burst detection (Half-Space Trees) | Threat landscape | Abstract, introduction, slide deck |

Everyone contributes 2–4 references for their own algorithms, and everyone proofreads the report.

**Schedule**

![ACE schedule, Oct 14 to Dec 6, 2026](images/schedule.png)

The plan aims for the Nov 26 (12:00 CET) early submission, which secures December slots for both theory exams. Dec 6 is the fallback.

**Risks**

- **LinkedIn access:** scraping breaks LinkedIn's terms and gets blocked. Mitigation: public datasets and job APIs.
- **Personal data in profiles:** names and identifiers are removed at the collector, and only skills and country are stored.
- **LLM cost and accuracy:** extraction results are cached and spot-checked against a hand-labelled sample.
- **Team size:** the course rules expect 5-member teams and ACE has 4. The plan sizes the work for 4 members (8 stream stages); confirm this with the supervisor in the Oct 19 review.

## References

The course allows 10–15 references, no older than 10 years, with 2–3 exceptions. The foundational algorithm papers below are older, so the report keeps at most 3 of them and cites the recent papers for the rest. These citations are from memory; check each one before it goes into the report.

**Recent (2016 or later)**

- ENISA. (2022). *European Cybersecurity Skills Framework (ECSF)*. European Union Agency for Cybersecurity.
- Gomes, H. M., Read, J., Bifet, A., Barddal, J. P., & Gama, J. (2019). Machine learning for streaming data: State of the art, challenges, and opportunities. *ACM SIGKDD Explorations Newsletter, 21*(2), 6–22.
- ISC2. (2024). *ISC2 Cybersecurity Workforce Study*. ISC2.
- Montiel, J., et al. (2021). River: Machine learning for streaming data in Python. *Journal of Machine Learning Research, 22*(110), 1–8.
- Petersen, R., Santos, D., Wetzel, K., Smith, M., & Witte, G. (2020). *Workforce Framework for Cybersecurity (NICE Framework)* (NIST SP 800-181 Rev. 1). NIST.
- Taylor, S. J., & Letham, B. (2018). Forecasting at scale. *The American Statistician, 72*(1), 37–45.
- Zhang, M., Jensen, K. N., Sonniks, S. D., & Plank, B. (2022). SkillSpan: Hard and soft skill extraction from English job postings. In *Proceedings of NAACL 2022*.

**Foundational algorithms (older than 10 years)**

- Bifet, A., & Gavaldà, R. (2007). Learning from time-changing data with adaptive windowing. In *Proceedings of the SIAM International Conference on Data Mining*. (ADWIN, S6)
- Broder, A. Z. (1997). On the resemblance and containment of documents. In *Compression and Complexity of Sequences*. IEEE. (MinHash, S1)
- Cormode, G., & Muthukrishnan, S. (2005). An improved data stream summary: The count-min sketch and its applications. *Journal of Algorithms, 55*(1), 58–75. (S4)
- Metwally, A., Agrawal, D., & El Abbadi, A. (2005). Efficient computation of frequent and top-k elements in data streams. In *ICDT 2005*. Springer. (Space-Saving, S3)
- Tan, S. C., Ting, K. M., & Liu, T. F. (2011). Fast anomaly detection for streaming data. In *Proceedings of IJCAI 2011*. (Half-Space Trees, S8)

**Platforms analysed**

- [CyberSeek](https://www.cyberseek.org): cybersecurity supply and demand heat map (US).
