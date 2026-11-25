# Build from the repository root: docker build -f deploy/monitoring/exporter/Dockerfile .
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ACE_SCHEMAS_DIR=/app/shared/schemas
WORKDIR /app
COPY shared/python/pyproject.toml shared/python/
COPY shared/python/ace shared/python/ace
COPY shared/schemas shared/schemas
COPY deploy/monitoring/exporter/pyproject.toml deploy/monitoring/exporter/
COPY deploy/monitoring/exporter/ace_exporter deploy/monitoring/exporter/ace_exporter
RUN pip install --no-cache-dir ./shared/python ./deploy/monitoring/exporter && useradd --create-home --uid 10001 ace
USER 10001
EXPOSE 9400
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.environ.get('EXPORTER_PORT', '9400'), timeout=3)"
CMD ["python", "-m", "ace_exporter"]
