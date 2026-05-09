from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from src.config import Settings
from src.proxy import create_app
from src.proxy.auth import CustomerRecord, get_directory


def _settings() -> Settings:
    return Settings(
        country_code="DZ",
        environment="dev",
        master_plane_mock=True,
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app(settings=_settings())
    directory = get_directory()
    directory.register(
        "sk-test-1",
        CustomerRecord(
            customer_id="cust-1",
            country_code="DZ",
            plan="professional",
            upstream_provider_key="upstream-key-redacted",
        ),
    )
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_chat_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_chat_unknown_api_key(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-unknown"},
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_chat_streaming_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-1"},
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert r.status_code == 400


def test_chat_runs_structural_detector(client: TestClient) -> None:
    # Embed a valid Algerian phone in user content; structural detector
    # should fire and the gateway echo response should report >=1 detection.
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-1"},
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Mon numéro: +213 555 12 34 56"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "detections=" in body["choices"][0]["message"]["content"]
    # parse the echoed detection count
    content = body["choices"][0]["message"]["content"]
    detections_part = content.split("detections=")[-1]
    assert int(detections_part) >= 1
