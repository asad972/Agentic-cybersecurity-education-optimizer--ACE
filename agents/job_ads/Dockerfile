FROM python:3.11-slim

WORKDIR /app
COPY shared/python /opt/ace/shared-python
COPY shared/schemas /opt/ace/schemas
COPY shared/testdata /opt/ace/testdata
RUN mkdir -p /opt/ace/job_ads
COPY agents/job_ads/__init__.py agents/job_ads/normalizer.py agents/job_ads/agent.py agents/job_ads/__main__.py /opt/ace/job_ads/
RUN pip install --no-cache-dir /opt/ace/shared-python kafka-python==2.2.15 cassandra-driver==3.30.1

ENV PYTHONPATH=/opt/ace
ENV ACE_SCHEMAS_DIR=/opt/ace/schemas
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092
ENV CASSANDRA_HOSTS=cassandra
ENV CASSANDRA_KEYSPACE=ace

ENTRYPOINT ["python", "-m", "job_ads"]