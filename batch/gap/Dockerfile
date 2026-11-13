FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACE_TAXONOMY_DIR=/opt/ace/shared/taxonomy

WORKDIR /opt/ace

COPY batch/gap/pyproject.toml /tmp/ace-gap/pyproject.toml
COPY batch/gap/ace_gap /tmp/ace-gap/ace_gap
RUN python -m pip install --no-cache-dir /tmp/ace-gap \
    && rm -rf /tmp/ace-gap \
    && useradd --create-home --uid 10001 ace

COPY shared/taxonomy /opt/ace/shared/taxonomy

USER ace
ENTRYPOINT ["python", "-m", "ace_gap"]
