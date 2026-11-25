# Batch analytics (M3)

- `forecast/`: Batch 1, monthly Skill Demand forecasting per country and for the EU Region (SARIMA primary, LightGBM challenger). Reads `demand_trend`, writes `forecasts`.
- `gap/`: Monthly Skills Gap and ECSF role-relations job. Uses the latest qualifying S5 snapshot, writes nullable ratios to `skills_gap`, and compares roles through Demand Top 10 Jaccard overlap.

No Spark. Runs as Kubernetes CronJobs.

Both jobs install `shared/python[batch]` for the common Skills Scope,
Cassandra lifecycle and month helpers, and use `ace.taxonomy` for canonical
Skills and ECSF role mappings. Their calculations and CQL remain job-owned.
