# Batch analytics (M3)

- `forecast/`: Batch 1, skills-need forecasting per country and for Europe (Prophet / ARIMA, LightGBM challenger). Reads `skill_trend`, writes `forecasts`.
- `gap/`: monthly skills gap job. Writes `skills_gap`.

No Spark. Runs as Kubernetes CronJobs.
