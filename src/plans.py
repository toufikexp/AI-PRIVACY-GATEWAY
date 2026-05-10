"""Plan-tier capability matrix.

Plan flags drive UI gates, rule-edit gates, and audit-only behaviour. The
matrix below is the source of truth — `Pipeline` and `dashboard.routes`
read it via `capabilities_for(plan)` to decide whether to allow an action.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlanCaps:
    plan: str
    max_tier3_rules: int
    max_exceptions: int
    can_edit_tier2: bool
    can_use_audit_only_mode: bool
    can_use_fallback_mode: bool
    air_gapped: bool


_CAPS: dict[str, PlanCaps] = {
    "starter": PlanCaps(
        plan="starter",
        max_tier3_rules=25,
        max_exceptions=10,
        can_edit_tier2=False,
        can_use_audit_only_mode=False,
        can_use_fallback_mode=False,
        air_gapped=False,
    ),
    "professional": PlanCaps(
        plan="professional",
        max_tier3_rules=250,
        max_exceptions=100,
        can_edit_tier2=True,
        can_use_audit_only_mode=True,
        can_use_fallback_mode=False,
        air_gapped=False,
    ),
    "enterprise": PlanCaps(
        plan="enterprise",
        max_tier3_rules=10_000,
        max_exceptions=10_000,
        can_edit_tier2=True,
        can_use_audit_only_mode=True,
        can_use_fallback_mode=True,
        air_gapped=False,
    ),
    "sovereign": PlanCaps(
        plan="sovereign",
        max_tier3_rules=100_000,
        max_exceptions=100_000,
        can_edit_tier2=True,
        can_use_audit_only_mode=True,
        can_use_fallback_mode=True,
        air_gapped=True,
    ),
}


def capabilities_for(plan: str) -> PlanCaps:
    if plan not in _CAPS:
        raise ValueError(f"unknown plan {plan!r}")
    return _CAPS[plan]
