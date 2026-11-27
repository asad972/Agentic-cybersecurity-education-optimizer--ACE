# Monitoring and pipeline health (M2, WBS 4.8)

Prometheus collects numbers from the running services every 15 seconds and Grafana shows them on one dashboard, **ACE pipeline health**. It answers two questions: are all services up, and is data flowing through the pipeline as it should (records per source, duplicates removed by S1, extraction success rate, Unmapped Skills, late records, invalid messages, daily LLM spend)?

| Part | What it does | Where |
| --- | --- | --- |
| Pipeline metrics exporter | Our own small service. Reads the Kafka topics with its own consumer group and counts what passes; reads today's LLM spend from `llm_cache`. It never publishes to Kafka and never writes to Cassandra. | [`exporter/`](exporter/) |
| Kafka exporter | Ready-made ([danielqsj/kafka-exporter](https://github.com/danielqsj/kafka_exporter)). Brokers, topic offsets and consumer lag per group, e.g. the sink's `ace-sink`. | image `danielqsj/kafka-exporter:v1.10.0` |
| Prometheus | Collects and stores the numbers for 30 days; works out duplicates, success rate and Unmapped share with [recording rules](prometheus/rules/pipeline-health.yml). | [`prometheus/`](prometheus/) |
| Grafana | The dashboard, with the data source and dashboard loaded from files, so nothing is set up by hand. | [`grafana/`](grafana/) |
| Flink reporter | Flink's own Prometheus reporter, for late records. Turned on in the Flink configuration (see below). | |

The same rules, data source and dashboard are used on Docker Compose and on Kubernetes ([`deploy/k8s/monitoring/`](../k8s/monitoring/)).

## The dashboard

All "in the selected time range" panels follow the time picker at the top right (6 hours by default). "Per hour" panels show, at every point, the hour before it.

| Panel | What it means | Comes from |
| --- | --- | --- |
| Services up | Every service Prometheus collects from, UP or DOWN. Flink is DOWN until the Flink cluster runs. | Prometheus `up` |
| Kafka brokers | Brokers Kafka exporter can see: 1 locally, 3 on Kubernetes. | Kafka exporter |
| Cassandra | Whether the exporter reached Cassandra (keyspace `ace`) at its last LLM spend check. | exporter |
| Unreadable messages | Messages on the watched topics that were not JSON objects. Should stay 0. | exporter |
| Records ingested per source | Records per minute on `raw.jobs`, `raw.profiles`, `raw.programmes`, `raw.workforce` and `threats.events`, per source (e.g. `raw.jobs · adzuna`). Replays show as bursts. | exporter |
| Records ingested in the selected time range | The same as totals. | exporter |
| Duplicates removed by S1 | Job ads on `raw.jobs` that S1 did not pass on to `jobs.clean`, as a number and as a share. Job ads S1 has not reached yet count too, so the number briefly runs ahead during a replay. | exporter + rules |
| Extraction success rate | `skills.demand` ÷ `jobs.clean` (job ads) and `skills.supply` ÷ `raw.profiles` (profiles): the share S2 turned into at least one Skill. | exporter + rules |
| Unmapped Skills share | Share of all mentions S2 found that match no Skill Taxonomy entry. A rising share means the taxonomy is missing something; S9 lists the most frequent ones. | exporter + rules |
| Mentions in the selected time range | Skill, Certification and Unmapped mentions per topic. | exporter |
| Late records dropped by Flink stages | Records that came after their window's grace period, per Flink job. The stage base drops and counts them (`late_records_total`). | Flink reporter + rules |
| Invalid messages per topic and producer | Messages that failed their schema check and went to `errors.invalid`, by the topic they were meant for and who sent them. | exporter + rules |
| Consumer lag per group and topic | Messages each consumer group still has to read. A lag that keeps growing means that consumer is stuck or too slow. | Kafka exporter |
| Sink lag | The same for the sink alone (group `ace-sink`). | Kafka exporter |
| LLM spend today | Euros spent on the LLM since midnight UTC: the sum of `cost_eur` in `llm_cache` for rows created today. Cached answers are never paid twice, so each row is one paid call. | exporter |
| Share of the daily LLM budget used | Spend ÷ `LLM_DAILY_BUDGET_EUR`. At 100 % the LLM module stops calling until midnight UTC. | exporter |
| LLM spend today per task, Paid LLM calls today | Spend and calls per task (e.g. `skill_extraction`) and provider (`local`, `hosted`). | exporter |
| Orchestrator tasks per agent, Who decided | Tasks on `tasks.collect` per agent and action, and how many the LLM chose versus the fixed rules. The list of tasks with their reasons is M1's "What the orchestrator decided" panel. | exporter |

## Run it locally

Start the main stack first, from the repository root. This also starts the API and the dashboard; see [Running the project](../../README.md#running-the-project):

```powershell
docker compose up -d --build --wait
```

The monitoring containers join the main stack's Docker network, whose name comes from the folder the repository is in. Once, write it and a Grafana admin password of your choice to `deploy/monitoring/.env` (git ignores `.env` files):

```powershell
$net = docker inspect ace-kafka --format '{{range $name, $net := .NetworkSettings.Networks}}{{$name}}{{end}}'
Set-Content deploy\monitoring\.env "ACE_NETWORK=$net`nGRAFANA_ADMIN_PASSWORD=choose-a-password" -Encoding ascii
```

Then start monitoring:

```powershell
docker compose -f deploy/monitoring/compose.yml up -d --build
```

| What | Address |
| --- | --- |
| Grafana | http://localhost:3000 (opens on the dashboard; anyone can view, `admin` + your password to edit) |
| Prometheus | http://localhost:9090 (Status → Targets shows what it collects from) |

Grafana needs about a minute the first time. Stop with `docker compose -f deploy/monitoring/compose.yml down`; add `-v` to also delete the stored numbers. Optional settings in the same `.env`: `GRAFANA_PORT`, `PROMETHEUS_PORT`, `LLM_DAILY_BUDGET_EUR` (default 2).

## Run it on Kubernetes

The manifests are in [`deploy/k8s/monitoring/`](../k8s/monitoring/) and go into namespace `ace` next to the rest of the stack. They expect the Services `kafka` (port 9092), `cassandra` (9042), `flink-jobmanager` (9249) and the headless Service `flink-taskmanager` (9249).

1. Build the exporter image and make it available to the cluster (for minikube: `minikube image load ace-pipeline-exporter:0.1.0`). For a registry, change the image name with `kustomize edit set image` in `deploy/k8s/monitoring`.

   ```powershell
   docker build -f deploy/monitoring/exporter/Dockerfile -t ace-pipeline-exporter:0.1.0 .
   ```

2. Create the Grafana admin password as a Secret, once (it is never stored in the repository):

   ```powershell
   kubectl -n ace create secret generic grafana-admin --from-literal=password=choose-a-password
   ```

3. Apply:

   ```powershell
   kubectl apply -k deploy/k8s/monitoring
   kubectl -n ace get pods -l app.kubernetes.io/component=monitoring
   ```

4. Open Grafana and Prometheus through a port forward (each in its own window):

   ```powershell
   kubectl -n ace port-forward svc/grafana 3000:3000
   kubectl -n ace port-forward svc/prometheus 9090:9090
   ```

Prometheus keeps its data on a 5 GB volume (`prometheus-data`); Grafana keeps nothing, since everything it shows is loaded from ConfigMaps. Remove everything with `kubectl delete -k deploy/k8s/monitoring` (this also deletes the stored numbers).

## Late records from Flink

The stage base counts late records in a Flink counter called `late_records_total` (`ace.stage.LATE_METRIC`). Flink's Prometheus reporter is part of the Flink image; it only has to be switched on in the Flink configuration of the JobManager and the TaskManagers:

```yaml
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9249
```

With the official Flink image, the same two lines go in the `FLINK_PROPERTIES` environment variable. Port 9249 has to be reachable as `flink-jobmanager:9249` and `flink-taskmanager:9249`. Flink publishes counters as Prometheus gauges named `flink_taskmanager_job_task_operator_..._late_records_total`, with the job name in the `job_name` label; the rule `ace:late_records:increase1h` sums them per job.

On Kubernetes, `deploy/k8s/base/flink.yaml` already sets both lines. Until the stages run on Flink, the "Late records" panel shows no data and the `flink` target shows as DOWN in Prometheus. The rule matches both name forms Flink can produce (with or without a metric group in front of `late_records_total`), which the rule tests cover. The exact name still has to be confirmed on the first stage that runs on the cluster.

## The exporter

Metrics (all start with `ace_`):

| Metric | Labels | Meaning |
| --- | --- | --- |
| `ace_records_total` | topic, source | Records read per topic and source |
| `ace_skill_mentions_total` | topic, kind | Skill, Certification and Unmapped mentions in `skills.demand` / `skills.supply` |
| `ace_invalid_messages_total` | topic, producer | Messages on `errors.invalid` |
| `ace_collect_tasks_total` | agent, action, decided_by | Orchestrator tasks on `tasks.collect` |
| `ace_unreadable_messages_total` | topic | Messages that were not JSON objects |
| `ace_llm_spend_today_eur`, `ace_llm_calls_today` | task, provider | Spend and paid calls since midnight UTC |
| `ace_llm_daily_budget_eur` | | `LLM_DAILY_BUDGET_EUR` |
| `ace_cassandra_up`, `ace_llm_spend_available` | | 1 when the last check reached Cassandra / could read `llm_cache` |

How it counts:

- Topic names come from `ace.contracts`, never typed by hand.
- Counters start from 0 when the exporter restarts and it carries on from where its consumer group stopped; Prometheus handles the restart, so the hourly and time-range panels stay right. The very first time it reads everything Kafka still keeps.
- Duplicates, success rate and Unmapped share are worked out by Prometheus (recording rules), not by the exporter, for the same reason.
- Sources and producers are free text, so label values are cut to 64 characters, and after 50 different values for one label the rest count as `other`.
- A missing or unreadable `llm_cache` (e.g. before the LLM module has run) shows as "llm_cache not readable", never as an error.
- `llm_cache` is keyed by task and input, not by time, so the spend check reads the whole table. It holds one row per paid call, so every 5 minutes is cheap.

Settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka brokers |
| `EXPORTER_GROUP_ID` | `ace-monitoring` | Kafka consumer group |
| `EXPORTER_PORT` | `9400` | Port of `/metrics` |
| `CASSANDRA_HOSTS`, `CASSANDRA_PORT`, `CASSANDRA_KEYSPACE`, `CASSANDRA_LOCAL_DC` | `localhost`, `9042`, `ace`, `datacenter1` | Cassandra |
| `LLM_SPEND_INTERVAL_SECONDS` | `300` | How often the spend is read |
| `LLM_DAILY_BUDGET_EUR` | not set | Shown next to the spend |

## Tests

Exporter, Python 3.11, with the shared `ace` package installed first. The tests need no Kafka or Cassandra and never load their client libraries, so they also run on Windows:

```powershell
pip install -e "shared/python[dev]"
pip install -e "deploy/monitoring/exporter[dev]"
python -m pytest deploy/monitoring/exporter/tests
```

Recording rules and Prometheus configuration, with `promtool` from the Prometheus image (run from `deploy/monitoring/prometheus`):

```powershell
docker run --rm -v "${PWD}:/p" -w /p --entrypoint promtool prom/prometheus:v3.15.1 test rules tests/pipeline-health-test.yml
docker run --rm -v "${PWD}:/etc/prometheus" --entrypoint promtool prom/prometheus:v3.15.1 check config /etc/prometheus/prometheus.yml
```

The dashboard is [`grafana/dashboards/pipeline-health.json`](grafana/dashboards/pipeline-health.json). Grafana loads it read-only. To change it, log in as `admin`, save a copy (Save as), change the copy, export it as JSON and replace the file, keeping `"uid": "ace-pipeline-health"` and the data source `ace-prometheus`.
