# Kubernetes manifests (M2)

Production layout: Kafka and Cassandra as 3-node StatefulSets, one Deployment per agent and stream stage, CronJobs for batch, Deployment + Ingress for API and dashboard. See the deployment diagram in [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#deployment).
