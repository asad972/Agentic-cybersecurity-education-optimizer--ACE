FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ACE_SCHEMAS_DIR=/app/shared/schemas THREAT_COLLECTOR_STATE=/state/collector.sqlite
COPY shared/python /app/shared/python
COPY agents/threat/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir /app/shared/python -r /app/requirements.txt
COPY shared/schemas /app/shared/schemas
COPY agents/threat /app/agents/threat
RUN useradd --create-home --uid 10001 ace && mkdir /state && chown ace:ace /state
USER ace
VOLUME ["/state"]
ENTRYPOINT ["python", "-m", "agents.threat"]
CMD ["--consume-tasks"]
