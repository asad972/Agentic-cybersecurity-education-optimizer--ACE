# One image for every stream stage S1–S10, run on Kafka with ace.kafka.
# Build from the repository root:
#   docker build -f streaming/Dockerfile -t ace-stage .
# Run one stage by naming its class; settings go after --arg:
#   docker run --rm --network <stack network> ace-stage s4_demand_frequency.stage:DemandFrequency
#   docker run --rm --network <stack network> ace-stage s7_threat_frequency.stage:ThreatFrequencyStage --arg period=daily
# The root docker-compose.yml starts each stage from this image.
FROM python:3.11-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/default-java \
    ACE_SCHEMAS_DIR=/app/shared/schemas ACE_TAXONOMY_DIR=/app/shared/taxonomy \
    ACE_FLINK_JARS=/opt/ace/flink-jars \
    KAFKA_BOOTSTRAP_SERVERS=kafka:29092
COPY shared/python /app/shared/python
# ARM64 needs to compile PyFlink's extensions; remove the compiler afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless g++ \
    && pip install --no-cache-dir -e '/app/shared/python[flink]' && pip check \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/*
# Download and check the Kafka connector JAR now, so a running stage never downloads anything.
# It is saved readable by its owner only, so open it to the non-root user below.
RUN python -c "from ace.kafka import connector_jar; print(connector_jar())" \
    && chmod -R a+rX "$ACE_FLINK_JARS"
COPY shared/schemas /app/shared/schemas
COPY shared/taxonomy /app/shared/taxonomy
COPY streaming /app/streaming
RUN useradd --create-home --uid 10001 ace
USER ace
WORKDIR /app/streaming
ENTRYPOINT ["python", "-m", "ace.kafka"]
CMD ["--help"]
