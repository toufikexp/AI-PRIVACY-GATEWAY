from __future__ import annotations

import asyncio

import pytest
from src.tenancy import (
    CustomerContext,
    MissingTenantScopeError,
    bind_customer,
    require_customer,
    reset_customer,
)


def test_require_without_binding_raises() -> None:
    with pytest.raises(MissingTenantScopeError):
        require_customer()


def test_bind_and_require_roundtrip() -> None:
    ctx = CustomerContext(customer_id="c-1", country_code="DZ", plan="starter")
    token = bind_customer(ctx)
    try:
        assert require_customer() is ctx
    finally:
        reset_customer(token)


def test_country_code_validated() -> None:
    with pytest.raises(ValueError):
        CustomerContext(customer_id="c-1", country_code="XYZ", plan="starter")


def test_empty_customer_id_rejected() -> None:
    with pytest.raises(MissingTenantScopeError):
        CustomerContext(customer_id="", country_code="DZ", plan="starter")


def test_contextvar_isolated_across_tasks() -> None:
    """Concurrent asyncio tasks must NOT see each other's customer scope."""

    async def task(cust_id: str) -> str:
        ctx = CustomerContext(customer_id=cust_id, country_code="DZ", plan="starter")
        bind_customer(ctx)
        await asyncio.sleep(0)
        # Each task has its own contextvar copy — see asyncio.Task docs.
        return require_customer().customer_id

    async def driver() -> tuple[str, str]:
        a = asyncio.create_task(task("A"))
        b = asyncio.create_task(task("B"))
        return await a, await b

    a, b = asyncio.run(driver())
    assert {a, b} == {"A", "B"}
