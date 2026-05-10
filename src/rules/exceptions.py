"""Tier 1 exception entries (customer-validated false positives).

CLAUDE.md hard rule #4: Tier 1 rules are immutable. Customer overrides
land here as exception entries that the merge engine consults to suppress
specific detections without mutating the rules themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from src.merge.engine import ExceptionEntry
from src.tenancy import require_customer


@dataclass(frozen=True, slots=True)
class RuleException:
    """Persistable exception entry."""

    exception_id: str
    customer_id: str
    rule_id: str | None
    entity_type: str | None
    text_match: str | None
    note: str | None = None


class RuleExceptionStore:
    """In-memory exception store. Postgres backend lives in `postgres.py`."""

    def __init__(self) -> None:
        self._by_customer: dict[str, list[RuleException]] = {}
        self._lock = Lock()

    async def list_active(self) -> list[ExceptionEntry]:
        ctx = require_customer()
        with self._lock:
            entries = list(self._by_customer.get(ctx.customer_id, ()))
        return [
            ExceptionEntry(rule_id=e.rule_id, entity_type=e.entity_type, text_match=e.text_match)
            for e in entries
        ]

    async def add(self, exc: RuleException) -> None:
        ctx = require_customer()
        if exc.customer_id != ctx.customer_id:
            raise PermissionError("exception customer_id does not match request scope")
        with self._lock:
            self._by_customer.setdefault(ctx.customer_id, []).append(exc)

    async def remove(self, exception_id: str) -> None:
        ctx = require_customer()
        with self._lock:
            entries = self._by_customer.get(ctx.customer_id, [])
            self._by_customer[ctx.customer_id] = [
                e for e in entries if e.exception_id != exception_id
            ]
