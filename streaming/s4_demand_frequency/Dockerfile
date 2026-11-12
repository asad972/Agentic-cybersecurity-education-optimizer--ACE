# S4 Demand frequency. Build from the repository root, naming the runtime
# stage (without --target, Docker builds the last stage, which is the tests):
#   docker build --target runtime -f streaming/s4_demand_frequency/Dockerfile -t ace-s4 .
FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/default-java \
    ACE_SCHEMAS_DIR=/app/shared/schemas ACE_TAXONOMY_DIR=/app/shared/taxonomy
COPY shared/python /app/shared/python
# ARM64 needs to compile PyFlink's extensions; remove the compiler afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless g++ \
    && pip install --no-cache-dir -e '/app/shared/python[flink]' \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/*
COPY shared/schemas /app/shared/schemas
# S4 asks its sketch about every taxonomy entry, so the image needs the taxonomy.
COPY shared/taxonomy /app/shared/taxonomy
COPY streaming/s4_demand_frequency /app/streaming/s4_demand_frequency
RUN useradd --create-home --uid 10001 ace && mkdir /output && chown ace:ace /output
USER ace
WORKDIR /app/streaming
ENTRYPOINT ["python", "-m", "s4_demand_frequency"]
CMD ["--input", "s4_demand_frequency/fixtures/job_ads.jsonl", "--output-dir", "/output", "--engine", "flink"]

# S4's tests, including the Flink one:
#   docker build --target test -f streaming/s4_demand_frequency/Dockerfile -t ace-s4-test .
FROM runtime AS test
USER root
WORKDIR /app
RUN pip install --no-cache-dir -e '/app/shared/python[dev]'
COPY streaming/conftest.py /app/streaming/conftest.py
USER ace
ENTRYPOINT ["pytest", "-p", "no:cacheprovider"]
CMD ["-q", "streaming/s4_demand_frequency"]
