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
other for the model and baseline, preventing new labels from growing model state
indefinitely. Alert `subject` preserves the original collector label: within the
model group with the largest increase, the most frequent source type wins (ties
break lexically). For example, a `botnet` spike is reported as `botnet`, not
`other`. The details distinguish the group baseline from the source-type count;
there is no separate historical baseline for each unknown label.

The default model has 25 trees, height 8, a 48-observation window and a stable
Country-specific random seed. It scores each observation **before learning it**.
An alert requires all of:

- At least 48 earlier UTC hours since the first observation for this Country,
  including inferred empty hours between finalized inputs.
- HST score at least 0.50.
- Total observations at least 15 and greater than three times the prior EWMA
  total. The baseline uses alpha 0.05; this gate suppresses unusual decreases.

Issue #38 asks only for the HST score; the extra gates are there because that
score alone is direction-blind, so the EWMA ratio keeps increases only,
`min_total` stops a near-zero baseline from turning a handful of observations
into a burst, and the cooldown (below) reports a sustained burst once, not hourly.

Known limitation: warmup counts hours, not records. Zero-filled hours count
towards the 48, so a Country with very few records passes warmup mostly on
empty hours. Its model and baseline have then learned almost only zeros, and
the first hour with at least `min_total` (15) observations can alert: two
records 72 hours apart, the second with 15 observations, produce one. Read such
an alert as activity where there was almost none, not as a spike over an
established level. `min_total` is the only guard; there is no minimum number of
non-empty hours.

Both normal rows and spikes subsequently update the model and baseline.
These are demonstration thresholds, not measured operational accuracy.
`BurstConfig` exposes the model parameters; the CLI exposes `--threshold`.

Alerts have a six-hour cooldown per (Country, original subject), configurable
with `BurstConfig.cooldown_hours` (0 disables it). A six-hour sustained burst
therefore emits at most one alert for that subject. A different subject or
Country can still alert. Cooldown timestamps are checkpointed and expired on
the next input, so state retains at most one entry per hour of cooldown, not
an ever-growing history of labels. Increasing cooldown cannot recover entries
already expired under an earlier policy.

`created_at` is the **affected UTC hour start**, not processing or delivery
time. The tuple `(country, alert_type, subject, created_at)` is a deterministic
sink de-duplication key for replaying the same ordered data and configuration;
the Kafka routing key remains Country. No new field is added to the shared
alert schema. Cooldown and replay suppression within checkpointed state do not
replace sink idempotency after a fresh-state replay. `details` explains the
score, prior baseline and type group. Counts are feed observations, not confirmed attacks.

## Ordering, validation and state

- The input must contain immutable, finalized hourly aggregates in increasing
  `(day, hour)` order **per Country**. Different Countries can interleave.
  Missing hours between finalized inputs are learned as zero counts, advancing
  warmup and decaying the EWMA. This happens on the next input, not a wall-clock
  timer; S8 cannot infer quiet hours before its first observation. It assumes an
  absent finalized hour is zero, so collectors must report outages separately.
  Gap filling retains no history but takes work proportional to elapsed hours.
- Duplicate or older hours do not retrain the model or emit another alert.
  A conflicting update for an already processed hour is also ignored; correcting
  old aggregates requires a deliberate replay into fresh state.
- Shared schema failures go to `errors.invalid`. Since the shared aggregate
  schema also covers daily rows, S8 additionally routes a missing `hour` or
  wrong input topic to `errors.invalid` without training the model.
- The River model, fixed-size type baseline, sample count, integer UTC epoch-hour,
  bounded cooldown timestamps and replay
  counters live in the shared runtime's **per-key memory**, stored as Flink
  keyed state. There is no global model dictionary or accumulated event history.
  Model size is proportional to `n_trees * 2**height` per Country. The default
  25 trees of height 8 are stored for every Country; increasing height by one
  roughly doubles tree storage and checkpoint/serialization cost. Do not raise
  it casually; measure checkpoint size and processing latency first.
- The checkpoint signature includes only model-shape settings (`n_trees`,
  `height`, `window_size`, `max_feature_count`, `seed`), feature vocabulary,
  state version and River version. Threshold, upward ratio, minimum total,
  EWMA alpha, warmup and cooldown are read from the current configuration and
  can change across a restart without invalidating the model.
- Original version-1 S8 checkpoints with matching model settings/River are
  upgraded automatically: their timestamp becomes an epoch-hour and cooldown
  starts empty. Previously omitted quiet hours cannot be reconstructed from
  that old state; zero filling applies to future gaps. Incompatible model or
  River changes still require explicit migration or an intentional fresh-state
  replay. Only trusted Flink-managed state is deserialized. River 0.23.0 is
  pinned once, in the shared `stages` extra.
- S8 consumes windows already closed by S7; it adds no second watermark or
  grace window. Every dropped duplicate/out-of-order record logs a warning
  labelled `late_records_total` with Country, reason, incoming/last epoch-hour,
  increment (`dropped=1`) and the keyed reason counter. This is observable in
  worker logs; it is not a newly registered Prometheus counter. The current
  shared `RecordStage` API has no stage-level metrics callback.

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
workers after the shared-stage and S4 tests can stall. On timeout the runner
kills the child's process tree (POSIX process group / Windows `taskkill /T /F`)
and reports captured stdout/stderr. The child uses setup
captured during collection, without inherited PyFlink gateway/worker flags
or temporary paths. It does not require collector credentials or an S7 checkout.

For just S8: `docker run --rm ace-s8:test -q streaming/s8_threat_bursts/tests`.

Run the bounded demo with Python 3.11, Java 17 and the shared pinned PyFlink:

```sh
docker build --target runtime -f streaming/s8_threat_bursts/Dockerfile -t ace-s8:local .
docker run --rm ace-s8:local --demo --engine flink --output-dir /output
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
python -m pip install -e "shared/python[flink,dev]"
pytest streaming
```

For tests without Flink/Java, install `shared/python[dev]` instead and run
`pytest streaming -m 'not flink'`. This does not check Flink execution.
Both shared `dev` and `flink` extras include `ace[stages]`, which pins River.
The Python/Flink install commands in CI PR #79 therefore work unchanged, with
the pin included in its existing `shared/python/pyproject.toml` cache key.

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

The Docker image has no implicit demo command. Running it without arguments
exits with usage instructions requiring `--input` or `--demo`; it never silently
processes synthetic records in a deployed pod. Until the shared Kafka runner
is available, this image is a fixture runner, not a live Kafka consumer. Use
the explicit demo command above for a smoke test.

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
