# FastAPI service skeleton — M4 / #33

This release provides the API process, Cassandra connection lifecycle and router registration for teammates. It has no dependency on the shared stream runtime. The API reads Cassandra; the sink and initialization tools own writes/schema creation.

## Run with Docker

From the repository root, start Cassandra and initialize the existing schema:

```bash
docker compose up -d cassandra cassandra-init
docker compose -f api/compose.yml up --build -d --wait
```

Open http://localhost:8000/docs. Check `GET /health` for process liveness and `GET /ready` for database connectivity. Readiness returns 503 until Cassandra and the `ace` keyspace are available; the API retries rather than substituting sample data. The Docker health check measures process liveness. For orchestration, use `/ready` as the separate readiness probe.

The standalone API container reaches the root stack through its published Cassandra port, using `host.docker.internal`. `extra_hosts` also supports Linux Docker. When deploying on a shared container network, set `CASSANDRA_HOSTS` to the Cassandra service name instead. The service also accepts `CASSANDRA_PORT`, `CASSANDRA_KEYSPACE`, `CASSANDRA_LOCAL_DC` and comma-separated `API_CORS_ORIGINS`. Set `ACE_API_PORT=8001` before the Compose command if port 8000 is in use.

Stop the API with `docker compose -f api/compose.yml stop`. Stop the root database services separately when finished; retain their volumes to keep data.

## Local development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e './api[dev]'
python -m uvicorn ace_api.main:app --reload --host 127.0.0.1 --port 8000
```

Run tests from the repository root with `python -m pytest -q -c api/pyproject.toml api/tests`. Unit tests use fake connections and need no running database. Verify the real connection separately with `/ready` against the root Cassandra stack.

Build the API alone with `docker build -t ace-api -f api/Dockerfile api` (the build context is `api/`).

## Add a teammate router

Put a module in `ace_api/routers/` and export an `APIRouter`. At the bottom of `main.py`, import the router and pass it to the existing factory:

```python
from .routers.demand import router as demand_router

app = create_app(extra_routers=[demand_router])
```

Inside a route, use `request.app.state.database.session` for prepared, bounded Cassandra reads. The session is opened and closed by the service. Use synchronous `def` routes for blocking driver operations (FastAPI runs these in its worker thread pool), or explicitly offload reads in async routes. Handle connection failures with a suitable 503 response. Do not execute unrestricted scans or introduce database writes in browser-facing endpoints.

Domain endpoints remain with their owners, as listed in `docs/ARCHITECTURE.md`. Map, hourly threat and WebSocket features are later releases.
