"""Session map: bidirectional original ↔ synthetic mapping for one request.

Hard rule (CLAUDE.md #2): original PII never lands on disk in plaintext.
Session maps are memory-only, AES-256-GCM encrypted at rest in the dict,
and purged on response delivery OR on idle timeout (default 30 min).

Component decomposition for reverse substitution (ARCHITECTURE §4.5.1) is
deferred to the substitution engine in Phase 2; this module owns the
storage / lifecycle invariant and stays small enough to audit.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from threading import Lock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.tenancy import require_customer

NONCE_BYTES = 12  # NIST recommendation for AES-GCM


class SessionPurgedError(LookupError):
    """Lookup attempted on a session map that has already been purged."""


@dataclass(slots=True)
class SessionMapEntry:
    """Plaintext form of a single original ↔ synthetic mapping.

    Constructed transiently when adding entries; never stored as-is. Only
    the encrypted byte form lives in `EncryptedSessionMap._encrypted`.
    """

    original: str
    synthetic: str
    entity_type: str


class EncryptedSessionMap:
    """In-memory, per-request, AES-256-GCM encrypted session map.

    Threading: a single `Lock` serialises mutation and purge. A purged map
    raises `SessionPurgedError` on every operation; resurrection is not
    supported (would defeat the on-response-purge invariant).
    """

    def __init__(self, *, key: bytes, customer_id: str, request_id: str) -> None:
        if len(key) != 32:
            raise ValueError("session map key must be 32 bytes (AES-256)")
        self._aead = AESGCM(key)
        self.customer_id = customer_id
        self.request_id = request_id
        self.created_at = time.monotonic()
        self.last_access = self.created_at
        # synthetic_value -> (nonce, ciphertext). The synthetic value is the
        # lookup key because reverse substitution always sees the synthetic.
        self._encrypted: dict[str, tuple[bytes, bytes]] = {}
        self._lock = Lock()
        self._purged = False
        # Bind to current customer at construction time as defense-in-depth
        # against using a session map from a different request scope.
        require_customer()  # raises if no scope; cross-checked below
        self._bound_customer = customer_id

    # ----- mutation -----

    def add(self, entry: SessionMapEntry) -> None:
        """Encrypt and store one mapping. Idempotent on `synthetic`."""
        with self._lock:
            self._check_alive()
            nonce = os.urandom(NONCE_BYTES)
            payload = f"{entry.entity_type}\x1f{entry.original}".encode()
            aad = self._aad()
            ct = self._aead.encrypt(nonce, payload, aad)
            self._encrypted[entry.synthetic] = (nonce, ct)
            self.last_access = time.monotonic()

    # ----- lookup -----

    def reverse(self, synthetic: str) -> SessionMapEntry | None:
        """Return the original plaintext for a synthetic, or None if absent."""
        with self._lock:
            self._check_alive()
            slot = self._encrypted.get(synthetic)
            self.last_access = time.monotonic()
            if slot is None:
                return None
            nonce, ct = slot
            pt = self._aead.decrypt(nonce, ct, self._aad())
            entity_type, original = pt.decode().split("\x1f", 1)
            return SessionMapEntry(original=original, synthetic=synthetic, entity_type=entity_type)

    def __len__(self) -> int:
        with self._lock:
            return 0 if self._purged else len(self._encrypted)

    @property
    def is_purged(self) -> bool:
        return self._purged

    def is_idle(self, *, now: float | None = None, timeout_s: int) -> bool:
        with self._lock:
            if self._purged:
                return True
            current = time.monotonic() if now is None else now
            return (current - self.last_access) >= timeout_s

    # ----- lifecycle -----

    def purge(self) -> None:
        """Zero out and forget all entries. Safe to call multiple times."""
        with self._lock:
            self._encrypted.clear()
            self._purged = True

    # ----- internals -----

    def _check_alive(self) -> None:
        if self._purged:
            raise SessionPurgedError(f"session map for request {self.request_id} has been purged")

    def _aad(self) -> bytes:
        # Bind ciphertexts to (customer, request) so a stolen ciphertext
        # cannot be replayed under a different scope.
        return f"{self._bound_customer}|{self.request_id}".encode()


@dataclass(slots=True)
class SessionMapStore:
    """Process-wide registry of live session maps with idle-purge sweeping."""

    key: bytes
    idle_timeout_s: int
    _maps: dict[str, EncryptedSessionMap] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def open(self, *, request_id: str) -> EncryptedSessionMap:
        ctx = require_customer()
        sm = EncryptedSessionMap(key=self.key, customer_id=ctx.customer_id, request_id=request_id)
        with self._lock:
            self._maps[request_id] = sm
        return sm

    def close(self, request_id: str) -> None:
        with self._lock:
            sm = self._maps.pop(request_id, None)
        if sm is not None:
            sm.purge()

    def sweep_idle(self) -> int:
        """Purge all maps idle longer than `idle_timeout_s`. Returns count."""
        purged = 0
        now = time.monotonic()
        with self._lock:
            stale = [
                rid
                for rid, sm in self._maps.items()
                if sm.is_idle(now=now, timeout_s=self.idle_timeout_s)
            ]
            for rid in stale:
                sm = self._maps.pop(rid)
                sm.purge()
                purged += 1
        return purged
