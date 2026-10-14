# Agents (M1)

Collector agents (job ads, profiles, reports, threats) and the orchestrator. Each agent publishes JSON to Kafka and never writes to Cassandra. Message formats: [shared/schemas](../shared/schemas/).

Planned layout: one subfolder per agent, each with its own `Dockerfile`.
