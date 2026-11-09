# S7: threat frequency — M4, issue #36

S7 consumes `threats.events` and counts threat types per Country in UTC hourly
and daily tumbling windows. It produces country-keyed `agg.threats_hourly` and
`agg.threats_daily` records matching the existing `agg_threats` schema.

This implementation uses the current `ace.stage.WindowedStage`, `build_flink`
and `run_local` APIs. It changes no shared contracts. The shared base supports
one window size and one output topic per instance, so `build_s7(stream)` joins
an hourly instance and a daily instance into one PyFlink graph.

## Window behavior

- Event time comes from `observed_at`, normalized to UTC by the shared base.
- Both branches use a 15-minute grace period. The shared watermark is latest
  event time minus 15 minutes minus 1 millisecond. An hourly window starting at
  10:00 closes after an observation at 11:15; the daily window closes after an
  observation at 00:15 the next day. Results emit once per window execution.
- Counts are keyed by Country, but **watermarks are not per Country**. The
  local runner has one watermark; Flink uses its upstream task watermarks.
  A much newer event from another Country can make an older event late. This
  follows the shared framework rather than the previous private M4 runner.
- Hourly and daily windows accept late arrivals independently. A closed hour
  cannot reopen, while its day may still accept the event. Each branch has the
  shared `late_records_total` metric on its consumed late side output.
- Repeated `event_id` values count once per Country/window. This assumes a
  replay preserves the event's Country and observation time. IDs and counts
  live in Flink's picklable window accumulator and are discarded when it closes.
  Memory is proportional to distinct events in open windows; this is exact
  counting, not an approximate sketch or global deduplication service.
- Input and output validation use `ace.contracts`. The combined job emits one
  `errors.invalid` message per invalid input, although both branches validate
  it. Output validation failures from either branch remain visible.
- Empty windows produce no row: no observations are not evidence of zero
  attacks. Bounded fixture input closes remaining windows at EOF. A continuous
  stream closes them only when event-time watermarks advance, not on wall-clock
  midnight or Kafka idleness.

Hourly payload:

```json
{"country":"HU","day":"2026-11-01","hour":0,"counts":{"malware":1,"phishing":1}}
```

Daily payload for the same fixture:

```json
{"country":"HU","day":"2026-11-01","counts":{"malware":2,"phishing":1}}
```

## Test and demonstrate with Docker

Run from the repository root with Docker Desktop running. This avoids needing
Java or PyFlink installed on the host. Images use Python 3.11, Java 17 and the
PyFlink version pinned in `shared/python/pyproject.toml`.

```sh
docker build --target test -f streaming/s7_threat_frequency/Dockerfile -t ace-s7:test .
docker run --rm ace-s7:test
```

The test suite includes a `@pytest.mark.flink` test of the combined real Flink
graph against the local runner: both topics, Country keys, duplicates and one
invalid-input error. Deterministic late-arrival tests use `run_local`, because
Flink advances its watermark periodically rather than after every record.
The pickle test checks accumulator serialization, not a full cluster restart.
Each S7 Flink test uses a fresh Python process (120-second timeout), so successive
MiniCluster jobs do not reuse stale PyFlink/Beam worker threads.

```sh
docker build --target runtime -f streaming/s7_threat_frequency/Dockerfile -t ace-s7:local .
docker run --rm ace-s7:local
```

The default command runs the bundled small fake fixture through real Flink,
writes `/output/hourly.jsonl`, `/output/daily.jsonl`, `/output/invalid.jsonl`, and
prints row counts: **4 hourly, 3 daily, 0 invalid**. Output is ephemeral with
`--rm`; mount a writable directory at `/output` to retain it. The image runs as
UID 10001 and the mounted directory must allow that user to write.

For host setup, follow [the shared Python/Java instructions](../../shared/python/README.md).
Keep the virtual environment outside the repository, using a path without
spaces or brackets (for example `C:\venvs\ace` on Windows). Install
`shared/python[flink,dev]` there; use `[dev]` only for tests without Flink.
From the repository root, plain `pytest streaming -m 'not flink'` works.
Omit the marker filter to include the real Flink tests when Java is installed.

Run the CLI from `streaming/`, using the same import name as S4:

```sh
cd streaming
python -m s7_threat_frequency \
  --input s7_threat_frequency/fixtures/events.jsonl \
  --output-dir /tmp/ace-s7-output
```

Add `--engine flink` to run the combined graph. Both engines print a JSON
summary with `late_hourly` and `late_daily` followed by output row counts.
The Flink summary counts its actual side outputs; watermark timing can differ
from the deterministic local runner on out-of-order fixtures. Late payloads
are collected, never printed into stdout. Only `streaming/` is shipped to
workers; `ace` must be installed in the worker's Python environment.

Per-branch integration tests use `ace.stage.run_flink_local`. The combined
fixture harness remains small and separate because the shared runner currently
accepts only one stage and does not return late records. Multi-window and late
result support can replace this harness when available in the shared package.

## Deployment integration

The current shared framework provides the processing graph, but does not yet
provide the old `KafkaRunner` or a shared Kafka source/sink launcher. This
release supplies the S7 graph and a bounded fixture CLI. The CLI does **not**
connect to Kafka; a live collector-to-S7-to-Kafka deployment is a separate
integration check with the deployment owner.

The integration entry point is:

```python
from s7_threat_frequency.job import build_s7

# stream: PyFlink DataStream of (topic_name, JSON_text), only threats.events.
outputs, late_hourly, late_daily = build_s7(stream)
# outputs: (topic_name, country_or_invalid_topic_key, JSON_text).
# Attach deployment's Kafka sink to outputs and consumers to both late streams.
```

Deployment must supply the Kafka connector compatible with its Flink cluster,
subscribe to `contracts.topic("threats.events").name`, preserve the output topic
and key, enable checkpoints, and configure durable checkpoint storage and Kafka
delivery semantics. Output records are already validated/routed by `ace`;
do not bypass that path. Both hourly and daily **must read the raw events**;
daily counts cannot be reconstructed from hourly output without losing events
accepted by the day after their hour closed.

Existing topic schemas, collector code, S8, the sink, API, dashboard and shared
deployment files are outside this release. It needs no provider API keys.
