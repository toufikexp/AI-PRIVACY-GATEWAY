"""Customer directory backed by Postgres (`customer_config` table).

API keys are stored as bcrypt hashes — the gateway never persists raw keys.
On `lookup(api_key)` we iterate active hashes for the key prefix and
constant-time-compare. Prefix indexing keeps this O(1) when there are tens
of thousands of customers; bcrypt's per-hash cost (~50ms) only fires for
candidates sharing the prefix.

Plan tier and upstream provider key live alongside the hash. The
`upstream_provider_key` column is encrypted at rest with AES-256-GCM via
the audit_encryption_key (re-used as a generic "row encryption" key for
this table) so a leaked DB dump does not expose customer LLM credentials.

Production deployments rotate API keys via the admin CLI:
    python -m src.proxy.customer_admin create --customer cust-1 \\
        --country DZ --plan enterprise --upstream-key sk-proj-...
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Protocol

import asyncpg
import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.proxy.auth import CustomerRecord


@dataclass(frozen=True, slots=True)
class _Row:
    customer_id: str
    api_key_prefix: str
    api_key_hash: bytes
    country_code: str
    plan: str
    upstream_provider_key_nonce: bytes
    upstream_provider_key_ct: bytes
    failure_mode: str
    enabled: bool


class CustomerStore(Protocol):
    async def lookup(self, api_key: str) -> CustomerRecord | None: ...


KEY_PREFIX_LEN = 12  # first N chars of the API key, used as a coarse index
NONCE_BYTES = 12


class PostgresCustomerStore:
    """Async customer store backed by `customer_config`.

    Lookup path: read all rows with the same api_key_prefix → bcrypt-check
    each candidate → on match, AES-GCM-decrypt the upstream key and return
    a CustomerRecord. Rows are cached by prefix for 30s with the result of
    the bcrypt check keyed on the full api_key (still hashed in cache).
    """

    def __init__(self, *, pool: asyncpg.Pool, encryption_key: bytes) -> None:
        if len(encryption_key) != 32:
            raise ValueError("customer-store encryption_key must be 32 bytes (AES-256)")
        self._pool = pool
        self._aead = AESGCM(encryption_key)

    async def lookup(self, api_key: str) -> CustomerRecord | None:
        if len(api_key) < KEY_PREFIX_LEN:
            return None
        prefix = api_key[:KEY_PREFIX_LEN]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT customer_id, api_key_prefix, api_key_hash, country_code, plan,
                       upstream_provider_key_nonce, upstream_provider_key_ct,
                       failure_mode, enabled
                FROM customer_config
                WHERE api_key_prefix = $1 AND enabled = true
                """,
                prefix,
            )
        for r in rows:
            if not bcrypt.checkpw(api_key.encode(), bytes(r["api_key_hash"])):
                continue
            upstream = self._aead.decrypt(
                bytes(r["upstream_provider_key_nonce"]),
                bytes(r["upstream_provider_key_ct"]),
                r["customer_id"].encode(),
            ).decode()
            return CustomerRecord(
                customer_id=r["customer_id"],
                country_code=r["country_code"],
                plan=r["plan"],
                upstream_provider_key=upstream,
            )
        return None

    # ----- Admin operations (called from customer_admin CLI) -----

    async def create(
        self,
        *,
        customer_id: str,
        country_code: str,
        plan: str,
        upstream_provider_key: str,
        failure_mode: str = "strict",
    ) -> str:
        """Create a customer; generate and return a fresh API key.

        The raw key is returned ONCE. After this method, only the bcrypt
        hash is persisted; the original cannot be recovered.
        """
        api_key = "sk-" + secrets.token_urlsafe(32)
        prefix = api_key[:KEY_PREFIX_LEN]
        hashed = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt(rounds=12))
        nonce = os.urandom(NONCE_BYTES)
        ct = self._aead.encrypt(nonce, upstream_provider_key.encode(), customer_id.encode())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO customer_config
                  (customer_id, api_key_prefix, api_key_hash, country_code, plan,
                   upstream_provider_key_nonce, upstream_provider_key_ct,
                   failure_mode, enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true)
                """,
                customer_id,
                prefix,
                hashed,
                country_code,
                plan,
                nonce,
                ct,
                failure_mode,
            )
        return api_key

    async def rotate_key(self, *, customer_id: str) -> str:
        """Issue a new key for an existing customer; old key invalidated."""
        api_key = "sk-" + secrets.token_urlsafe(32)
        prefix = api_key[:KEY_PREFIX_LEN]
        hashed = bcrypt.hashpw(api_key.encode(), bcrypt.gensalt(rounds=12))
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE customer_config
                SET api_key_prefix = $2, api_key_hash = $3
                WHERE customer_id = $1
                """,
                customer_id,
                prefix,
                hashed,
            )
        if result.endswith("UPDATE 0"):
            raise LookupError(f"unknown customer_id {customer_id!r}")
        return api_key

    async def disable(self, *, customer_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE customer_config SET enabled = false WHERE customer_id = $1",
                customer_id,
            )


class InMemoryCustomerStore:
    """Used by tests and the in-memory mode."""

    def __init__(self) -> None:
        self._by_key: dict[str, CustomerRecord] = {}

    def register(self, api_key: str, record: CustomerRecord) -> None:
        self._by_key[api_key] = record

    async def lookup(self, api_key: str) -> CustomerRecord | None:
        return self._by_key.get(api_key)
