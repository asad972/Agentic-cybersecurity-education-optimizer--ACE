import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager
import logging

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .database import CassandraConnection

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, database=None,
               extra_routers: Sequence[APIRouter] = ()) -> FastAPI:
    settings = settings or Settings.from_env()
    database = database if database is not None else CassandraConnection(settings)

    @asynccontextmanager
    async def lifespan(app):
        # Stay observable while Cassandra starts. /ready retries failed connections.
        try:
            await asyncio.to_thread(database.open)
        except Exception as error:
            log.warning("Cassandra startup failed (%s)", type(error).__name__)
        try:
            yield
        finally:
            await asyncio.to_thread(database.close)

    app = FastAPI(title="ACE API", version="0.1.0", lifespan=lifespan,
                  description="Service skeleton. Dashboard owners register their own routers.")
    app.state.database = database
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                       allow_methods=["GET"], allow_headers=["Accept", "Content-Type"])

    @app.get("/health", tags=["Service"])
    def health():
        """Process liveness; does not imply database readiness."""
        return {"status": "ok"}

    @app.get("/ready", tags=["Service"])
    def ready(request: Request):
        if not request.app.state.database.ready():
            raise HTTPException(status_code=503, detail="Cassandra is unavailable")
        return {"status": "ready", "database": "connected"}

    for router in extra_routers:
        app.include_router(router)
    return app


app = create_app()
