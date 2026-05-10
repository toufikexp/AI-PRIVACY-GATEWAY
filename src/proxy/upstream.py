"""Upstream LLM forwarder.

Forwards a sanitised request body to an OpenAI-compatible upstream endpoint
and returns the raw response payload. The proxy route is responsible for
running detection / substitution before calling this, and reverse
substitution after.
"""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamForwarder:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def chat_completion(
        self, *, body: dict[str, Any], api_key: str, timeout_s: float
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers=headers,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result
