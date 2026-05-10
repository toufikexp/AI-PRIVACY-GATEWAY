from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config import Settings
from src.proxy import create_app
from src.proxy.auth import CustomerRecord, get_directory


def _settings() -> Settings:
    return Settings(country_code="DZ", environment="dev", master_plane_mock=True)


def _mock_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app(settings=_settings())
    transport = httpx.MockTransport(_mock_handler)
    app.state.pipeline._d.upstream._client = httpx.AsyncClient(transport=transport)
    directory = get_directory()
    directory.register(
        "sk-dash-1",
        CustomerRecord(
            customer_id="cust-dash",
            country_code="DZ",
            plan="enterprise",
            upstream_provider_key="sk-up",
        ),
    )
    return TestClient(app)


def _hdr() -> dict[str, str]:
    return {"Authorization": "Bearer sk-dash-1"}


def test_landing_renders(client: TestClient) -> None:
    r = client.get("/dashboard/", headers=_hdr())
    assert r.status_code == 200
    assert "Overview" in r.text
    assert "cust-dash" in r.text


def test_activity_empty_then_populated(client: TestClient) -> None:
    r0 = client.get("/dashboard/activity", headers=_hdr())
    assert r0.status_code == 200
    assert "no activity yet" in r0.text

    # Trigger an audit-producing request
    client.post(
        "/v1/chat/completions",
        headers=_hdr(),
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    r1 = client.get("/dashboard/activity", headers=_hdr())
    assert "no activity yet" not in r1.text


def test_exception_add_via_htmx(client: TestClient) -> None:
    r = client.post(
        "/dashboard/exceptions/add",
        headers=_hdr(),
        data={"rule_id": "dz.nin", "text_match": "123", "note": "false-positive"},
    )
    assert r.status_code == 200
    assert "dz.nin" in r.text


def test_audit_view_renders(client: TestClient) -> None:
    r = client.get("/dashboard/audit", headers=_hdr())
    assert r.status_code == 200
    assert "Audit log" in r.text


def test_dashboard_requires_auth(client: TestClient) -> None:
    r = client.get("/dashboard/")
    assert r.status_code == 401


def test_rules_view_renders_with_seeded_rules(client: TestClient) -> None:
    # Seed a Tier 3 rule for this customer
    from src.rules import Rule

    store = client.app.state.rule_store
    # Use the customer's scope so upsert is allowed
    from src.tenancy import CustomerContext, bind_customer, reset_customer

    ctx = CustomerContext(customer_id="cust-dash", country_code="DZ", plan="enterprise")
    tok = bind_customer(ctx)
    try:
        import asyncio

        asyncio.run(
            store.upsert_tier3(
                Rule(
                    rule_id="cust-dash.codename",
                    tier=3,
                    entity_type="custom",
                    description="Project Phoenix",
                    country_code=None,
                    industry=None,
                    customer_id="cust-dash",
                )
            )
        )
    finally:
        reset_customer(tok)

    r = client.get("/dashboard/rules", headers=_hdr())
    assert r.status_code == 200
    assert "Project Phoenix" in r.text
