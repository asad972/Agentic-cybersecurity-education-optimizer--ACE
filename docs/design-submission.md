# ACE – Agentic Cybersecurity Education Optimizer: Project Design

Oct 19, 2026

## 1. Team

| | Member | Responsibility | Stream stages |
| --- | --- | --- | --- |
| M1 | Singh Multani Gurinder | Data agents, orchestrator, Skill Taxonomy | S1, S2 |
| M2 | Ullah Asad (team lead) | Kafka, Cassandra, deployment, monitoring | S3, S4 |
| M3 | Bailey Kelen | Forecasting, Skills Gap, LLM module | S5, S6 |
| M4 | Roomi Babar Ali | API, web app, Europe map, threat agent | S7, S8 |
| M5 | Richartz Lena | Education, workforce and platform agents | S9, S10 |

## 2. System description

ACE is a European counterpart to CyberSeek. For every country in the EU27, EEA, UK and Switzerland it shows which cybersecurity skills employers ask for (**Demand**), which skills professionals have (**Supply**), the **Skills Gap** (Demand ÷ Supply) and cyber threats, on choropleth maps and dashboards. The numbers match the architecture diagram in section 3:

1. **Online sources:** job ads (Adzuna and German Federal Employment Agency APIs, German job-ads history 2015–2026), the Stack Overflow Developer Survey, ENISA CYBERHEAD programmes, ISC2 reports, CyberSeek and ENISA ECSF, and the OTX and AbuseIPDB threat feeds.
2. **AI agents** each collect one kind of data; history is replayed in date order, so every window uses event time. A planning **orchestrator** (LangGraph or CrewAI) checks coverage per country every 6 hours and sends at most 5 collection tasks, each with a reason. Text is read by an LLM: Qwen via Ollama, Claude Haiku as fallback.
3. **Kafka** is the only way into the system. Messages are JSON, validated against shared schemas and keyed by country.
4. **Ten stream-mining stages** run as Apache Flink jobs (PyFlink and River, no Spark), read from Kafka and publish their results back. Every skill is mapped to one Skill Taxonomy based on the ECSF, so Demand and Supply can be compared.
5. A **sink** saves records and results in **Cassandra**, using query-first tables with replication factor 3.
6. **Batch jobs** forecast skill Demand 6–12 months ahead per country and for Europe (Prophet or ARIMA, LightGBM as challenger; retrained weekly or when S6 detects drift) and compute the monthly Skills Gap where at least 20 people have the skill (currently Germany, the UK and France).
7. A **FastAPI** service and a **React** app show a CyberSeek-style Europe map with a metric switch, one dashboard per member and a world threat map with scroll-back; alerts arrive live over WebSocket.

Every component runs in its own Docker container on Kubernetes (Docker Swarm as fallback); Prometheus and Grafana track pipeline health.

## 3. System architecture

![ACE system architecture](images/architecture.png)

## 4. Stream-mining tasks and algorithms

| Stage | Task | Algorithm | Input → output | Owner |
| --- | --- | --- | --- | --- |
| S1 | Remove near-duplicate job ads posted on several sites | MinHash + LSH | raw job ads → clean job ads | M1 |
| S2 | Extract skills, certifications and the ECSF role from text | LLM agent + Skill Taxonomy mapping | job ads, profiles → skills | M1 |
| S3 | Top 10 skills and certifications per country, last 30 days | Space-Saving, mergeable daily summaries | Demand skills → Top 10 | M2 |
| S4 | Daily Demand per skill and country (Demand map) | Count-Min Sketch, daily tumbling windows | Demand skills → daily counts | M2 |
| S5 | Supply per skill and country over the last 365 days | Exponential histograms (DGIM), sliding window | Supply skills → counts | M3 |
| S6 | Detect shifts in Demand; trigger forecast retraining | ADWIN | daily counts → drift alerts | M3 |
| S7 | Threat counts per country, hourly and daily | Tumbling windows | threat events → counts | M4 |
| S8 | Detect unusual threat bursts per country | Half-Space Trees | hourly counts → burst alerts | M4 |
| S9 | Find emerging skills not yet in the taxonomy | Lossy Counting, 30-day window | unmapped skills → candidates | M5 |
| S10 | Count distinct companies hiring per country | HyperLogLog, mergeable daily sketches | clean job ads → company counts | M5 |
