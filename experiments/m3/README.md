# M3 algorithm experiments

This harness measures the production implementations used by S5, S6, and the
Demand forecasting job. It uses fixed seed `20261122`, records no wall-clock
timings, and writes deterministic JSON containing its configuration, dependency
versions, and rounded metrics.

The image uses separate stream and forecast virtual environments. This preserves
the merged production metadata: River 0.23 requires pandas below 3, while the
forecast package pins pandas 3.1.1. The standard-library entry point runs both
workers and combines their results without weakening either dependency set.

Build and generate the canonical evidence from the repository root:

```bash
docker build -f experiments/m3/Dockerfile -t ace-m3-experiments .
docker run --rm ace-m3-experiments \
  --revision "$(git rev-parse HEAD)" \
  > experiments/m3/results.json
```

Rebuild after adding `results.json`, then verify it:

```bash
docker build -f experiments/m3/Dockerfile -t ace-m3-experiments .
docker run --rm ace-m3-experiments \
  --check experiments/m3/results.json
```

Render the Markdown table used by the report:

```bash
python -m experiments.m3 --table experiments/m3/results.json
```

Run harness tests inside the image's isolated environments:

```bash
docker run --rm --entrypoint /opt/ace-stream/bin/python ace-m3-experiments \
  -m pytest -q experiments/m3/tests/test_stream_experiments.py
docker run --rm --entrypoint /opt/ace-forecast/bin/python ace-m3-experiments \
  -m pytest -q \
  experiments/m3/tests/test_forecast_experiments.py \
  experiments/m3/tests/test_harness.py
```

The forecast workloads are synthetic because the repository does not contain a
versioned multi-year Demand series. They measure implementation behavior under
controlled patterns, not real-world forecasting generalization. LightGBM's
quantile ranges are recursive forecast quantiles rather than formally calibrated
confidence intervals.
