"""FastAPI factory for the master plane.

Run with:
    uvicorn src.master_plane.app:create_app --factory --port 9090
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.logging_setup import configure_logging
from src.master_plane.db import make_pool, migrate
from src.master_plane.routes import router
from src.master_plane.settings import MasterSettings, get_master_settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: MasterSettings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    pool = await make_pool(settings.postgres_dsn.get_secret_value())
    await migrate(pool)
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


def create_app(settings: MasterSettings | None = None) -> FastAPI:
    cfg = settings or get_master_settings()
    app = FastAPI(
        title="LLM Privacy Gateway — Master Plane",
        description=(
            "Commerce plane: customer accounts, plan management, license issuance, "
            "and content-free telemetry intake. NEVER receives customer prompts."
        ),
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.state.settings = cfg
    app.include_router(router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
