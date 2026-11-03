# Build from the repository root: docker build -f storage/sink/Dockerfile .
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ACE_SCHEMAS_DIR=/app/shared/schemas
WORKDIR /app
COPY shared/python/pyproject.toml shared/python/
COPY shared/python/ace shared/python/ace
COPY shared/schemas shared/schemas
COPY storage/sink/pyproject.toml storage/sink/
COPY storage/sink/ace_sink storage/sink/ace_sink
RUN pip install --no-cache-dir ./shared/python ./storage/sink && useradd --create-home --uid 10001 ace
USER ace
CMD ["python", "-m", "ace_sink"]
