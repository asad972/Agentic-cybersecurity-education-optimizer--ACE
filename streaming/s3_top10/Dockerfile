# S3 Top 10. Build from the repository root, naming the runtime stage
# (without --target, Docker builds the last stage, which is the tests):
#   docker build --target runtime -f streaming/s3_top10/Dockerfile -t ace-s3 .
FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    JAVA_HOME=/usr/lib/jvm/default-java ACE_SCHEMAS_DIR=/app/shared/schemas
COPY shared/python /app/shared/python
# ARM64 needs to compile PyFlink's extensions; remove the compiler afterwards.
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless g++ \
    && pip install --no-cache-dir -e '/app/shared/python[flink]' && pip check \
    && apt-get purge -y --auto-remove g++ && rm -rf /var/lib/apt/lists/*
COPY shared/schemas /app/shared/schemas
COPY streaming/s3_top10 /app/streaming/s3_top10
RUN useradd --create-home --uid 10001 ace && mkdir /output && chown ace:ace /output
USER ace
WORKDIR /app/streaming
ENTRYPOINT ["python", "-m", "s3_top10"]
# No default input: the image never processes the fake job ads unless asked to.
# Smoke test: docker run --rm ace-s3 --input s3_top10/fixtures/job_ads.jsonl --output-dir /output --engine flink
CMD []

# S3's tests, including the Flink one:
#   docker build --target test -f streaming/s3_top10/Dockerfile -t ace-s3-test .
FROM runtime AS test
USER root
WORKDIR /app
RUN pip install --no-cache-dir -e '/app/shared/python[dev]'
COPY streaming/conftest.py /app/streaming/conftest.py
USER ace
ENTRYPOINT ["pytest", "-p", "no:cacheprovider"]
CMD ["-q", "streaming/s3_top10"]
