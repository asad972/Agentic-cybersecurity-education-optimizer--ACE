FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ACE_TAXONOMY_DIR=/app/shared/taxonomy
WORKDIR /app
COPY api/pyproject.toml ./
COPY api/ace_api ./ace_api
COPY shared/python/ace/__init__.py shared/python/ace/ecsf.py shared/python/ace/taxonomy.py ./ace/
COPY shared/taxonomy ./shared/taxonomy
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 ace
USER ace
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "ace_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
