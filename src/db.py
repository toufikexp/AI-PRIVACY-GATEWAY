"""Database pool factory and migration script."""

from __future__ import annotations

import asyncpg

# DDL is intentionally inlined here so a fresh dev environment can run
# `await migrate(pool)` once and have a working schema. Production deploys
# manage migrations via a dedicated tool (alembic etc.); this is the
# minimal-but-correct schema for the data plane.
SCHEMA_DDL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS rules (
    rule_id            text PRIMARY KEY,
    tier               smallint NOT NULL CHECK (tier IN (1, 2, 3)),
    entity_type        text NOT NULL,
    description        text NOT NULL,
    country_code       text,
    industry           text,
    customer_id        text,
    enabled            boolean NOT NULL DEFAULT true,
    confidence_floor   real NOT NULL DEFAULT 0.0,
    keywords           text[] NOT NULL DEFAULT '{}',
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rules_tenant_idx ON rules (customer_id, tier);
CREATE INDEX IF NOT EXISTS rules_country_idx ON rules (country_code, tier);

CREATE TABLE IF NOT EXISTS rule_exceptions (
    exception_id  text PRIMARY KEY,
    customer_id   text NOT NULL,
    rule_id       text,
    entity_type   text,
    text_match    text,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rule_exceptions_tenant_idx ON rule_exceptions (customer_id);

CREATE TABLE IF NOT EXISTS audit_log (
    record_id          text PRIMARY KEY,
    customer_id        text NOT NULL,
    request_id         text NOT NULL,
    event_type         text NOT NULL,
    occurred_at        double precision NOT NULL,
    payload_nonce      bytea NOT NULL,
    payload_ciphertext bytea NOT NULL,
    content_hash       text NOT NULL,
    prev_hash          text NOT NULL,
    hmac_signature     text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_tenant_idx ON audit_log (customer_id, created_at);
CREATE INDEX IF NOT EXISTS audit_log_chain_idx ON audit_log (created_at);

CREATE TABLE IF NOT EXISTS customer_config (
    customer_id              text PRIMARY KEY,
    country_code             text NOT NULL,
    plan                     text NOT NULL,
    api_key_hash             text NOT NULL,
    upstream_provider_key    text NOT NULL,
    failure_mode             text NOT NULL DEFAULT 'strict',
    created_at               timestamptz NOT NULL DEFAULT now()
);
"""


async def make_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def migrate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
