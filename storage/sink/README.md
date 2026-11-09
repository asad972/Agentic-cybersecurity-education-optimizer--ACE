# Sink service (M2, WBS 4.5)

The only writer of stream data to Cassandra. It reads the topics below from Kafka and stores every message as rows in its query-first tables in keyspace `ace` ([`storage/cql/schema.cql`](../cql/schema.cql)). The table layout is in [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#storage-cassandra).

| Topic | Tables | Code |
| --- | --- | --- |
| `jobs.clean` | `job_ads_raw` | `topics/raw.py` |
| `raw.profiles` | `profiles_raw` | `topics/raw.py` |
| `raw.programmes` | `education_programmes` | `topics/raw.py` |
| `raw.workforce` | `workforce_baseline` | `topics/raw.py` |
| `agg.demand_daily` (S4) | `demand_by_day`, `demand_map`, `demand_trend`, `job_ads_by_day` | `topics/counts.py` |
| `agg.supply_daily` (S5) | `supply_by_day`, `supply_map` | `topics/counts.py` |
| `agg.top10_demand` (S3) | `top10` | `topics/counts.py` |
| `agg.threats_hourly`, `agg.threats_daily` (S7) | `threats_by_hour`, `threats_by_day` | `topics/events.py` |
| `agg.emerging_skills` (S9) | `emerging_skills` | `topics/events.py` |
| `agg.companies_daily` (S10) | `companies_by_day` | `topics/events.py` |
| `alerts.drift` (S6), `alerts.threats` (S8) | `alerts` | `topics/events.py` |
| `analysis.platforms` | `platform_analysis` | `topics/events.py` |
| `tasks.collect` | `orchestrator_decisions` | `topics/events.py` |

## How it works

- **One function per topic** (`ace_sink/topics/`) turns a message into rows, one per target table. These functions never touch Cassandra, so they are tested with plain messages. The tests also check every row against `schema.cql`: the table exists, the columns exist, the primary key is complete and each value has the right type.
- **Upserts:** Cassandra's `INSERT` replaces the row with the same primary key, and no table uses counters, so writing a message twice leaves the same rows behind. A missing optional value is written as null, so it clears an old one.
- **Nothing is lost:** a message's Kafka offset is committed only after its rows are written, or after it is delivered to `errors.invalid`. After a crash the sink reads it again and writes the same rows again.
- **Invalid messages** go to `errors.invalid` with the reason, and the sink moves on: not JSON (including `NaN`, `Infinity` and numbers too large for a double such as `1e400`, which Python's `json` would otherwise accept), failing the topic schema, or `Unstorable` (e.g. an hourly threat count with no hour, a value Cassandra cannot store, such as 3.0 or 3000000000 for an `int` column, a row Cassandra refuses with `InvalidRequest`, such as a key longer than 64 KB, or a date or time Python cannot read).
- **Everything else stops the sink** without committing: Cassandra unavailable, `errors.invalid` not accepting a record, or a bug in a row function. Docker or Kubernetes restarts it, and a bug is noticed instead of quietly sending valid messages to `errors.invalid`.
- **Known limit:** a recomputed result with fewer entries does not remove the old ones (a Top 10 recomputed with 8 items keeps the old ranks 9 and 10). Stages emit each window once, from the same input, so this only matters if a window is recomputed differently.
- `ingested_at` and `updated_at` are the time the sink read the message, not the Kafka record time, which producers such as Flink stages set to the event time.
- Topic names come from `ace.contracts`, never typed by hand. Adding a topic means one new function with `@rows_for("topic.name")` in `ace_sink/topics/`.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `SINK_TOPICS` | every topic above | Comma-separated topics to write (the ConfigMap switch) |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka brokers |
| `SINK_GROUP_ID` | `ace-sink` | Kafka consumer group |
| `CASSANDRA_HOSTS` | `localhost` | Comma-separated Cassandra hosts |
| `CASSANDRA_PORT` | `9042` | |
| `CASSANDRA_KEYSPACE` | `ace` | |
| `CASSANDRA_LOCAL_DC` | `datacenter1` | |

## Run with Docker

From the repository root, together with Kafka and Cassandra:

```bash
docker compose up -d --build sink
docker compose logs -f sink
```

The sink starts after `kafka-init` has created the topics and `cassandra-init` has created the tables.

## Tests

Python 3.11, with the shared `ace` package installed first:

```powershell
pip install -e "shared/python[dev]"
pip install -e "storage/sink[dev]"
python -m pytest storage/sink/tests
```

The tests need no Kafka or Cassandra and never load their client libraries, so they also run on Windows.
