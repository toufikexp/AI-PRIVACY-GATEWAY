"""Dashboard endpoints — Jinja2 + HTMX (no separate frontend build).

Pages:
  GET  /dashboard/                  — landing
  GET  /dashboard/activity          — recent audit activity for the customer
  GET  /dashboard/rules             — rule catalogue (Tier 1/2/3 separated)
  GET  /dashboard/exceptions        — Tier 1 exceptions list
  POST /dashboard/exceptions/add    — add Tier 1 exception (HTMX)
  GET  /dashboard/audit             — audit log viewer (latest N)

Authentication is shared with the API router. In production we'd serve
the dashboard on a separate cookie-auth path — for MVP it lives behind
the same Bearer token so the proxy is single-binary.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.audit.writer import AuditRecord
from src.proxy.auth import CustomerRecord, authenticate
from src.rules import RuleExceptionStore
from src.rules.exceptions import RuleException
from src.rules.store import RuleStore

_HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))

router = APIRouter()


def _audit_records(request: Request) -> list[AuditRecord]:
    records: list[AuditRecord] = request.app.state.audit._backend.all()
    return records


def _rule_store(request: Request) -> RuleStore:
    return request.app.state.rule_store  # type: ignore[no-any-return]


def _exception_store(request: Request) -> RuleExceptionStore:
    return request.app.state.exception_store  # type: ignore[no-any-return]


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, customer: CustomerRecord = Depends(authenticate)) -> Any:
    return templates.TemplateResponse(request, "landing.html", {"customer": customer})


@router.get("/activity", response_class=HTMLResponse)
async def activity(request: Request, customer: CustomerRecord = Depends(authenticate)) -> Any:
    records = [r for r in _audit_records(request) if r.customer_id == customer.customer_id]
    records = records[-50:][::-1]
    return templates.TemplateResponse(
        request, "activity.html", {"customer": customer, "records": records}
    )


@router.get("/rules", response_class=HTMLResponse)
async def rules(
    request: Request,
    customer: CustomerRecord = Depends(authenticate),
    store: RuleStore = Depends(_rule_store),
) -> Any:
    from src.rules.models import Rule

    rules = await store.list_for_customer()
    by_tier: dict[int, list[Rule]] = {1: [], 2: [], 3: []}
    for r in rules:
        by_tier.setdefault(r.tier, []).append(r)
    return templates.TemplateResponse(
        request, "rules.html", {"customer": customer, "by_tier": by_tier}
    )


@router.get("/exceptions", response_class=HTMLResponse)
async def list_exceptions(
    request: Request,
    customer: CustomerRecord = Depends(authenticate),
    store: RuleExceptionStore = Depends(_exception_store),
) -> Any:
    entries = await store.list_active()
    return templates.TemplateResponse(
        request, "exceptions.html", {"customer": customer, "exceptions": entries}
    )


@router.post("/exceptions/add", response_class=HTMLResponse)
async def add_exception(
    request: Request,
    rule_id: str = Form(default=""),
    entity_type: str = Form(default=""),
    text_match: str = Form(default=""),
    note: str = Form(default=""),
    customer: CustomerRecord = Depends(authenticate),
    store: RuleExceptionStore = Depends(_exception_store),
) -> Any:
    await store.add(
        RuleException(
            exception_id=f"exc-{uuid.uuid4().hex[:12]}",
            customer_id=customer.customer_id,
            rule_id=rule_id or None,
            entity_type=entity_type or None,
            text_match=text_match or None,
            note=note or None,
        )
    )
    entries = await store.list_active()
    return templates.TemplateResponse(request, "exceptions_table.html", {"exceptions": entries})


@router.get("/audit", response_class=HTMLResponse)
async def audit_view(request: Request, customer: CustomerRecord = Depends(authenticate)) -> Any:
    records = [r for r in _audit_records(request) if r.customer_id == customer.customer_id]
    return templates.TemplateResponse(
        request, "audit.html", {"customer": customer, "records": records[-200:][::-1]}
    )
