FROM python:3.11-slim

WORKDIR /app
COPY shared/python /opt/ace/shared-python
COPY shared/schemas /opt/ace/schemas
RUN mkdir -p /opt/ace/m5/workforce_agent /opt/ace/m5/sources
COPY agents/m5/m5_runtime.py agents/m5/workforce.py /opt/ace/m5/
COPY agents/m5/workforce_agent/__init__.py agents/m5/workforce_agent/__main__.py /opt/ace/m5/workforce_agent/
COPY agents/m5/sources/isc2_2024_global.json /opt/ace/m5/sources/
RUN pip install --no-cache-dir /opt/ace/shared-python kafka-python==2.2.15

ENV PYTHONPATH=/opt/ace/m5
ENV ACE_SCHEMAS_DIR=/opt/ace/schemas
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092

ENTRYPOINT ["python", "-m", "workforce_agent"]
