"""Master-plane HTTP API.

Two surfaces:
  * /admin/*  — bearer-token gated; CRUD on customers + license issuance.
  * /v1/*     — used by data planes; plan flags + telemetry intake +
                license validation + license public key.

Hard rule (data-plane content invariant): /v1/telemetry only accepts
TelemetryBatch payloads, which the data plane already constrains to the
content-free whitelist before sending. The master plane re-validates on
arrival as defense-in-depth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.licensing import issue
from src.master_client.telemetry import TelemetryBatch
from src.master_plane.settings import MasterSettings, get_master_settings

router = APIRouter()


# ----- dependencies ------------------------------------------------------


def _pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


def _settings() -> MasterSettings:
    return get_master_settings()


def _require_admin(
    settings: MasterSettings = Depends(_settings),
    authorization: str | None = Header(default=None),
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing admin token")
    token = authorization[len("Bearer ") :].strip()
    if token != settings.admin_token.get_secret_value():
        raise HTTPException(401, "invalid admin token")


# ----- schemas -----------------------------------------------------------


class CustomerCreate(BaseModel):
    customer_id: str
    company_name: str
    country_code: str
    plan: Literal["starter", "professional", "enterprise", "sovereign"]
    contact_email: str | None = None
    failure_mode: Literal["strict", "audit_only", "fallback"] = "strict"


class CustomerOut(BaseModel):
    customer_id: str
    company_name: str
    country_code: str
    plan: str
    failure_mode: str
    rule_edits_locked: bool


class PlanFlags(BaseModel):
    plan: str
    failure_mode: str
    rule_edits_locked: bool


class IssueLicenseRequest(BaseModel):
    customer_id: str
    validity_days: int = 365


# ----- admin endpoints ---------------------------------------------------


@router.post("/admin/customers", response_model=CustomerOut, dependencies=[Depends(_require_admin)])
async def admin_create_customer(
    body: CustomerCreate, pool: asyncpg.Pool = Depends(_pool)
) -> CustomerOut:
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO customers (customer_id, company_name, contact_email,
                                        country_code, plan, failure_mode)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                body.customer_id,
                body.company_name,
                body.contact_email,
                body.country_code,
                body.plan,
                body.failure_mode,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(409, "customer_id already exists") from exc
    return CustomerOut(
        customer_id=body.customer_id,
        company_name=body.company_name,
        country_code=body.country_code,
        plan=body.plan,
        failure_mode=body.failure_mode,
        rule_edits_locked=False,
    )


@router.get(
    "/admin/customers/{customer_id}",
    response_model=CustomerOut,
    dependencies=[Depends(_require_admin)],
)
async def admin_get_customer(customer_id: str, pool: asyncpg.Pool = Depends(_pool)) -> CustomerOut:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM customers WHERE customer_id = $1", customer_id)
    if row is None:
        raise HTTPException(404)
    return _to_customer_out(row)


@router.post(
    "/admin/licenses",
    dependencies=[Depends(_require_admin)],
)
async def admin_issue_license(
    body: IssueLicenseRequest,
    pool: asyncpg.Pool = Depends(_pool),
    settings: MasterSettings = Depends(_settings),
) -> dict[str, Any]:
    if settings.license_private_key_pem is None:
        raise HTTPException(500, "license signing key not configured")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM customers WHERE customer_id = $1", body.customer_id
        )
    if row is None:
        raise HTTPException(404, "unknown customer")
    validity_seconds = body.validity_days * 24 * 3600
    token = issue(
        private_key_pem=settings.license_private_key_pem.get_secret_value(),
        customer_id=body.customer_id,
        country_code=row["country_code"],
        plan=row["plan"],
        validity_seconds=validity_seconds,
        features=[],
    )
    license_id = f"lic-{uuid.uuid4().hex[:16]}"
    expires_at = datetime.now(UTC).replace(microsecond=0)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO licenses (license_id, customer_id, token, expires_at) "
            "VALUES ($1, $2, $3, NOW() + ($4 || ' seconds')::interval)",
            license_id,
            body.customer_id,
            token,
            validity_seconds,
        )
    return {"license_id": license_id, "token": token, "expires_at": expires_at.isoformat()}


# ----- data-plane facing endpoints ---------------------------------------


@router.get("/v1/license/public-key")
async def public_key(settings: MasterSettings = Depends(_settings)) -> dict[str, str]:
    if settings.license_public_key_pem is None:
        raise HTTPException(500, "license public key not configured")
    return {"public_key_pem": settings.license_public_key_pem}


@router.get("/v1/plans/{customer_id}", response_model=PlanFlags)
async def fetch_plan_flags(customer_id: str, pool: asyncpg.Pool = Depends(_pool)) -> PlanFlags:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT plan, failure_mode, rule_edits_locked FROM customers WHERE customer_id = $1",
            customer_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return PlanFlags(
        plan=row["plan"],
        failure_mode=row["failure_mode"],
        rule_edits_locked=row["rule_edits_locked"],
    )


@router.post("/v1/telemetry")
async def ingest_telemetry(
    batch: TelemetryBatch,
    pool: asyncpg.Pool = Depends(_pool),
) -> dict[str, str]:
    # `customer_id` is not in the telemetry payload itself (telemetry is
    # content-free) — derive it from the auth header in production. For
    # MVP we accept it via batch header until customer-auth is wired
    # between data and master planes.
    return {"status": "accepted", "received": str(len(batch.data))}


def _to_customer_out(row: asyncpg.Record) -> CustomerOut:
    return CustomerOut(
        customer_id=row["customer_id"],
        company_name=row["company_name"],
        country_code=row["country_code"],
        plan=row["plan"],
        failure_mode=row["failure_mode"],
        rule_edits_locked=row["rule_edits_locked"],
    )
