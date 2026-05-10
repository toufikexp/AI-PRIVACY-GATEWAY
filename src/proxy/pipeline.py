"""Detection → substitution → forward → reverse pipeline.

This is the per-request orchestrator. The route handler in `routes.py`
calls `Pipeline.handle_chat`. Pipeline is constructed once at app startup
and shared across requests; per-request state lives in the encrypted
session map and contextvars.

Latency budget (ARCHITECTURE §7.1):
  * Detectors A/B/C run via asyncio.gather → bounded by slowest detector.
  * Forward and reverse substitution are pure-Python and bounded by text
    length.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from src.audit import AuditWriter
from src.config import Settings
from src.detectors.base import Detection
from src.detectors.contextual import RuleSnippet
from src.detectors.ner import StubNERDetector
from src.detectors.structural import StructuralDetector
from src.merge import MergeConfig, MergeEngine, MergeResult
from src.merge.engine import ExceptionEntry
from src.observability import (
    detection_count,
    detector_latency,
    pipeline_latency,
    requests_total,
)
from src.proxy.upstream import UpstreamForwarder
from src.retrieval import HybridRetriever
from src.rules.exceptions import RuleExceptionStore
from src.substitution import (
    SessionMapStore,
    apply_substitution,
    reverse_substitution,
)
from src.tenancy import require_customer

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PipelineDeps:
    settings: Settings
    structural: StructuralDetector
    ner: object  # StubNERDetector | OnnxNERDetector — both implement detect()
    contextual: object  # StubContextualDetector | VLLMContextualDetector
    retriever: HybridRetriever
    exception_store: RuleExceptionStore | object
    session_store: SessionMapStore
    audit: AuditWriter
    upstream: UpstreamForwarder


class Pipeline:
    def __init__(self, deps: PipelineDeps) -> None:
        self._d = deps
        self._merger = MergeEngine(
            config=MergeConfig(
                tier1_threshold=deps.settings.tier1_confidence_threshold,
                tier2_threshold=deps.settings.tier2_confidence_threshold,
                tier3_threshold=deps.settings.tier3_confidence_threshold,
            )
        )

    async def handle_chat(
        self,
        *,
        body: dict[str, Any],
        upstream_api_key: str,
    ) -> dict[str, Any]:
        ctx = require_customer()
        request_id = f"chatcmpl-{uuid.uuid4().hex}"
        salt = secrets.token_hex(8)

        original_text = _flatten_messages(body.get("messages") or [])

        t_start = time.monotonic()

        rules: list[RuleSnippet] = await self._d.retriever.retrieve(text=original_text)

        # Detectors run concurrently with per-detector latency timing.
        async def _timed(name: str, coro: Any) -> list[Detection]:
            start = time.monotonic()
            try:
                result: list[Detection] = await coro
                return result
            finally:
                detector_latency.labels(detector=name).observe(time.monotonic() - start)

        detect_tasks = [
            _timed("structural", self._d.structural.detect(original_text)),
            _timed("ner", self._d.ner.detect(original_text)),  # type: ignore[attr-defined]
            _timed("contextual", self._invoke_contextual(original_text, rules)),
        ]
        detector_results = await asyncio.gather(*detect_tasks, return_exceptions=True)
        all_detections: list[Detection] = []
        for r in detector_results:
            if isinstance(r, BaseException):
                if self._d.settings.default_failure_mode == "strict":
                    requests_total.labels(
                        plan=ctx.plan, country=ctx.country_code, outcome="detector_error"
                    ).inc()
                    raise r
                log.warning("detector_failed", error=str(r))
                continue
            all_detections.extend(r)

        # Customer Tier 1 exceptions (apply during merge)
        exceptions: list[ExceptionEntry]
        try:
            exceptions = await self._d.exception_store.list_active()  # type: ignore[attr-defined]
        except AttributeError:
            exceptions = []

        merge_result: MergeResult = self._merger.merge(
            original_text=original_text,
            detections=all_detections,
            exceptions=exceptions,
        )

        session_map = self._d.session_store.open(request_id=request_id)
        try:
            forward = apply_substitution(
                original_text=original_text,
                detections=merge_result.accepted,
                session_map=session_map,
                country_code=ctx.country_code,
                salt=salt,
            )

            sanitised_body = _replace_messages(body, forward.sanitised_text)

            upstream_response = await self._d.upstream.chat_completion(
                body=sanitised_body,
                api_key=upstream_api_key,
                timeout_s=self._d.settings.upstream_request_timeout_s,
            )

            response_text = _extract_response_text(upstream_response)
            ner_entities = await self._post_response_entities(response_text)
            reverse = reverse_substitution(
                response_text=response_text,
                session_map=session_map,
                components_by_synthetic=forward.components_by_synthetic,
                ner_entities=ner_entities,
            )
            final_response = _replace_response_text(upstream_response, reverse.text)

            elapsed = time.monotonic() - t_start
            pipeline_latency.observe(elapsed)
            requests_total.labels(plan=ctx.plan, country=ctx.country_code, outcome="ok").inc()
            for det in merge_result.accepted:
                detection_count.labels(entity_type=det.entity_type, tier=str(det.tier)).inc()
            self._d.audit.record(
                request_id=request_id,
                event_type="request",
                payload={
                    "detection_total": len(all_detections),
                    "accepted": len(merge_result.accepted),
                    "below_threshold": len(merge_result.below_threshold),
                    "exception_suppressed": len(merge_result.exception_suppressed),
                    "span_invalid": len(merge_result.span_invalid),
                    "novel_in_response": len(reverse.novel_entities),
                    "latency_ms": int(elapsed * 1000),
                },
            )
            return final_response
        finally:
            # Hard rule #2: purge on response delivery (success or failure).
            self._d.session_store.close(request_id)

    async def _invoke_contextual(self, text: str, rules: list[RuleSnippet]) -> list[Detection]:
        contextual = self._d.contextual
        # The stub detector accepts a `retrieved_rules` kwarg; the HTTP one too.
        result: list[Detection] = await contextual.detect(  # type: ignore[attr-defined]
            text, retrieved_rules=rules
        )
        return result

    async def _post_response_entities(self, text: str) -> list[str]:
        # Reuse Detector B for post-response NER (ARCHITECTURE §4.5.2).
        require_customer()
        try:
            ner = self._d.ner
            if isinstance(ner, StubNERDetector):
                detections = await ner.detect(text)
            else:
                detections = await ner.detect(text)  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("post_ner_failed", error=str(exc))
            return []
        return [d.text for d in detections]


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _replace_messages(body: dict[str, Any], sanitised: str) -> dict[str, Any]:
    """Return a copy of `body` with all message contents replaced by `sanitised`.

    Multi-message conversations are treated as one concatenated input for
    detection; on forward we collapse them into a single user message so
    the upstream LLM receives the sanitised aggregate. This is intentional
    at MVP — multi-turn substitution-mapping is deferred (see PRD).
    """
    cloned = dict(body)
    cloned["messages"] = [{"role": "user", "content": sanitised}]
    return cloned


def _extract_response_text(response: dict[str, Any]) -> str:
    try:
        return str(response["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def _replace_response_text(response: dict[str, Any], new_text: str) -> dict[str, Any]:
    cloned = dict(response)
    choices = list(cloned.get("choices") or [])
    if choices:
        first = dict(choices[0])
        message = dict(first.get("message") or {})
        message["content"] = new_text
        first["message"] = message
        choices[0] = first
        cloned["choices"] = choices
    return cloned
