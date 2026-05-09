"""OpenAI-compatible API routes.

`POST /v1/chat/completions` runs the full detection → substitution →
forward → reverse pipeline implemented in `pipeline.py`.

Streaming is rejected at MVP (PRD: out of scope).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.proxy.auth import CustomerRecord, authenticate
from src.proxy.pipeline import Pipeline
from src.proxy.schemas import ChatCompletionRequest

router = APIRouter()


def _get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline  # type: ignore[no-any-return]


@router.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    customer: CustomerRecord = Depends(authenticate),
    pipeline: Pipeline = Depends(_get_pipeline),
) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="streaming responses are out of scope at MVP (PRD)",
        )
    raw_body: dict[str, Any] = body.model_dump(exclude_none=True)
    return await pipeline.handle_chat(
        body=raw_body, upstream_api_key=customer.upstream_provider_key
    )
