"""Audit writer with hash chain + HMAC tamper-evidence.

Per ARCHITECTURE §7.4.2 and audit-and-security skill:
- Each record contains content_hash + previous_record_hash + HMAC signature.
- Sensitive fields (e.g. detection counts that could be linkable) are
  AES-256-GCM encrypted with a per-deployment key.
- Writes are blocking: if the backend raises, the request fails closed
  (CLAUDE.md hard rule #5).

This module ships an in-memory backend used by tests and for the proxy
skeleton; a PostgreSQL backend lands with Phase 1 schema work.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.tenancy import require_customer

NONCE_BYTES = 12
GENESIS_HASH = "0" * 64  # sha256 hex digest of "no previous record"


class AuditChainBroken(RuntimeError):
    """Verification detected a chain mismatch — possible tampering."""


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Persisted audit row; sensitive payload kept as AES-GCM ciphertext."""

    record_id: str
    customer_id: str
    request_id: str
    event_type: str
    occurred_at: float  # epoch seconds (UTC)
    payload_nonce: bytes
    payload_ciphertext: bytes
    content_hash: str  # sha256 hex of the canonical record body
    prev_hash: str  # content_hash of previous record in chain
    hmac_signature: str  # hex hmac-sha256 over (content_hash|prev_hash)


class AuditBackend(Protocol):
    def append(self, record: AuditRecord) -> None: ...
    def all(self) -> list[AuditRecord]: ...
    def latest_hash(self) -> str: ...


class InMemoryAuditBackend:
    """Append-only in-memory backend. Good enough for tests and CI."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = Lock()

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)

    def all(self) -> list[AuditRecord]:
        with self._lock:
            return list(self._records)

    def latest_hash(self) -> str:
        with self._lock:
            return self._records[-1].content_hash if self._records else GENESIS_HASH


class AuditWriter:
    """Builds and appends tamper-evident audit records.

    The writer is `customer_id`-aware via `require_customer()`. Callers must
    bind a customer scope before calling `record()`; otherwise the call
    raises `MissingTenantScopeError` and the request fails closed.
    """

    def __init__(
        self,
        *,
        backend: AuditBackend,
        encryption_key: bytes,
        hmac_key: bytes,
    ) -> None:
        if len(encryption_key) != 32:
            raise ValueError("audit encryption key must be 32 bytes (AES-256)")
        if len(hmac_key) < 32:
            raise ValueError("audit HMAC key must be at least 32 bytes")
        self._aead = AESGCM(encryption_key)
        self._hmac_key = hmac_key
        self._backend = backend

    def record(
        self,
        *,
        request_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> AuditRecord:
        ctx = require_customer()
        nonce = os.urandom(NONCE_BYTES)
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ct = self._aead.encrypt(nonce, canonical_payload, ctx.customer_id.encode())

        occurred_at = time.time()
        record_id = str(uuid.uuid4())

        body = json.dumps(
            {
                "record_id": record_id,
                "customer_id": ctx.customer_id,
                "request_id": request_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload_nonce": nonce.hex(),
                "payload_ciphertext": ct.hex(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        content_hash = hashlib.sha256(body).hexdigest()
        prev_hash = self._backend.latest_hash()
        signature = hmac.new(
            self._hmac_key,
            f"{content_hash}|{prev_hash}".encode(),
            hashlib.sha256,
        ).hexdigest()

        record = AuditRecord(
            record_id=record_id,
            customer_id=ctx.customer_id,
            request_id=request_id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload_nonce=nonce,
            payload_ciphertext=ct,
            content_hash=content_hash,
            prev_hash=prev_hash,
            hmac_signature=signature,
        )
        # Blocking write — fail closed if backend raises.
        self._backend.append(record)
        return record

    def verify_chain(self) -> None:
        """Recompute the chain end-to-end. Raises AuditChainBroken on mismatch.

        Verifies prev_hash linkage and HMAC signatures. Does NOT decrypt
        payloads; payload integrity is bound by AES-GCM's own auth tag.
        """
        records = self._backend.all()
        prev = GENESIS_HASH
        for rec in records:
            if rec.prev_hash != prev:
                raise AuditChainBroken(
                    f"record {rec.record_id}: prev_hash mismatch "
                    f"(expected {prev}, got {rec.prev_hash})"
                )
            body = json.dumps(
                {
                    "record_id": rec.record_id,
                    "customer_id": rec.customer_id,
                    "request_id": rec.request_id,
                    "event_type": rec.event_type,
                    "occurred_at": rec.occurred_at,
                    "payload_nonce": rec.payload_nonce.hex(),
                    "payload_ciphertext": rec.payload_ciphertext.hex(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            recomputed = hashlib.sha256(body).hexdigest()
            if recomputed != rec.content_hash:
                raise AuditChainBroken(f"record {rec.record_id}: content_hash mismatch")
            expected_sig = hmac.new(
                self._hmac_key,
                f"{rec.content_hash}|{rec.prev_hash}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected_sig, rec.hmac_signature):
                raise AuditChainBroken(f"record {rec.record_id}: HMAC signature invalid")
            prev = rec.content_hash
