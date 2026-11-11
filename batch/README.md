# Batch analytics (M3)

- `forecast/`: Batch 1, monthly Skill Demand forecasting per country and for the EU Region (SARIMA primary, LightGBM challenger). Reads `demand_trend`, writes `forecasts`.
- `gap/`: monthly Skills Gap job. Writes `skills_gap` (ratio only when Supply ≥ `GAP_MIN_SUPPLY`) and `role_relations`.

No Spark. Runs as Kubernetes CronJobs.
