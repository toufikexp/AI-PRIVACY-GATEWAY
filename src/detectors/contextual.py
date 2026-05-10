"""Detector C — contextual LLM with retrieval.

Two backends:
  * `stub` — produces no detections. Used in CI and dev.
  * `http` — POSTs to an OpenAI-compatible vLLM endpoint hosting
             Qwen2.5-7B AWQ-int4 (or any compatible model). Prompts the
             model with retrieved Tier 1+2+3 rules and the input text;
             expects structured JSON output. Span-validates every returned
             span against the original input — hallucinated spans are
             dropped (CLAUDE.md hard rule #6).

The HTTP client uses `httpx.AsyncClient` and reuses connections via the
client object held on app.state.

Tier-aware prefix caching is implemented by sending the (system + tier1 +
tier2) prompt as a stable prefix. vLLM detects identical prefixes across
requests and reuses KV cache. We do NOT explicitly manage the cache — vLLM
handles it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from src.detectors.base import Detection, EntityType
from src.tenancy import require_customer

DETECTOR_NAME = "contextual"

# Prompt template; tier1+tier2 form the cacheable prefix.
_SYSTEM_PROMPT = (
    "You are a privacy detection assistant. Identify sensitive entities in "
    "the user's text per the provided rule catalogue. Return STRICTLY valid "
    'JSON of the form: {"detections": [{"entity_type": str, "start": '
    'int, "end": int, "text": str, "confidence": float, "rule_id": '
    "str}]}. Use exact character offsets into the input text."
)


@dataclass(frozen=True, slots=True)
class RuleSnippet:
    rule_id: str
    tier: int
    entity_type: str
    description: str


class StubContextualDetector:
    """No-op contextual detector used when vLLM is not available."""

    name = DETECTOR_NAME

    async def detect(
        self, text: str, *, retrieved_rules: list[RuleSnippet] | None = None
    ) -> list[Detection]:
        require_customer()
        return []


class VLLMContextualDetector:
    """Real Detector C backed by an OpenAI-compatible vLLM server."""

    name = DETECTOR_NAME

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        model: str,
        timeout_s: float,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_s = timeout_s

    async def detect(
        self, text: str, *, retrieved_rules: list[RuleSnippet] | None = None
    ) -> list[Detection]:
        require_customer()
        rules = retrieved_rules or []
        prefix = self._build_prefix(rules)
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prefix},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        resp = await self._client.post("/chat/completions", json=body, timeout=self._timeout_s)
        resp.raise_for_status()
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return []
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return []
        raw_detections = parsed.get("detections") or []
        return list(self._parse_detections(text, raw_detections))

    def _build_prefix(self, rules: list[RuleSnippet]) -> str:
        # Group rules by tier so prefix caching benefits — Tier 1 + 2 are
        # stable per customer per country.
        tier1 = [r for r in rules if r.tier == 1]
        tier2 = [r for r in rules if r.tier == 2]
        tier3 = [r for r in rules if r.tier == 3]
        sections = [_SYSTEM_PROMPT]
        for label, group in (
            ("Tier 1 country", tier1),
            ("Tier 2 industry", tier2),
            ("Tier 3 customer", tier3),
        ):
            if not group:
                continue
            sections.append(f"\n## {label} rules:")
            for r in group:
                sections.append(f"- {r.rule_id} [{r.entity_type}]: {r.description}")
        return "\n".join(sections)

    def _parse_detections(self, text: str, raw: list[dict[str, object]]) -> Iterable[Detection]:
        for item in raw:
            try:
                start_raw = item["start"]
                end_raw = item["end"]
                claim_text = str(item["text"])
                if not isinstance(start_raw, (int, str)) or not isinstance(end_raw, (int, str)):
                    continue
                start = int(start_raw)
                end = int(end_raw)
                entity_type = str(item.get("entity_type", "custom"))
                conf_raw = item.get("confidence", 0.7)
                if not isinstance(conf_raw, (int, float, str)):
                    continue
                confidence = float(conf_raw)
                rule_id = item.get("rule_id")
            except (KeyError, ValueError, TypeError):
                continue
            # Span validation — hallucinated spans dropped silently.
            if start < 0 or end > len(text) or end <= start:
                continue
            if text[start:end] != claim_text:
                continue
            yield Detection(
                entity_type=_safe_entity_type(entity_type),
                start=start,
                end=end,
                text=claim_text,
                confidence=max(0.0, min(0.99, confidence)),
                tier=3,
                detector=DETECTOR_NAME,
                rule_id=str(rule_id) if rule_id else None,
            )


_KNOWN_ENTITY_TYPES: set[str] = {
    "national_id",
    "tax_id",
    "bank_account",
    "card_number",
    "phone",
    "email",
    "person",
    "organization",
    "location",
    "date",
    "monetary",
    "custom",
}


def _safe_entity_type(value: str) -> EntityType:
    return value if value in _KNOWN_ENTITY_TYPES else "custom"  # type: ignore[return-value]


def make_detector(
    *, backend: str, vllm_url: str | None, model: str, timeout_s: float
) -> tuple[object, httpx.AsyncClient | None]:
    """Construct the configured contextual detector.

    Returns (detector, http_client_or_none). The HTTP client is owned by
    the app and closed at shutdown.
    """
    if backend == "http":
        if not vllm_url:
            raise ValueError("vllm_backend=http requires vllm_url")
        client = httpx.AsyncClient(base_url=vllm_url, timeout=timeout_s)
        return VLLMContextualDetector(client=client, model=model, timeout_s=timeout_s), client
    return StubContextualDetector(), None
