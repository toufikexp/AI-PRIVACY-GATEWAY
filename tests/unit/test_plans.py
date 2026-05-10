from __future__ import annotations

import pytest
from src.plans import capabilities_for


def test_starter_caps() -> None:
    caps = capabilities_for("starter")
    assert caps.max_tier3_rules == 25
    assert not caps.can_edit_tier2
    assert not caps.can_use_audit_only_mode


def test_professional_caps() -> None:
    caps = capabilities_for("professional")
    assert caps.max_tier3_rules == 250
    assert caps.can_edit_tier2
    assert caps.can_use_audit_only_mode


def test_enterprise_caps() -> None:
    caps = capabilities_for("enterprise")
    assert caps.can_use_fallback_mode
    assert not caps.air_gapped


def test_sovereign_caps() -> None:
    caps = capabilities_for("sovereign")
    assert caps.air_gapped
    assert caps.can_use_audit_only_mode
    assert caps.can_use_fallback_mode


def test_unknown_plan_raises() -> None:
    with pytest.raises(ValueError):
        capabilities_for("platinum")
