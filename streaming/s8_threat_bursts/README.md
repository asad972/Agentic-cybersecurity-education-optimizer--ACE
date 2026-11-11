# S8: threat burst detection — M4, issue #38

S8 consumes finalized `agg.threats_hourly` records from S7 and emits
Country-keyed `alerts.threats` records. Each Country has a separate River
Half-Space Trees model. The stage subclasses the current `ace.stage.RecordStage`
and uses `ace.contracts` for topic lookup and input/output validation.

## Detection behavior

Features are hourly threat-type counts, scaled with
`log1p(min(count, 1000)) / log1p(1000)`, plus UTC hour-of-day divided by 23.
The fixed type vocabulary is malware, phishing, ddos, scanning, spam,
brute_force, abuse_report, indicator and other. Unknown types are combined into
other for both the model and baseline, preventing new labels from growing state
indefinitely. Alert `subject` names the type group with the largest increase.

The default model has 25 trees, height 8, a 48-observation window and a stable
Country-specific random seed. It scores each observation **before learning it**.
An alert requires all of:

- At least 48 earlier observed hours for this Country.
- HST score at least 0.50.
- Total observations at least 15 and greater than three times the prior EWMA
  total. The baseline uses alpha 0.05; this gate suppresses unusual decreases.

Both normal rows and spikes subsequently update the model and baseline.
These are demonstration thresholds, not measured operational accuracy.
`BurstConfig` exposes the model parameters; the CLI exposes `--threshold`.

`created_at` is the **affected UTC hour start**, not processing or delivery
time. This makes replayed alert keys deterministic and places markers on the
correct historical hour. `details` explains the score, prior baseline and type
group. Counts are feed observations, not confirmed attacks.

## Ordering, validation and state

- The input must contain immutable, finalized hourly aggregates in increasing
  `(day, hour)` order **per Country**. Different Countries can interleave.
  Missing hours stay missing and do not advance warmup or train zero counts.
- Duplicate or older hours do not retrain the model or emit another alert.
  A conflicting update for an already processed hour is also ignored; correcting
  old aggregates requires a deliberate replay into fresh state.
- Shared schema failures go to `errors.invalid`. Since the shared aggregate
  schema also covers daily rows, S8 additionally routes a missing `hour` or
  wrong input topic to `errors.invalid` without training the model.
- The River model, fixed-size type baseline, sample count, last hour and replay
  counters live in the shared runtime's **per-key memory**, stored as Flink
  keyed state. There is no global model dictionary or accumulated event history.
  Model size is proportional to `n_trees * 2**height` per Country; serializing
  that model for each hourly update has a cost even though history does not grow.
- State includes configuration, feature vocabulary and River version. An
  incompatible restore fails explicitly and requires a state migration or an
  intentional fresh-state replay. River is pinned to 0.23.0 in this stage's
  requirements file. Only trusted Flink-managed state is deserialized.
- S8 consumes windows already closed by S7; it adds no second watermark or
  grace window. Duplicate/out-of-order counters are in keyed memory. The
  current shared `RecordStage` API does not expose them as Prometheus metrics
  or return late side outputs; deployment monitoring must account for that.

The tests check pickle restoration and continued scoring. They do not simulate
a full Flink cluster failure or establish exactly-once Kafka delivery.

## Test with Docker

From the repository root, with Docker Desktop running:

```sh
docker build --target test -f streaming/s8_threat_bursts/Dockerfile -t ace-s8:test .
docker run --rm ace-s8:test
```

The test image runs all of `streaming/`, including the real Flink tests. S8's
Flink test uses the shared `run_flink_local` runner and compares both Country
models, replay handling and invalid routing with `run_local`. It runs in a
fresh Python process with a 120-second timeout: reusing the same loopback
workers after the shared-stage and S4 tests can stall. The child uses setup
captured during collection, without inherited PyFlink gateway/worker flags
or temporary paths. It does not require collector credentials or an S7 checkout.

For just S8: `docker run --rm ace-s8:test -q streaming/s8_threat_bursts/tests`.

Run the bounded demo with Python 3.11, Java 17 and the shared pinned PyFlink:

```sh
docker build --target runtime -f streaming/s8_threat_bursts/Dockerfile -t ace-s8:local .
docker run --rm ace-s8:local
```

The synthetic fixture spans seven days for HU and DE. It contains one injected
HU phishing spike at `2026-10-17T12:00:00Z`. Expected output: **1 alert, 0 invalid**.
The container writes `/output/alerts.jsonl` and `/output/invalid.jsonl`; files
are ephemeral with `--rm`. Mount a writable `/output` to retain them (UID 10001).
The runtime image includes only S8 and shared code/schemas.

## Host setup and fixture CLI

Follow [the shared setup](../../shared/python/README.md) for Python 3.11 and
Java. Keep the virtual environment outside the repository, particularly on
Windows (for example `C:\venvs\ace`, without spaces or brackets).
From the repository root, in that activated environment:

```sh
python -m pip install -e "shared/python[flink,dev]" -r streaming/s8_threat_bursts/requirements.txt
pytest streaming
```

For tests without Flink/Java, install `shared/python[dev]` instead and run
`pytest streaming -m 'not flink'`. This does not check Flink execution.
CI jobs collecting S8 tests must also install
`streaming/s8_threat_bursts/requirements.txt`; the shared package alone does
not install River. Resolve the shared extras and this requirements file in
the same pip command, as above, to honor PyFlink's dependency constraints.

Run from `streaming/`, using the same import convention as S4 and S7:

```sh
cd streaming
python -m s8_threat_bursts --demo --output-dir output
python -m s8_threat_bursts --input /path/to/hourly.jsonl --output-dir output --engine flink
```

Both engines write `alerts.jsonl` and `invalid.jsonl`, overwriting prior results,
and print row counts. Malformed JSON lines are routed to the invalid output.
The CLI starts fresh state each invocation and processes a finite fixture; it
does not connect to Kafka or resume a deployed job's checkpoint.

## Deployment integration

Use the current shared graph builder; there is no private Kafka runner:

```python
from ace.stage import build_flink
from s8_threat_bursts import ThreatBurstStage

stage = ThreatBurstStage()
# stream carries (topic_name, JSON_text) from stage.inputs[0].
outputs, _ = build_flink(stage, stream)
# outputs carries (topic_name, Kafka_key, JSON_text), including errors.invalid.
```

Deployment supplies the Kafka source/sink and connector, installs the same
River and shared package on workers, ships the `streaming/` import root, and
configures checkpoint storage and Kafka delivery semantics. Preserve Country
keys and increasing finalized hours per Country. Sink and live API consumers
use the existing alert schema; no schema, registry or Cassandra changes are
required by this stage. A live Kafka-to-Cassandra run remains an integration
check with the deployment owner.

## References

- [River HalfSpaceTrees API](https://riverml.xyz/dev/api/anomaly/HalfSpaceTrees/)
  documents feature bounds, score interpretation and model parameters.
- [Tan, Ting and Liu (2011), Fast Anomaly Detection for Streaming Data](https://www.ijcai.org/Proceedings/11/Papers/254.pdf)
  describes the underlying Half-Space Trees algorithm.
