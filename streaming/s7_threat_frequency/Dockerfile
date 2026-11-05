FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/default-java ACE_SCHEMAS_DIR=/app/shared/schemas
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*
COPY shared/python /app/shared/python
# ARM64 needs to compile PyFlink's extensions; remove the compiler afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && pip install --no-cache-dir '/app/shared/python[flink]' \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/*
COPY shared/schemas /app/shared/schemas
COPY streaming/s7_threat_frequency /app/streaming/s7_threat_frequency
RUN useradd --create-home --uid 10001 ace && mkdir /output && chown ace:ace /output
USER ace
ENTRYPOINT ["python", "-m", "streaming.s7_threat_frequency"]
CMD ["--input", "streaming/s7_threat_frequency/fixtures/events.jsonl", "--output-dir", "/output", "--engine", "flink"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir '/app/shared/python[dev]'
COPY streaming/conftest.py /app/streaming/conftest.py
USER ace
ENTRYPOINT ["python", "-m", "pytest", "-p", "no:cacheprovider"]
CMD ["-q", "streaming/s7_threat_frequency/tests"]
