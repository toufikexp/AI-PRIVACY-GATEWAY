from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

PlanTier = Literal["starter", "professional", "enterprise", "sovereign"]


class MissingTenantScopeError(RuntimeError):
    """Raised when an operation that requires a customer scope runs without one.

    Hard rule (CLAUDE.md #3): every DB query, cache key, log line, and audit
    record carries `customer_id`. A query reaching the data layer without one
    is an isolation bug — fail closed rather than risk cross-tenant leakage.
    """


@dataclass(frozen=True, slots=True)
class CustomerContext:
    """Per-request tenant binding.

    Constructed at the API boundary (auth dependency) and propagated through
    the request via a contextvar. All downstream code reads it via
    `require_customer()`; never via globals.
    """

    customer_id: str
    country_code: str
    plan: PlanTier

    def __post_init__(self) -> None:
        if not self.customer_id:
            raise MissingTenantScopeError("customer_id must be a non-empty string")
        if len(self.country_code) != 2 or not self.country_code.isalpha():
            raise ValueError(f"country_code must be ISO 3166-1 alpha-2; got {self.country_code!r}")


_current: ContextVar[CustomerContext | None] = ContextVar("current_customer", default=None)


def bind_customer(ctx: CustomerContext) -> object:
    """Bind a customer context to the current request scope.

    Returns a token usable with `reset_customer` to undo the binding.
    """
    return _current.set(ctx)


def reset_customer(token: object) -> None:
    _current.reset(token)  # type: ignore[arg-type]


def current_customer() -> CustomerContext | None:
    return _current.get()


def require_customer() -> CustomerContext:
    """Read the current customer context or raise.

    This is the canonical accessor used by detectors, retrieval, audit, and
    substitution. Never accept a `customer_id` parameter at module level —
    always require it via this function so the invariant is centralized.
    """
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantScopeError(
            "operation requires a CustomerContext; bind one at the API boundary"
        )
    return ctx
