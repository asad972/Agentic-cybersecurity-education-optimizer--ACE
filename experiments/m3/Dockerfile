FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ace

COPY shared/python shared/python
COPY batch/forecast batch/forecast
COPY streaming/s5_supply_frequency streaming/s5_supply_frequency
COPY streaming/s6_drift streaming/s6_drift
COPY experiments experiments

RUN python -m venv /opt/ace-stream \
    && /opt/ace-stream/bin/python -m pip install --no-cache-dir \
        -e "./shared/python[stages]" \
        numpy==2.1.3 \
        pytest==9.1.1 \
    && /opt/ace-stream/bin/python -m pip check

RUN python -m venv /opt/ace-forecast \
    && /opt/ace-forecast/bin/python -m pip install --no-cache-dir \
        -e "./shared/python[batch]" \
        -e "./batch/forecast[dev]" \
    && /opt/ace-forecast/bin/python -m pip check

ENV ACE_STREAM_PYTHON=/opt/ace-stream/bin/python \
    ACE_FORECAST_PYTHON=/opt/ace-forecast/bin/python

ENTRYPOINT ["python", "-m", "experiments.m3"]
