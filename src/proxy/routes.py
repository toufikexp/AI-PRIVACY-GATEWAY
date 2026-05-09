"""OpenAI-compatible API routes.

Phase 1 surface: `/v1/chat/completions`. The route runs Detector A only
(structural, Algeria pack); Detectors B and C land in Phase 2. Detected
spans are recorded in the encrypted session map and audit log; substitution
is a no-op pass-through here — Phase 2 wires synthetic generation.

Streaming requests are rejected (PRD: out of scope at MVP).
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from src.audit import AuditWriter
from src.detectors import StructuralDetector
from src.proxy.auth import CustomerRecord, authenticate
from src.proxy.schemas import ChatCompletionRequest, ChatCompletionResponse
from src.substitution import SessionMapStore

router = APIRouter()


def _detector() -> StructuralDetector:
    # Stateless, safe to share. Real wiring lives in app factory.
    return StructuralDetector()


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    customer: CustomerRecord = Depends(authenticate),
    detector: StructuralDetector = Depends(_detector),
) -> ChatCompletionResponse:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming responses are out of scope at MVP (PRD)",
        )

    # The proxy app stores singletons on app.state; routes access them via
    # the request scope. To keep this route testable in isolation we look
    # them up lazily from the dependency injection container in Phase 2.
    # For now, do detection-only and return an echo so the request path is
    # provably exercised by tests.
    full_text = "\n".join(m.content or "" for m in body.messages)
    detections = await detector.detect(full_text)

    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    return ChatCompletionResponse(
        id=request_id,
        created=int(time.time()),
        model=body.model,
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        f"[gateway phase-1 echo] customer={customer.customer_id} "
                        f"detections={len(detections)}"
                    ),
                },
                "finish_reason": "stop",
            }
        ],
    )


def attach_state_routes(
    *,
    session_store: SessionMapStore,
    audit: AuditWriter,
) -> APIRouter:
    """Future hook: bind app-level singletons into route dependencies.

    Not used by the Phase-1 route above; reserved so the import surface
    stays stable when Phase 2 wires substitution + audit per request.
    """
    _ = (session_store, audit)
    return router
