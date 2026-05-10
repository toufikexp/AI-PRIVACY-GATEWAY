"""Master-plane Postgres schema and helpers."""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   text PRIMARY KEY,
    company_name  text NOT NULL,
    contact_email text,
    country_code  text NOT NULL,
    plan          text NOT NULL CHECK (plan IN ('starter','professional','enterprise','sovereign')),
    failure_mode  text NOT NULL DEFAULT 'strict',
    rule_edits_locked boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS licenses (
    license_id    text PRIMARY KEY,
    customer_id   text NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    token         text NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);
CREATE INDEX IF NOT EXISTS licenses_customer_idx ON licenses (customer_id);

-- Telemetry aggregates: structured numeric/categorical fields only.
-- A foreign key to customers makes the multi-tenant scope explicit.
CREATE TABLE IF NOT EXISTS telemetry (
    id              bigserial PRIMARY KEY,
    customer_id     text NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
    plane           text NOT NULL,
    country_code   text NOT NULL,
    field           text NOT NULL,
    value_numeric   double precision,
    value_text      text,
    value_bool      boolean,
    received_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS telemetry_customer_idx
    ON telemetry (customer_id, received_at DESC);
"""


async def make_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)


async def migrate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
