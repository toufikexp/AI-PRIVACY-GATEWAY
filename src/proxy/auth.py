"""Customer authentication and tenant binding for the proxy.

MVP uses a simple API-key header (`Authorization: Bearer sk-...`). The key
maps to a `CustomerContext`; that context is bound to the request via
`bind_customer` and read by every downstream module via `require_customer`.

Production wires this dependency to the `customer_config` table; for now we
back it with an in-memory dict seeded from settings/tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from src.tenancy import CustomerContext, bind_customer

_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True, slots=True)
class CustomerRecord:
    """Resolved customer info — what `customer_config` will return at Phase 2."""

    customer_id: str
    country_code: str
    plan: str
    upstream_provider_key: str  # provider API key the proxy forwards with


class CustomerDirectory:
    """In-memory customer directory. Replaced by DB lookup in Phase 2."""

    def __init__(self) -> None:
        self._by_api_key: dict[str, CustomerRecord] = {}

    def register(self, api_key: str, record: CustomerRecord) -> None:
        self._by_api_key[api_key] = record

    def lookup(self, api_key: str) -> CustomerRecord | None:
        return self._by_api_key.get(api_key)


_directory = CustomerDirectory()


def get_directory() -> CustomerDirectory:
    return _directory


def _parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization[len(_BEARER_PREFIX) :].strip()


async def authenticate(
    authorization: str | None = Header(default=None),
    directory: CustomerDirectory = Depends(get_directory),
) -> CustomerRecord:
    """FastAPI dependency: resolve API key → CustomerRecord.

    Side effect: binds a `CustomerContext` to the contextvar so downstream
    code can call `require_customer()` without threading it through every
    function signature.
    """
    api_key = _parse_bearer(authorization)
    record = directory.lookup(api_key)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown API key",
        )
    plan_normalized = record.plan.lower()
    if plan_normalized not in {"starter", "professional", "enterprise", "sovereign"}:
        # Defense-in-depth; should be impossible if directory data is sane.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="invalid plan tier on customer record",
        )
    bind_customer(
        CustomerContext(
            customer_id=record.customer_id,
            country_code=record.country_code,
            plan=plan_normalized,  # type: ignore[arg-type]
        )
    )
    return record
