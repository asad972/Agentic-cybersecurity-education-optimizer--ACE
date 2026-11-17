# Kubernetes manifests (M2)

These files run ACE on Kubernetes. `base/` is the cluster layout from [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#deployment): 3 Kafka brokers, 3 Cassandra nodes and replication factor 3. `overlays/local/` shrinks it to one copy of everything, so it fits on a laptop.

Everything goes into the namespace `ace`. Prometheus and Grafana are set up separately in `deploy/k8s/monitoring/`, which comes with the monitoring pull request (issue #22).

## What runs

| Part | Kubernetes objects | Cluster (`base`) | Laptop (`overlays/local`) |
| --- | --- | --- | --- |
| Kafka brokers (KRaft) | StatefulSet `kafka`, Services `kafka` and `kafka-headless`, one 5 GiB volume per broker | 3 | 1 |
| Topic setup | Job `kafka-init` (runs [`deploy/kafka/create-topics.sh`](../kafka/create-topics.sh) on [`topics.csv`](../../shared/schemas/topics.csv)) | runs once | runs once |
| Kafka UI | Deployment + Service `kafka-ui` | 1 | 1 |
| Cassandra nodes | StatefulSet `cassandra`, Services `cassandra` and `cassandra-headless`, one 5 GiB volume per node | 3 | 1 |
| Table setup | Job `cassandra-init` (runs [`storage/cql/schema.cql`](../../storage/cql/schema.cql)) | runs once | runs once |
| Sink | Deployment `sink` | 2 | 1 |
| API | Deployment + Service `api` | 2 | 1 |
| Web app | Deployment + Service `dashboard` | 2 | 1 |
| Way in from outside | Ingresses `web` (`/`) and `api` (`/api`) | yes | NodePorts 30080 and 30800 |
| Flink JobManager | Deployment + Service `flink-jobmanager` (web UI on 8081) | 1 | 1 |
| Flink TaskManagers | Deployment `flink-taskmanager`, headless Service for metrics | 2 (6 slots each) | 1 (10 slots) |
| Settings | ConfigMaps `ace-config`, `kafka-config`, `cassandra-config`, `sink-config`, `api-config`, `flink-config`, `kafka-topics`, `cassandra-schema` | | |
| API keys | Secret `ace-api-keys`, created by hand on the cluster (see [Settings](#settings)) | | |

The ten stream stages are not Deployments of their own. Each stage is a PyFlink job that is submitted to the one Flink session cluster and runs in the TaskManagers' slots (see [Stream stages on Flink](#stream-stages-on-flink)).

Still to come from their owners: the agents (one Deployment each), the LLM server (Deployment + Service `llm`) and the forecast and gap jobs (CronJobs).

## Before you start

- A Kubernetes cluster and `kubectl`. On a laptop the easiest is Docker Desktop: Settings → Kubernetes → Enable Kubernetes. kind and minikube also work.
- Memory for Docker: about 6 GB for the laptop layout. The cluster layout needs about 14 GB, so it is meant for a real cluster.

## 1. Build the images

The sink, API and web app images are built on your machine and never pulled from a registry. From the repository root in PowerShell:

```powershell
docker build -t ace-sink -f storage/sink/Dockerfile .
docker build -t ace-api -f api/Dockerfile api
docker build -t ace-dashboard -f dashboard/Dockerfile dashboard
```

Docker Desktop Kubernetes sees these images straight away. On kind or minikube, copy them into the cluster first:

```powershell
kind load docker-image ace-sink ace-api ace-dashboard
```

```powershell
minikube image load ace-sink
minikube image load ace-api
minikube image load ace-dashboard
```

## 2. Start everything

```powershell
kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/k8s/overlays/local | kubectl apply -f -
```

Why not just `kubectl apply -k`: the topic list, the topic script and the Cassandra schema stay in their own folders, so Kubernetes uses exactly the same files as `docker-compose.yml`. Kustomize only reads files outside the `deploy/k8s` folder when you allow it with `--load-restrictor LoadRestrictionsNone`, and `kubectl apply -k` has no such option. The command above builds the manifests first and then applies them.

## 3. Check that it runs

```powershell
kubectl -n ace get pods
kubectl -n ace wait --for=condition=complete job/kafka-init job/cassandra-init --timeout=10m
kubectl -n ace logs job/kafka-init
kubectl -n ace logs job/cassandra-init
```

After a few minutes every pod is `Running` and the two setup jobs are `Completed`. Cassandra takes 1–2 minutes per node. The sink restarts a few times until the tables exist; that is expected, because it stops on any Cassandra error and Kubernetes starts it again.

## 4. Open the services

The laptop layout gives the web app NodePort 30080 and the API NodePort 30800. Whether `localhost` reaches them depends on how Docker Desktop made the cluster (Settings → Kubernetes): with the **kubeadm** cluster, http://localhost:30080 and http://localhost:30800/docs work; with the **kind** cluster they do not, so forward the ports.

On any cluster, forward a port (one command per window, Ctrl+C stops it):

```powershell
kubectl -n ace port-forward svc/dashboard 5173:80
kubectl -n ace port-forward svc/api 8000:8000
kubectl -n ace port-forward svc/kafka-ui 8088:8080          # Kafka UI on http://localhost:8088
kubectl -n ace port-forward svc/flink-jobmanager 8081:8081  # Flink web UI on http://localhost:8081
```

## Cluster layout

```powershell
kubectl kustomize --load-restrictor LoadRestrictionsNone deploy/k8s/base | kubectl apply -f -
```

This starts 3 Kafka brokers and 3 Cassandra nodes. Topics are created with replication factor 3 (at least 2 copies in sync), and keyspace `ace` with `NetworkTopologyStrategy` and replication factor 3. The keyspace is created before `schema.cql` runs, so the `CREATE KEYSPACE` in the file (replication factor 1, for development) changes nothing. The way in is an Ingress for the [ingress-nginx](https://kubernetes.github.io/ingress-nginx/) controller: `/` goes to the web app and `/api/...` goes to the API with `/api` removed.

## Settings

| ConfigMap | Holds |
| --- | --- |
| `ace-config` | Kafka and Cassandra addresses, `LLM_PROVIDER`, `LLM_DAILY_BUDGET_EUR`, `REPLAY_SECONDS_PER_DAY`, `GAP_MIN_SUPPLY` (same names as `.env.example`) |
| `kafka-config` | Broker count, partitions per topic, replication settings |
| `cassandra-config` | Cluster name, data centre, heap size, keyspace replication factor |
| `sink-config` | `SINK_TOPICS`: the topics the sink writes; remove one to switch it off |
| `api-config` | Browser origins allowed to call the API |
| `flink-config` | TaskManager slots and memory |

The values are in `base/kustomization.yaml`; the laptop changes are in `overlays/local/kustomization.yaml`. After changing a value, apply again: pods that use it restart by themselves.

**API keys** go into the Secret `ace-api-keys`. It is not in the manifests, so the keys never end up in git and applying the manifests again leaves them alone. Create it once on the cluster (replace the `YOUR_...` values); running the same command again updates the keys:

```powershell
kubectl -n ace create secret generic ace-api-keys --from-literal=OTX_API_KEY=YOUR_KEY --from-literal=ABUSEIPDB_API_KEY=YOUR_KEY --from-literal=ADZUNA_APP_ID=YOUR_ID --from-literal=ADZUNA_APP_KEY=YOUR_KEY --from-literal=LLM_API_KEY=YOUR_KEY --dry-run=client -o yaml | kubectl apply -f -
```

No pod reads the keys yet: they are for the agents and the hosted LLM fallback, which are still to come. Their Deployments load the keys with `envFrom` and `secretRef: {name: ace-api-keys, optional: true}`, so they also start when a key is missing.

**Changing `topics.csv`, `schema.cql`, `kafka-config` or `cassandra-config`:** the setup jobs use these, and a finished Job cannot be changed (apply reports `field is immutable`). Delete the two setup jobs and apply again. Both are safe to run twice: they only create what is missing.

```powershell
kubectl -n ace delete job kafka-init cassandra-init
```

## Stream stages on Flink

Flink runs as a session cluster: the JobManager accepts jobs and the TaskManagers run them. Each stage takes one slot per unit of parallelism, so the slots (12 in the cluster layout, 10 on a laptop) must cover all ten stages.

For now this is a skeleton. The cluster starts with the plain `flink:2.3.0-java17` image and its web UI works, but that image has no Python, so it cannot run the stages yet. The next step is an ACE Flink image built from it, with Python 3.11, PyFlink 2.3 (`apache-flink==2.3.0`), the Flink Kafka connector, the shared `ace` package and the `streaming/` folder. Then:

1. Point both Flink Deployments at that image by adding to `images:` in `base/kustomization.yaml`:

   ```yaml
     - name: flink
       newName: ace-flink
       newTag: latest
   ```

2. Submit each stage to the JobManager. The script path depends on the stage base's Kafka entry point, which does not exist yet:

   ```powershell
   kubectl -n ace exec deploy/flink-jobmanager -- flink run --detached --python STAGE_SCRIPT_PATH
   ```

   Running jobs show up in the Flink web UI. A stopped job is started again with the same command.

Checkpoints currently stay inside the JobManager; a shared volume or object store for them comes with the stage image.

## Monitoring

Prometheus and Grafana are in `deploy/k8s/monitoring/` (added by the monitoring pull request, issue #22). They can read:

| What | Address |
| --- | --- |
| Flink JobManager metrics | `flink-jobmanager:9249` |
| Flink TaskManager metrics (one per pod) | `flink-taskmanager:9249` (headless) |
| Kafka brokers (for an exporter) | `kafka:9092` |
| Cassandra (CQL) | `cassandra:9042` |
| API | `api:8000`, `/health` and `/ready` |

Every object has the labels `app.kubernetes.io/name`, `app.kubernetes.io/component` and `app.kubernetes.io/part-of: ace`.

## Remove everything

```powershell
kubectl delete namespace ace
```

This also deletes the Kafka and Cassandra volumes, so all data is gone.

## Docker Swarm (fallback)

If Kubernetes is not available, the same images can run as a Docker Swarm stack (`docker swarm init`, then `docker stack deploy`). There is no stack file yet. `docker-compose.yml` cannot be used as it is, because Swarm ignores `build`, `container_name` and the "wait until healthy" conditions, and has no one-off jobs; the topic and table setup would run as services that stop after one successful run (`restart_policy: condition: on-failure`).
