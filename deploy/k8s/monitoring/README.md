# Monitoring on Kubernetes (M2)

Prometheus, Grafana, the pipeline metrics exporter and the Kafka exporter in namespace `ace`, applied on their own with `kubectl apply -k deploy/k8s/monitoring`. The recording rules, Grafana data source and dashboard come from [`deploy/monitoring/`](../../monitoring/), the same files Docker Compose uses.

It scrapes and reads the services from the main manifests in `deploy/k8s/base` (Kafka, Cassandra, Flink and the API, from the Kubernetes pull request, issue #21). Apply those first; until they run, their targets show as DOWN in Prometheus and the panels that use them stay empty.

How to run it and what each panel means: [deploy/monitoring/README.md](../../monitoring/README.md).
