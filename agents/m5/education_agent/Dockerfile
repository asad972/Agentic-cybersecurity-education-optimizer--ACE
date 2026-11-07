FROM python:3.11-slim

WORKDIR /app
COPY shared/python /opt/ace/shared-python
COPY shared/schemas /opt/ace/schemas
COPY agents/m5 /opt/ace/m5
RUN pip install --no-cache-dir /opt/ace/shared-python kafka-python==2.2.15

ENV PYTHONPATH=/opt/ace/m5
ENV ACE_SCHEMAS_DIR=/opt/ace/schemas
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092

ENTRYPOINT ["python", "-m", "education_agent"]
