FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY ace_api ./ace_api
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 ace
USER ace
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "ace_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
