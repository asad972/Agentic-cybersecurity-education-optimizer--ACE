FROM python:3.11-slim

WORKDIR /app
COPY shared/python /opt/ace/shared-python
COPY shared/schemas /opt/ace/schemas
RUN mkdir -p /opt/ace/m5/platform_agent
COPY agents/m5/m5_runtime.py agents/m5/platform_analysis.py agents/m5/platform_evidence.json /opt/ace/m5/
COPY agents/m5/platform_agent/__init__.py agents/m5/platform_agent/__main__.py /opt/ace/m5/platform_agent/
RUN pip install --no-cache-dir '/opt/ace/shared-python[llm]' kafka-python==2.2.15

ENV PYTHONPATH=/opt/ace/m5
ENV ACE_SCHEMAS_DIR=/opt/ace/schemas
ENV KAFKA_BOOTSTRAP_SERVERS=kafka:29092

ENTRYPOINT ["python", "-m", "platform_agent"]
