"""Customer authentication and tenant binding for the proxy.

`Authorization: Bearer sk-…` resolves to a `CustomerRecord` via the
configured `CustomerStore` (Postgres or in-memory). The match binds a
`CustomerContext` to the request contextvar; every downstream module
reads it via `require_customer()`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from src.tenancy import CustomerContext, bind_customer

_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True, slots=True)
class CustomerRecord:
    customer_id: str
    country_code: str
    plan: str
    upstream_provider_key: str


class CustomerDirectory:
    """Dev-mode in-memory directory.

    Production deployments use `PostgresCustomerStore` instead. This class
    is kept for tests, file-seeded development setups, and the dashboard
    test suite.
    """

    def __init__(self) -> None:
        self._by_api_key: dict[str, CustomerRecord] = {}

    def register(self, api_key: str, record: CustomerRecord) -> None:
        self._by_api_key[api_key] = record

    def lookup(self, api_key: str) -> CustomerRecord | None:
        return self._by_api_key.get(api_key)

    async def alookup(self, api_key: str) -> CustomerRecord | None:
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


def _customer_store(request: Request) -> object:
    """Return the configured customer store (PG or in-memory directory).

    The store is set on `app.state.customer_store` at startup. In test or
    dev mode we fall back to the global `_directory`.
    """
    return getattr(request.app.state, "customer_store", _directory)


async def authenticate(
    request: Request,
    authorization: str | None = Header(default=None),
    store: object = Depends(_customer_store),
) -> CustomerRecord:
    api_key = _parse_bearer(authorization)
    # Stores may expose `lookup` as either sync or async. Accept both by
    # awaiting the result if it's a coroutine.
    import inspect

    looker = getattr(store, "lookup", None)
    if looker is None:
        raise HTTPException(500, "customer store has no lookup method")
    result = looker(api_key)
    record: CustomerRecord | None = await result if inspect.isawaitable(result) else result
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown API key")
    plan_normalized = record.plan.lower()
    if plan_normalized not in {"starter", "professional", "enterprise", "sovereign"}:
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
