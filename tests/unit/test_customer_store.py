from __future__ import annotations

import os

import pytest
from src.proxy.auth import CustomerRecord
from src.proxy.customer_store import (
    InMemoryCustomerStore,
    PostgresCustomerStore,
)


def test_in_memory_register_and_lookup() -> None:
    store = InMemoryCustomerStore()
    rec = CustomerRecord(
        customer_id="c", country_code="DZ", plan="enterprise", upstream_provider_key="x"
    )
    store.register("sk-test", rec)
    import asyncio

    found = asyncio.run(store.lookup("sk-test"))
    assert found == rec


def test_postgres_store_requires_32_byte_key() -> None:
    with pytest.raises(ValueError):
        PostgresCustomerStore(pool=object(), encryption_key=b"\x00" * 16)  # type: ignore[arg-type]


def test_postgres_store_accepts_32_byte_key() -> None:
    # We can't run real Postgres in unit tests; just make sure construction works.
    store = PostgresCustomerStore(pool=object(), encryption_key=os.urandom(32))  # type: ignore[arg-type]
    assert store is not None
