"""FastAPI application factory.

Wires settings → keys → backends → detectors → retrieval → pipeline →
routes. Backend choice is driven by environment variables so the same
binary runs against in-memory backends in CI and against Postgres + Redis
+ vLLM in production.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Thread

import httpx
from fastapi import FastAPI

from src.audit import AuditWriter, InMemoryAuditBackend
from src.config import Settings, get_settings
from src.dashboard.routes import router as dashboard_router
from src.detectors import (
    StructuralDetector,
    make_contextual_detector,
    make_ner_detector,
)
from src.logging_setup import configure_logging
from src.master_client.client import MasterPlaneClient, MockMasterPlaneClient
from src.proxy.pipeline import Pipeline, PipelineDeps
from src.proxy.routes import router as openai_router
from src.proxy.upstream import UpstreamForwarder
from src.retrieval import HybridRetriever
from src.rules import InMemoryRuleStore, RuleExceptionStore
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
    audit_loop_thread: Thread | None = getattr(app.state, "audit_loop_thread", None)
    audit_loop: asyncio.AbstractEventLoop | None = getattr(app.state, "audit_loop", None)
    try:
        yield
    finally:
        for client in (
            getattr(app.state, "vllm_client", None),
            getattr(app.state, "upstream_client", None),
            getattr(app.state, "master_http_client", None),
        ):
            if client is not None:
                await client.aclose()
        store: SessionMapStore = app.state.session_store
        for rid in list(store._maps.keys()):
            store.close(rid)
        if audit_loop is not None:
            audit_loop.call_soon_threadsafe(audit_loop.stop)
        if audit_loop_thread is not None:
            audit_loop_thread.join(timeout=5)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    app = FastAPI(
        title="LLM Privacy Gateway",
        version=cfg.software_version,
        description=(
            "Privacy-preserving proxy between MENA enterprises and cloud LLM "
            "providers. OpenAI-compatible API."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = cfg

    # Crypto material
    audit_enc_key = _hex_to_bytes(
        "audit_encryption_key", cfg.audit_encryption_key.get_secret_value(), 32
    )
    audit_hmac_key = _hex_to_bytes("audit_hmac_key", cfg.audit_hmac_key.get_secret_value(), 32)
    session_key = _hex_to_bytes("session_map_key", cfg.session_map_key.get_secret_value(), 32)

    # Audit backend selection
    audit_backend: object
    if cfg.audit_store_backend == "postgres":
        audit_backend, audit_loop, audit_thread = _bring_up_postgres_audit(cfg)
        app.state.audit_loop = audit_loop
        app.state.audit_loop_thread = audit_thread
    else:
        audit_backend = InMemoryAuditBackend()

    from src.audit.writer import AuditBackend  # local import for typing

    app.state.audit = AuditWriter(
        backend=audit_backend,  # type: ignore[arg-type]
        encryption_key=audit_enc_key,
        hmac_key=audit_hmac_key,
    )
    _ = AuditBackend  # silence unused-import: kept for documentation

    # Session map
    app.state.session_store = SessionMapStore(
        key=session_key, idle_timeout_s=cfg.session_map_idle_timeout_s
    )

    # Rules + exceptions (in-memory backend always wired; tests register seed rules)
    rule_store = InMemoryRuleStore()
    exception_store = RuleExceptionStore()
    app.state.rule_store = rule_store
    app.state.exception_store = exception_store

    # Detectors
    structural = StructuralDetector()
    ner = make_ner_detector(
        backend=cfg.ner_backend,
        model_path=cfg.ner_model_path,
        tokenizer_path=cfg.ner_tokenizer_path,
    )
    contextual, vllm_client = make_contextual_detector(
        backend=cfg.vllm_backend,
        vllm_url=cfg.vllm_url,
        model=cfg.vllm_model,
        timeout_s=cfg.vllm_request_timeout_s,
    )
    if vllm_client is not None:
        app.state.vllm_client = vllm_client

    # Retrieval
    retriever = HybridRetriever(rule_store=rule_store, top_k_tier3=cfg.vllm_top_k_rules)

    # Upstream forwarder
    upstream_client = httpx.AsyncClient(timeout=cfg.upstream_request_timeout_s)
    app.state.upstream_client = upstream_client
    upstream = UpstreamForwarder(client=upstream_client, base_url=cfg.upstream_openai_base_url)

    # Master plane client
    if cfg.master_plane_mock or not cfg.master_plane_url:
        master_client: MasterPlaneClient | MockMasterPlaneClient = MockMasterPlaneClient()
    else:
        master_http = httpx.AsyncClient(base_url=cfg.master_plane_url, timeout=10.0)
        app.state.master_http_client = master_http
        master_client = MasterPlaneClient(
            client=master_http,
            api_key=(
                cfg.master_plane_api_key.get_secret_value() if cfg.master_plane_api_key else ""
            ),
        )
    app.state.master_client = master_client

    # Pipeline
    pipeline = Pipeline(
        PipelineDeps(
            settings=cfg,
            structural=structural,
            ner=ner,
            contextual=contextual,
            retriever=retriever,
            exception_store=exception_store,
            session_store=app.state.session_store,
            audit=app.state.audit,
            upstream=upstream,
        )
    )
    app.state.pipeline = pipeline

    app.include_router(openai_router)
    app.include_router(dashboard_router, prefix="/dashboard")

    # Seed the in-memory customer directory from GATEWAY_CUSTOMERS_FILE /
    # GATEWAY_CUSTOMERS_JSON. Production uses customer_config in Postgres
    # — this is the single-process / dev / sovereign-offline path.
    from src.proxy.auth import get_directory
    from src.proxy.customer_seed import seed_directory

    seed_directory(get_directory())

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    return app


def _bring_up_postgres_audit(
    cfg: Settings,
) -> tuple[object, asyncio.AbstractEventLoop, Thread]:
    """Stand up an asyncpg pool on a dedicated event loop thread and return an
    audit backend that proxies sync writes onto it.

    The audit writer's interface is sync (writes are blocking, fail-closed
    per CLAUDE.md hard rule #5). We host an asyncio loop in a worker
    thread and bridge sync calls into it.
    """
    if cfg.postgres_dsn is None:
        raise ValueError("audit_store_backend=postgres requires postgres_dsn")
    import asyncpg  # local import keeps the in-memory path dep-free

    from src.audit.postgres_backend import PostgresAuditBackend
    from src.db import migrate

    loop = asyncio.new_event_loop()
    pool_holder: dict[str, asyncpg.Pool] = {}

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = Thread(target=_run, daemon=True, name="audit-loop")
    thread.start()

    async def _setup() -> None:
        pool = await asyncpg.create_pool(
            dsn=cfg.postgres_dsn.get_secret_value() if cfg.postgres_dsn else "",
            min_size=1,
            max_size=10,
        )
        pool_holder["pool"] = pool
        await migrate(pool)

    asyncio.run_coroutine_threadsafe(_setup(), loop).result()
    return PostgresAuditBackend(pool=pool_holder["pool"], loop=loop), loop, thread
