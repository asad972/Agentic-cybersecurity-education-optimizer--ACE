# Stream stages

One folder per stage, each its own container. Built with Quix Streams or Faust plus River. No Spark.

| Folder | Stage | Member | Input → output | Algorithm |
| --- | --- | --- | --- | --- |
| `s1_dedup` | S1 Near-duplicate filter | M1 | raw.jobs → jobs.clean | MinHash + LSH |
| `s2_skill_extraction` | S2 Skill extraction | M1 | jobs.clean, raw.profiles → skills.needed, skills.offered | LLM agent + ECSF |
| `s3_top10` | S3 Top 10 skills needed | M2 | skills.needed → agg.top10_needed | Space-Saving |
| `s4_need_frequency` | S4 Skills-need frequency | M2 | skills.needed → agg.needed_daily | Count-Min Sketch |
| `s5_offered_frequency` | S5 Skills-offered frequency | M3 | skills.offered → agg.offered_daily | Sliding-window counts |
| `s6_drift` | S6 Trend drift detection | M3 | agg.needed_daily → alerts.drift | ADWIN |
| `s7_threat_frequency` | S7 Threat frequency | M4 | threats.events → agg.threats_daily | Tumbling windows |
| `s8_threat_bursts` | S8 Threat burst detection | M4 | threats.events → alerts.threats | Half-Space Trees |
