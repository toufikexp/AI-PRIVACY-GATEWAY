from __future__ import annotations

from typing import Any

import pytest
from src.master_plane.routes import router as master_router


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Master-plane app with an in-memory fake pool."""
    from fastapi import FastAPI
    from src.master_plane.settings import MasterSettings

    fake_pool = _FakePool()
    settings = MasterSettings(admin_token="adm-test")
    app = FastAPI()
    app.state.settings = settings
    app.state.pool = fake_pool

    # Override the settings dependency so tests can swap it.
    from src.master_plane.routes import _settings as settings_dep

    app.dependency_overrides[settings_dep] = lambda: settings
    app.include_router(master_router)
    return app


@pytest.fixture
def client(app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app)


def _admin_hdr() -> dict[str, str]:
    return {"Authorization": "Bearer adm-test"}


def test_admin_create_and_get_customer(client: Any) -> None:
    resp = client.post(
        "/admin/customers",
        headers=_admin_hdr(),
        json={
            "customer_id": "cust-1",
            "company_name": "Acme",
            "country_code": "DZ",
            "plan": "enterprise",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["customer_id"] == "cust-1"
    assert body["plan"] == "enterprise"

    resp = client.get("/admin/customers/cust-1", headers=_admin_hdr())
    assert resp.status_code == 200
    assert resp.json()["customer_id"] == "cust-1"


def test_admin_endpoint_requires_token(client: Any) -> None:
    resp = client.post(
        "/admin/customers",
        json={
            "customer_id": "cust-x",
            "company_name": "Anon",
            "country_code": "DZ",
            "plan": "starter",
        },
    )
    assert resp.status_code == 401


def test_plan_flags_endpoint(client: Any) -> None:
    client.post(
        "/admin/customers",
        headers=_admin_hdr(),
        json={
            "customer_id": "cust-2",
            "company_name": "Beta",
            "country_code": "DZ",
            "plan": "professional",
        },
    )
    resp = client.get("/v1/plans/cust-2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "professional"
    assert body["failure_mode"] == "strict"


def test_telemetry_endpoint_accepts_whitelisted(client: Any) -> None:
    resp = client.post(
        "/v1/telemetry",
        json={
            "plane": "country",
            "country_code": "DZ",
            "data": [
                {"name": "request_count", "value": 10},
                {"name": "detection_count", "value": 5},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_telemetry_rejects_content_field(client: Any) -> None:
    resp = client.post(
        "/v1/telemetry",
        json={
            "plane": "country",
            "country_code": "DZ",
            "data": [{"name": "prompt_text", "value": "leak"}],
        },
    )
    assert resp.status_code == 422


# ---------- helpers ----------


class _FakePool:
    """Minimal asyncpg-like pool: stores rows in dicts, supports the SQL
    we actually use in master_plane.routes via a tiny matcher."""

    def __init__(self) -> None:
        self.customers: dict[str, dict[str, Any]] = {}

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self)


class _FakeAcquire:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._pool)

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        s = " ".join(sql.split())
        if s.startswith("INSERT INTO customers"):
            customer_id, company_name, contact_email, country_code, plan, failure_mode = args
            if customer_id in self._pool.customers:
                import asyncpg

                raise asyncpg.UniqueViolationError("customer_id exists")
            self._pool.customers[customer_id] = {
                "customer_id": customer_id,
                "company_name": company_name,
                "contact_email": contact_email,
                "country_code": country_code,
                "plan": plan,
                "failure_mode": failure_mode,
                "rule_edits_locked": False,
            }
            return "INSERT 0 1"
        return ""

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        s = " ".join(sql.split())
        if s.startswith("SELECT * FROM customers WHERE customer_id"):
            return self._pool.customers.get(args[0])
        if s.startswith("SELECT plan, failure_mode, rule_edits_locked FROM customers"):
            row = self._pool.customers.get(args[0])
            if row is None:
                return None
            return {
                "plan": row["plan"],
                "failure_mode": row["failure_mode"],
                "rule_edits_locked": row["rule_edits_locked"],
            }
        return None
