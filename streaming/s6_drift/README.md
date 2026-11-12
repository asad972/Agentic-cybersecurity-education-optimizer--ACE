# S6 Demand Drift Detection

S6 consumes the existing `agg.top10_demand` and `agg.demand_daily` contracts
and publishes ADWIN change alerts to `alerts.drift`. It changes no shared
schema, topic, storage, API, forecasting, or deployment contract.

## Eligibility and daily finalization

Only a `kind="skill"` S3 Top 10 item makes a Skill eligible. Eligibility is
per country and starts on the Top 10 record's day. Certification Top 10 items
are ignored. If a Top 10 record arrives after its day was finalized, the Skill
starts on the next unfinalized day; S6 never rewrites detector history.

S3 and S4 records arrive independently, so S6 retains one unfinalized demand
day. Seeing demand day D finalizes buffered days before D. This one-day lag
allows either the Top 10 or demand aggregate to arrive first for the same day
and gives the same result. The newest demand day remains buffered until a
later S4 day arrives; S6 does not invent an end-of-stream observation to flush
a delayed final day.

For a country that published an S4 snapshot, an eligible Skill absent from
`counts` is a real zero observation. A country with no S4 snapshot that day is
missing, not zero, so its country detector is not updated. For the EU sum, S6
carries forward that country's last observed value. A country that has never
contributed to an EU Skill is excluded until its first snapshot, rather than
being invented as zero.

## Country and EU alerts

S6 owns one ADWIN detector for every eligible `(country, Skill)` and one for
each Skill eligible anywhere in the EU view. The EU observation is the sum of
the latest known value from every country that has contributed since the Skill
became eligible. This prevents a late or missing country from creating a false
regional drop. EU alert details record observed, carried, and total
contributing-country coverage.

Country alerts keep their ISO alpha-2 code. EU alerts use `country="EU"`, the
existing `alerts.drift` convention that signals the forecasting workflow to
retrain early. S6 does not publish `tasks.collect` and does not invoke a batch
forecast directly.

Both upward and downward changes are emitted. The alert score is the relative
change in the ADWIN mean around the detected observation:

```text
abs(new_mean - previous_mean) / max(abs(previous_mean), 1)
```

`created_at` is deterministically set to midnight UTC on the observation day.
Alert details contain direction, observation, previous and new means, window
width, delta, scope, and EU country coverage where applicable.

## Replay and checkpoint behavior

The first valid S4 aggregate for one `(country, day)` is authoritative while
that day is buffered. Identical duplicates and conflicting replacements are
counted and ignored. Aggregates for finalized days are also counted and
ignored, so replay cannot retrain a detector or duplicate an alert.

All countries share one internal Flink key so checkpoint state can construct
EU sums. Published Kafka keys still come from each alert's `country`. State
records its format version, River version, configuration signature,
eligibility dates, the pending demand day, ADWIN detectors, the last finalized
day, the latest per-country EU contributions, and replay counters. A restore
made with an incompatible version or configuration fails explicitly instead
of silently changing detection behavior. After a River upgrade or S6 state
version change, stop the job, discard the incompatible checkpoint/savepoint,
and restart with empty state so the detectors rebuild from new events.

ADWIN state is bounded by its compressed bucket structure. S6 adds one country
detector and one latest EU value per eligible country/Skill pair, one EU
detector per eligible Skill, and retains at most the unfinalized lag buffer
rather than an unbounded demand history.

## Configuration and installation

S6 pins `river==0.23.0` in its own `requirements.txt`; River is not imposed on
unrelated users of the shared Python package. This is temporary until the
team-approved shared `stages` extra from PR #80 is merged; S6 will then use that
single pin and remove its requirements file.

```bash
python -m pip install -e "shared/python[flink,dev]" \
  -r streaming/s6_drift/requirements.txt
```

`DriftConfig` defaults are:

| Setting | Default | Meaning |
|---|---:|---|
| `delta` | `0.002` | ADWIN confidence parameter |
| `clock` | `1` | Check every daily observation |
| `max_buckets` | `5` | Maximum compressed buckets per size |
| `min_window_length` | `5` | Minimum subwindow length during a check |
| `grace_period` | `10` | Observations before ADWIN starts checking |
| `finalization_lag_days` | `1` | S3/S4 join and finalization lag |

All settings are part of the checkpoint signature. Numeric types, finite
ranges, and positive sizes are validated when the stage is constructed.

## References

- [River ADWIN 0.23.0 API](https://riverml.xyz/0.23.0/api/drift/ADWIN/)
- [Bifet and Gavaldà, *Learning from Time-Changing Data with Adaptive Windowing*](https://www.cs.upc.edu/~Gavalda/papers/adwin06.pdf)
