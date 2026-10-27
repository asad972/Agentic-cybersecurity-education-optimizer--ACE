# Stream stages

One folder per stage, each its own job on the shared Apache Flink cluster. Built with PyFlink plus River. No Spark.

Every stage uses event time, a grace period (1 h for job ads and profiles, 15 min for threats) and emits each window once, when it closes. See [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#stream-processing).

| Folder | Stage | Member | Input → output | Algorithm |
| --- | --- | --- | --- | --- |
| `s1_dedup` | S1 Near-duplicate filter | M1 | raw.jobs → jobs.clean | MinHash + LSH |
| `s2_skill_extraction` | S2 Skill extraction | M1 | jobs.clean, raw.profiles → skills.demand, skills.supply | LLM agent + Skill Taxonomy |
| `s3_top10` | S3 Top 10 Skills and Certifications | M2 | skills.demand → agg.top10_demand | Space-Saving, 30-day merge |
| `s4_demand_frequency` | S4 Demand frequency | M2 | skills.demand → agg.demand_daily | Count-Min Sketch |
| `s5_supply_frequency` | S5 Supply frequency | M3 | skills.supply → agg.supply_daily | Exponential histograms (DGIM) |
| `s6_drift` | S6 Demand drift detection | M3 | agg.demand_daily, agg.top10_demand → alerts.drift | ADWIN |
| `s7_threat_frequency` | S7 Threat frequency | M4 | threats.events → agg.threats_hourly, agg.threats_daily | Tumbling windows |
| `s8_threat_bursts` | S8 Threat burst detection | M4 | agg.threats_hourly → alerts.threats | Half-Space Trees, one per Country |
| `s9_emerging_skills` | S9 Emerging skills | M5 | skills.demand → agg.emerging_skills | Lossy Counting over Unmapped Skills |
| `s10_hiring_companies` | S10 Hiring companies | M5 | jobs.clean → agg.companies_daily | HyperLogLog, 30-day merge |

S9 and S10 have algorithm implementations, message adapters, and unit tests in
their respective folders. The adapters keep in-memory state between daily
calls; they are not yet Flink jobs and do not persist or restore state across
restarts. Wire them into the shared `ace.stage` once that package is available.
