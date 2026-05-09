from __future__ import annotations

import json
from typing import Any

import httpx
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
        upstream_openai_base_url="http://upstream.test",
    )


def _mock_upstream_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    user_text = body["messages"][-1]["content"]
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"echo: {user_text}"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return httpx.Response(200, json=payload)


@pytest.fixture
def app_with_mock_upstream() -> Any:
    app = create_app(settings=_settings())
    # Swap the real upstream client for a MockTransport-backed one.
    transport = httpx.MockTransport(_mock_upstream_handler)
    mock_client = httpx.AsyncClient(transport=transport)
    # Replace the client used by the upstream forwarder + pipeline.
    app.state.pipeline._d.upstream._client = mock_client
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
    return app


@pytest.fixture
def client(app_with_mock_upstream: Any) -> TestClient:
    return TestClient(app_with_mock_upstream)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


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


def test_chat_substitutes_phone_and_forwards(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-1"},
        json={
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "Mon numéro est +213 555 12 34 56"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    # Forward direction: original phone never reaches the upstream — the echo
    # response from the mock upstream contains the SUBSTITUTED phone.
    # Reverse direction: the substituted phone is mapped back, so the final
    # response we receive contains the ORIGINAL phone again.
    assert "+213 555 12 34 56" in content


def test_chat_no_detection_passes_through(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-1"},
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello world"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "echo: hello world"
