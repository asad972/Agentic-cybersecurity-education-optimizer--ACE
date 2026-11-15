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

**Windows:** run the API using Docker Desktop with Linux containers and the commands above. The Cassandra driver's native connection backend can fail to load on Windows with Python 3.12+. Local unit tests use fake connections and do not load that backend, but running the API against Cassandra should use Docker. The virtual-environment commands below use a macOS/Linux shell.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e './api[dev]'
python -m uvicorn ace_api.main:app --reload --host 127.0.0.1 --port 8000
```

Run tests from the repository root with `python -m pytest -q -c api/pyproject.toml api/tests`. Unit tests use fake connections and need no running database. Verify the real connection separately with `/ready` against the root Cassandra stack.

Build the API alone with `docker build -t ace-api -f api/Dockerfile api` (the build context is `api/`).

## Add a teammate router

Create a module such as `api/ace_api/routers/demand.py` and export an `APIRouter` named `router`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/demand", tags=["Demand"])


@router.get("/status")
def status():
    return {"status": "ok"}
```

The application automatically discovers these modules when `create_app()` runs. Restart the API (or use `--reload` locally), then check `/demand/status` and `/docs`. You do not need to edit `main.py` or `routers/__init__.py`.

Discovery loads public `.py` modules directly inside `routers/` in alphabetical order. Modules starting with `_`, subpackages, and modules without a `router` export are not registered. An invalid `router` export or module import error is logged with its module name and traceback, and that module is skipped. Other routers and the service probes stay available; check startup logs and `/docs` for missing endpoints. Keep module imports free of database connections; access the managed session inside requests. Use distinct URL prefixes for each member's endpoints. The optional `extra_routers` factory argument remains available for tests or explicit extensions; the same router object is registered only once, including re-exports and routers also passed explicitly. Routers that clash with built-in paths such as `/health` or `/ready` are rejected and logged without disabling the service.

**Windows-safe imports:** import driver classes such as `NoHostAvailable` from `cassandra.cluster` only inside the route function that uses them, never at module scope. Importing that module can load an unsupported native reactor on Windows before any request runs. A module that makes this mistake will be logged and skipped; its endpoints will be unavailable, while service probes and other modules continue to work. Keep unit-testable routes independent of driver imports wherever possible.

Inside a route, use `request.app.state.database.session` for prepared, bounded Cassandra reads. The session is opened and closed by the service. Use synchronous `def` routes for blocking driver operations (FastAPI runs these in its worker thread pool), or explicitly offload reads in async routes. Handle connection failures with a suitable 503 response. Do not execute unrestricted scans or introduce database writes in browser-facing endpoints.

Domain endpoints remain with their owners, as listed in `docs/ARCHITECTURE.md`. The hourly threat endpoint is a later release.

### M4 Europe map endpoints (#37)

- `GET /map?metric=job_ads&date=2026-11-02` — one value per country for a metric: `job_ads`, `demand`, `supply`, `gap`, `programmes`, `companies` or `threats`. `demand`, `supply` and `gap` also need `item`, the exact skill name (`&item=SIEM`). Every Europe-map country is listed; a country with nothing stored has `value: null`, never 0. For `threats` only, every other country with stored observations follows, ordered by code, so the world threat map (#40) reads the same endpoint; the other metrics never go beyond the Europe-map countries. The response also carries the metric's `unit`, `period` and a `note` on how to read it.
- `GET /map/country?country=HU&date=2026-11-02` — the country panel: Top 10 skills and certifications for that day, the latest workforce baseline, and up to 50 of that day's alerts (`alerts_truncated` says when there were more).
- `GET /map/meta` — the countries (EU27, EEA, UK, Switzerland), the metrics with their labels and `needs_item` (true for the metrics stored per Skill), and the accepted date range. Reference labels only; it reads no data. The dashboard takes the per-Skill metrics from here, so the list lives only in `map_data.py` (`ITEM_METRICS`).

`demand` and `supply` read `kind = skill` only: `item` is a Skill name, and
Certifications cannot be mapped yet.

Dates are UTC, from 2020-01-01 through today. Each read names a complete
partition key and a row limit; a partition larger than the limit is answered
with 503 rather than a truncated count. Storage problems are also 503, and
there is no sample-data fallback. The module is `routers/europe_map.py`, with
the queries in `map_data.py`.

### Live alert feed (#39)

`WS /live` pushes new drift (S6) and threat-burst (S8) alerts to browsers as they are raised. It is a WebSocket, so it is not listed in `/docs`. Every message is one JSON object:

```json
{"type": "status", "state": "connected", "message": "Connected. New alerts appear as they are raised."}
{"type": "alert", "alert": {"country": "HU", "day": "2026-11-15", "created_at": "2026-11-15T13:00:00Z",
                            "alert_type": "threat_burst", "subject": "phishing", "score": 0.91, "details": "..."}}
```

- `state` is `connecting`, `connected`, `unavailable` (Kafka cannot be reached; the API keeps retrying) or `disabled`. A status is sent on connect, on every change, and every 25 seconds as a heartbeat.
- `alert` has the fields of `shared/schemas/alert.schema.json`; `details` is `""` when the producer left it out. Messages that do not fit the schema are skipped and logged, never forwarded.
- The feed starts now: it does not replay earlier alerts. Earlier alerts of a day are in `GET /map/country`.
- A browser that falls more than 100 alerts behind gets a status saying some were skipped, instead of unbounded buffering. Browsers send nothing; anything they send is ignored.
- Browser origins must be in `API_CORS_ORIGINS`, as for REST; other origins are refused with 403.

The API reads the `alerts.drift` and `alerts.threats` topics from Kafka, as `docs/ARCHITECTURE.md` lists. Each API process follows every partition from its end, without joining a consumer group or committing offsets, so every replica broadcasts every alert and the sink's offsets are untouched. It connects to Kafka only when the first browser opens the feed; REST endpoints and `/ready` never depend on Kafka. During a broker outage the consumer keeps its position and the feed reports `unavailable` until the broker answers again.

Settings: `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`, which suits `uvicorn` on the host next to the root stack) and `API_LIVE_ALERTS=false` to switch the feed off.

**In Docker**, `api/compose.yml` sets `KAFKA_BOOTSTRAP_SERVERS=kafka:29092`, the broker's address inside the root stack's network. The standalone API container is not on that network, so the feed reports `unavailable` until you connect it (REST keeps working either way):

```bash
docker network connect "$(docker inspect ace-kafka --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}')" "$(docker compose -f api/compose.yml ps -q api)"
```

The feed connects within a few seconds. `host.docker.internal:9092` does not work for Kafka, because the broker tells host clients to continue on `localhost:9092`.

### M5 endpoints

The M5 read-only endpoints are:

- `GET /education?country=DE` — programmes and workforce baseline for a country.
- `GET /emerging?country=DE&day=2026-11-02` — S9 snapshot for a country and day.
- `GET /companies?country=DE&day=2026-11-02` — S10 estimates for a country and day.
- `GET /platforms` — evidence rows for CyberSeek, ENISA ECSF and the ISC2 Workforce Study.

The reads use bounded Cassandra partition queries. `/education` requires an
uppercase ISO alpha-2 country code; `/emerging` and `/companies` also require
an ISO date. These endpoints return database-backed data only: there is no
sample-data fallback.
