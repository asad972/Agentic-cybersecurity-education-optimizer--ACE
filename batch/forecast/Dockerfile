FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ace

COPY batch/forecast/pyproject.toml /tmp/ace-forecast/pyproject.toml
COPY batch/forecast/ace_forecast /tmp/ace-forecast/ace_forecast
RUN python -m pip install --no-cache-dir /tmp/ace-forecast \
    && rm -rf /tmp/ace-forecast

COPY shared/taxonomy /opt/ace/shared/taxonomy

ENV ACE_TAXONOMY_DIR=/opt/ace/shared/taxonomy \
    PYTHONUNBUFFERED=1

USER 10001:10001
ENTRYPOINT ["python", "-m", "ace_forecast"]
