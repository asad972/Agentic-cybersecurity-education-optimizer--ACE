# Batch analytics (M3)

- `forecast/`: Batch 1, skill Demand forecasting per country and for the EU region (Prophet / ARIMA, LightGBM challenger). Reads `demand_trend`, writes `forecasts`.
- `gap/`: Monthly Skills Gap and ECSF role-relations job. Uses the latest qualifying S5 snapshot, writes nullable ratios to `skills_gap`, and compares roles through Demand Top 10 Jaccard overlap.

No Spark. Runs as Kubernetes CronJobs.
