"""Lightweight in-process replacement for the vLLM Detector C client.

CLAUDE.md gotcha: vLLM startup is 30-60s; tests that don't need it MUST mock
it via this fixture. Use `MockLLMClient.queue(...)` to script responses for
specific input substrings, or just rely on the default empty-detections
behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.detectors.base import Detection


@dataclass(slots=True)
class MockLLMClient:
    """Drop-in replacement for the Detector C HTTP client.

    The real client returns `list[Detection]` for an input string. This mock
    accepts a `responder: Callable[[str], list[Detection]]`; if not set,
    returns no detections.
    """

    responder: Callable[[str], list[Detection]] | None = None
    calls: list[str] = field(default_factory=list)

    async def detect(self, text: str) -> list[Detection]:
        self.calls.append(text)
        if self.responder is None:
            return []
        return self.responder(text)
