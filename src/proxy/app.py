"""FastAPI application factory.

Builds the data-plane proxy: logging, settings, health endpoints, and the
OpenAI-compatible router. Singletons (audit writer, session map store) are
stored on `app.state` so tests can swap them by overriding dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.audit import AuditWriter, InMemoryAuditBackend
from src.config import Settings, get_settings
from src.logging_setup import configure_logging
from src.proxy.routes import router as openai_router
from src.substitution import SessionMapStore


def _hex_to_bytes(label: str, hex_value: str, expected_len: int) -> bytes:
    raw = bytes.fromhex(hex_value)
    if len(raw) != expected_len:
        raise ValueError(f"{label} must decode to {expected_len} bytes; got {len(raw)}")
    return raw


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    yield
    # On shutdown: purge any lingering session maps so plaintext does not
    # outlive the process. The store also guarantees no disk persistence.
    store: SessionMapStore = app.state.session_store
    for rid in list(store._maps.keys()):
        store.close(rid)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    app = FastAPI(
        title="LLM Privacy Gateway",
        version="0.1.0dev0",
        description=(
            "Privacy-preserving proxy between MENA enterprises and cloud LLM "
            "providers. OpenAI-compatible API. Phase 1 baseline."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = cfg

    audit_enc_key = _hex_to_bytes(
        "audit_encryption_key", cfg.audit_encryption_key.get_secret_value(), 32
    )
    audit_hmac_key = _hex_to_bytes("audit_hmac_key", cfg.audit_hmac_key.get_secret_value(), 32)
    session_key = _hex_to_bytes("session_map_key", cfg.session_map_key.get_secret_value(), 32)

    app.state.audit = AuditWriter(
        backend=InMemoryAuditBackend(),
        encryption_key=audit_enc_key,
        hmac_key=audit_hmac_key,
    )
    app.state.session_store = SessionMapStore(
        key=session_key, idle_timeout_s=cfg.session_map_idle_timeout_s
    )

    app.include_router(openai_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    return app
