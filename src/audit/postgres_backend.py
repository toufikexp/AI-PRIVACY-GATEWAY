"""Postgres-backed audit chain backend."""

from __future__ import annotations

import asyncio
from threading import Lock

import asyncpg

from src.audit.writer import GENESIS_HASH, AuditRecord


class PostgresAuditBackend:
    """Synchronous AuditBackend that proxies to an async asyncpg pool.

    The audit writer's interface is sync (it's called from within request
    handling). To keep the writer simple and the chain ordering correct
    we serialise writes through a lock and use `asyncio.run_coroutine_threadsafe`
    in the worker thread, OR — when called from within the event loop —
    use `loop.run_until_complete` on a dedicated audit loop.

    For simplicity and correctness we keep a small writer event loop on a
    dedicated thread; every audit write goes through it.
    """

    def __init__(self, *, pool: asyncpg.Pool, loop: asyncio.AbstractEventLoop) -> None:
        self._pool = pool
        self._loop = loop
        self._lock = Lock()
        self._cached_latest: str | None = None

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            future = asyncio.run_coroutine_threadsafe(self._insert(record), self._loop)
            future.result()
            self._cached_latest = record.content_hash

    def all(self) -> list[AuditRecord]:
        future = asyncio.run_coroutine_threadsafe(self._select_all(), self._loop)
        return future.result()

    def latest_hash(self) -> str:
        if self._cached_latest is not None:
            return self._cached_latest
        future = asyncio.run_coroutine_threadsafe(self._select_latest(), self._loop)
        latest = future.result()
        self._cached_latest = latest
        return latest

    async def _insert(self, record: AuditRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log
                  (record_id, customer_id, request_id, event_type, occurred_at,
                   payload_nonce, payload_ciphertext, content_hash, prev_hash, hmac_signature)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                record.record_id,
                record.customer_id,
                record.request_id,
                record.event_type,
                record.occurred_at,
                record.payload_nonce,
                record.payload_ciphertext,
                record.content_hash,
                record.prev_hash,
                record.hmac_signature,
            )

    async def _select_all(self) -> list[AuditRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM audit_log ORDER BY created_at, record_id")
        return [
            AuditRecord(
                record_id=r["record_id"],
                customer_id=r["customer_id"],
                request_id=r["request_id"],
                event_type=r["event_type"],
                occurred_at=r["occurred_at"],
                payload_nonce=bytes(r["payload_nonce"]),
                payload_ciphertext=bytes(r["payload_ciphertext"]),
                content_hash=r["content_hash"],
                prev_hash=r["prev_hash"],
                hmac_signature=r["hmac_signature"],
            )
            for r in rows
        ]

    async def _select_latest(self) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT content_hash FROM audit_log ORDER BY created_at DESC, record_id DESC LIMIT 1"
            )
        return row["content_hash"] if row else GENESIS_HASH
