"""FastAPI application factory for the data plane.

Wires settings → keys → backends → detectors → retrieval → pipeline →
routes. Backend choice flows from env-driven Settings:

  * Customer auth         GATEWAY_CUSTOMER_STORE_BACKEND (memory | postgres)
  * Audit storage         GATEWAY_AUDIT_STORE_BACKEND    (memory | postgres)
  * Rule storage          GATEWAY_RULE_STORE_BACKEND     (memory | postgres)
  * Detector B            GATEWAY_NER_BACKEND            (stub | onnx | transformers)
  * Detector C            GATEWAY_VLLM_BACKEND           (stub | http)
  * Crypto keys           GATEWAY_KEY_STORE_BACKEND      (env | vault)
  * Master plane          GATEWAY_MASTER_PLANE_MOCK      (true | false)
  * Licensing             GATEWAY_LICENSE_REQUIRED       (false | true)
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
from src.keystore import resolve_keys
from src.licensing import LicenseInvalidError, verify
from src.logging_setup import configure_logging
from src.master_client.client import MasterPlaneClient, MockMasterPlaneClient
from src.observability import install_tracing, metrics_endpoint
from src.proxy.auth import CustomerDirectory, get_directory
from src.proxy.customer_seed import seed_directory
from src.proxy.pipeline import Pipeline, PipelineDeps
from src.proxy.routes import router as openai_router
from src.proxy.upstream import UpstreamForwarder
from src.retrieval import HybridRetriever
from src.rules import InMemoryRuleStore, RuleExceptionStore
from src.substitution import SessionMapStore


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    audit_loop_thread: Thread | None = getattr(app.state, "audit_loop_thread", None)
    audit_loop: asyncio.AbstractEventLoop | None = getattr(app.state, "audit_loop", None)
    shutdown_tracing = install_tracing("gateway-data-plane", settings.otel_exporter_otlp_endpoint)
    try:
        yield
    finally:
        shutdown_tracing()
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
        pg_pool = getattr(app.state, "pg_pool", None)
        if pg_pool is not None:
            await pg_pool.close()
        if audit_loop is not None:
            audit_loop.call_soon_threadsafe(audit_loop.stop)
        if audit_loop_thread is not None:
            audit_loop_thread.join(timeout=5)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    # 1. License gate (fail closed before any state is initialised).
    if cfg.license_required:
        if cfg.license_token is None or cfg.license_public_key_pem is None:
            raise RuntimeError(
                "GATEWAY_LICENSE_REQUIRED=true but license token or public key is missing"
            )
        try:
            verify(
                token=cfg.license_token.get_secret_value(),
                public_key_pem=cfg.license_public_key_pem,
                country_code=cfg.country_code,
            )
        except LicenseInvalidError as exc:
            raise RuntimeError(f"license verification failed: {exc}") from exc

    app = FastAPI(
        title="LLM Privacy Gateway — Data Plane",
        version=cfg.software_version,
        description=(
            "Privacy-preserving proxy between MENA enterprises and cloud LLM "
            "providers. OpenAI-compatible API."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = cfg

    # 2. Crypto keys via env or Vault.
    keys = resolve_keys(cfg)

    # 3. Postgres pool (shared by audit, rules, customers when configured).
    pg_pool = _maybe_postgres_pool(cfg)
    if pg_pool is not None:
        app.state.pg_pool = pg_pool

    # 4. Audit backend.
    audit_backend: object
    if cfg.audit_store_backend == "postgres":
        audit_backend, audit_loop, audit_thread = _bring_up_postgres_audit(cfg)
        app.state.audit_loop = audit_loop
        app.state.audit_loop_thread = audit_thread
    else:
        audit_backend = InMemoryAuditBackend()

    app.state.audit = AuditWriter(
        backend=audit_backend,  # type: ignore[arg-type]
        encryption_key=keys.audit_encryption_key,
        hmac_key=keys.audit_hmac_key,
    )

    # 5. Session map store.
    app.state.session_store = SessionMapStore(
        key=keys.session_map_key, idle_timeout_s=cfg.session_map_idle_timeout_s
    )

    # 6. Rules + exceptions.
    if cfg.rule_store_backend == "postgres" and pg_pool is not None:
        from src.rules.postgres_backend import PostgresExceptionStore, PostgresRuleStore

        rule_store: object = PostgresRuleStore(pool=pg_pool)
        exception_store: object = PostgresExceptionStore(pool=pg_pool)
    else:
        rule_store = InMemoryRuleStore()
        exception_store = RuleExceptionStore()
    app.state.rule_store = rule_store
    app.state.exception_store = exception_store

    # 7. Customer store: Postgres (production) or in-memory directory (dev).
    if cfg.customer_store_backend == "postgres" and pg_pool is not None:
        from src.proxy.customer_store import PostgresCustomerStore

        app.state.customer_store = PostgresCustomerStore(
            pool=pg_pool, encryption_key=keys.audit_encryption_key
        )
    else:
        directory: CustomerDirectory = get_directory()
        app.state.customer_store = directory
        seed_directory(directory)

    # 8. Detectors.
    structural = StructuralDetector()
    ner = make_ner_detector(
        backend=cfg.ner_backend,
        model_path=cfg.ner_model_path,
        tokenizer_path=cfg.ner_tokenizer_path,
        hf_model=cfg.ner_hf_model,
        aggregation=cfg.ner_aggregation,
    )
    contextual, vllm_client = make_contextual_detector(
        backend=cfg.vllm_backend,
        vllm_url=cfg.vllm_url,
        model=cfg.vllm_model,
        timeout_s=cfg.vllm_request_timeout_s,
    )
    if vllm_client is not None:
        app.state.vllm_client = vllm_client

    # 9. Retrieval.
    retriever = HybridRetriever(rule_store=rule_store, top_k_tier3=cfg.vllm_top_k_rules)  # type: ignore[arg-type]

    # 10. Upstream forwarder.
    upstream_client = httpx.AsyncClient(timeout=cfg.upstream_request_timeout_s)
    app.state.upstream_client = upstream_client
    upstream = UpstreamForwarder(client=upstream_client, base_url=cfg.upstream_openai_base_url)

    # 11. Master-plane client.
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

    # 12. Pipeline.
    app.state.pipeline = Pipeline(
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

    # 13. Routes.
    app.include_router(openai_router)
    app.include_router(dashboard_router, prefix="/dashboard")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    if cfg.metrics_enabled:
        app.add_route("/metrics", metrics_endpoint)

    return app


def _maybe_postgres_pool(cfg: Settings) -> object | None:
    """Open a single shared asyncpg pool if any backend is set to postgres."""
    needs_pg = (
        cfg.audit_store_backend == "postgres"
        or cfg.rule_store_backend == "postgres"
        or cfg.customer_store_backend == "postgres"
    )
    if not needs_pg or cfg.postgres_dsn is None:
        return None
    import asyncpg  # local import keeps the in-memory path dep-free

    from src.db import migrate

    async def _setup() -> object:
        pool = await asyncpg.create_pool(dsn=cfg.postgres_dsn.get_secret_value())  # type: ignore[union-attr]
        await migrate(pool)
        return pool

    # Run on the current loop if there is one (uvicorn lifespan); otherwise
    # we spin a fresh loop for the duration of pool creation.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return None  # pool will be created in lifespan
    except RuntimeError:
        pass
    return asyncio.run(_setup())


def _bring_up_postgres_audit(
    cfg: Settings,
) -> tuple[object, asyncio.AbstractEventLoop, Thread]:
    """Start an asyncpg-backed audit backend on a dedicated loop thread."""
    if cfg.postgres_dsn is None:
        raise ValueError("audit_store_backend=postgres requires postgres_dsn")
    import asyncpg

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
