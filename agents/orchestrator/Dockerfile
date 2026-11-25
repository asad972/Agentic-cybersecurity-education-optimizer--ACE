FROM python:3.11-slim

WORKDIR /app
COPY shared/python /opt/ace/shared-python
COPY shared/schemas /opt/ace/schemas
COPY agents/orchestrator /opt/ace/orchestrator
RUN pip install --no-cache-dir "/opt/ace/shared-python[llm]" kafka-python==2.2.15 \
    && useradd --create-home --uid 10001 ace

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/opt/ace
ENV ACE_SCHEMAS_DIR=/opt/ace/schemas
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092
ENV CASSANDRA_HOSTS=cassandra
ENV CASSANDRA_KEYSPACE=ace

USER ace
ENTRYPOINT ["python", "-m", "orchestrator"]
