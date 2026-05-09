"""HTTP client for data plane → master plane communication.

Push-only direction: telemetry batches.
Pull direction:      plan flags + license validation + signed pack updates.

The client never sends customer prompt content. Telemetry batches are
constructed via `master_client.telemetry.build_batch`, which enforces the
content-free invariant.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from src.master_client.telemetry import TelemetryBatch


@dataclass(slots=True)
class PlanFlags:
    plan: str
    failure_mode: str
    rule_edits_locked: bool
    fetched_at: float


class MasterPlaneClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
    ) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def push_telemetry(self, batch: TelemetryBatch) -> None:
        body = batch.model_dump()
        resp = await self._client.post("/telemetry", json=body, headers=self._headers)
        resp.raise_for_status()

    async def fetch_plan_flags(self, customer_id: str) -> PlanFlags:
        resp = await self._client.get(f"/plans/{customer_id}", headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        return PlanFlags(
            plan=data["plan"],
            failure_mode=data.get("failure_mode", "strict"),
            rule_edits_locked=bool(data.get("rule_edits_locked", False)),
            fetched_at=time.time(),
        )

    async def validate_license(self) -> bool:
        resp = await self._client.get("/license", headers=self._headers)
        return resp.status_code == 200


class MockMasterPlaneClient:
    """Used when GATEWAY_MASTER_PLANE_MOCK=true (default in dev).

    Reads `dev_plan_flags.json` if present; otherwise serves a permissive
    enterprise plan with strict failure mode. Telemetry is dropped.
    """

    def __init__(self, *, dev_flags_path: str = "dev_plan_flags.json") -> None:
        self._dev_path = dev_flags_path

    async def push_telemetry(self, batch: TelemetryBatch) -> None:
        return None

    async def fetch_plan_flags(self, customer_id: str) -> PlanFlags:
        try:
            with open(self._dev_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return PlanFlags(
                plan=data.get("plan", "enterprise"),
                failure_mode=data.get("failure_mode", "strict"),
                rule_edits_locked=bool(data.get("rule_edits_locked", False)),
                fetched_at=time.time(),
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return PlanFlags(
                plan="enterprise",
                failure_mode="strict",
                rule_edits_locked=False,
                fetched_at=time.time(),
            )

    async def validate_license(self) -> bool:
        return True
