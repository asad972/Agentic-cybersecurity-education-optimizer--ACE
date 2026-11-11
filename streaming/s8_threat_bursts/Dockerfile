FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/default-java ACE_SCHEMAS_DIR=/app/shared/schemas
COPY shared/python /app/shared/python
# ARM64 needs to compile PyFlink's extensions; remove the compiler afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless g++ \
    && pip install --no-cache-dir -e '/app/shared/python[flink]' \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/*
COPY streaming/s8_threat_bursts/requirements.txt /app/s8-requirements.txt
RUN pip install --no-cache-dir -e '/app/shared/python[flink]' -r /app/s8-requirements.txt \
    && pip check
COPY shared/schemas /app/shared/schemas
COPY streaming/s8_threat_bursts /app/streaming/s8_threat_bursts
RUN useradd --create-home --uid 10001 ace && mkdir /output && chown ace:ace /output
USER ace
WORKDIR /app/streaming
ENTRYPOINT ["python", "-m", "s8_threat_bursts"]
CMD ["--demo", "--engine", "flink", "--output-dir", "/output"]

FROM runtime AS test
USER root
WORKDIR /app
RUN pip install --no-cache-dir -e '/app/shared/python[dev]'
COPY streaming /app/streaming
USER ace
ENTRYPOINT ["pytest", "-p", "no:cacheprovider"]
CMD ["-q", "streaming"]
