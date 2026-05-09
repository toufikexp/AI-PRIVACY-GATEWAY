"""CI invariant: master plane never receives customer content (CLAUDE.md #1).

This test file MUST NOT be bypassed or marked xfail. If you need to add a
new telemetry field, update `TELEMETRY_ALLOWED_FIELDS` AND add a positive
test here for the field; never widen the validator to accept arbitrary keys.
"""

from __future__ import annotations

import pytest
from src.master_client import (
    TELEMETRY_ALLOWED_FIELDS,
    TelemetryContentLeakError,
    build_batch,
)


def test_allowed_fields_round_trip() -> None:
    batch = build_batch(
        plane="country",
        country_code="DZ",
        metrics={
            "request_count": 1234,
            "token_count_in": 9876,
            "token_count_out": 5432,
            "detection_count": 21,
            "detection_count_tier_1": 10,
            "detection_count_tier_2": 7,
            "detection_count_tier_3": 4,
            "exception_count": 1,
            "latency_p50_ms": 95.3,
            "latency_p99_ms": 312.7,
            "vllm_up": True,
            "rule_pack_version": "DZ-1.4.0",
            "software_version": "0.1.0dev0",
            "plan": "enterprise",
            "country_code": "DZ",
            "window_start_epoch": 1_700_000_000,
            "window_end_epoch": 1_700_000_300,
        },
    )
    assert batch.plane == "country"
    assert {d.name for d in batch.data} <= TELEMETRY_ALLOWED_FIELDS


def test_unknown_field_rejected() -> None:
    with pytest.raises(TelemetryContentLeakError):
        build_batch(
            plane="country",
            country_code="DZ",
            metrics={"prompt_text": "hello"},  # forbidden — content leak
        )


def test_long_string_value_rejected() -> None:
    with pytest.raises(TelemetryContentLeakError):
        build_batch(
            plane="country",
            country_code="DZ",
            # rule_pack_version IS in the whitelist, but free-text smuggled
            # via long string values is rejected by the value-length cap.
            metrics={"rule_pack_version": "x" * 65},
        )


def test_invalid_categorical_rejected() -> None:
    with pytest.raises(TelemetryContentLeakError):
        build_batch(
            plane="country",
            country_code="DZ",
            metrics={"plan": "platinum"},  # not in allowed plan tiers
        )


def test_country_code_format_enforced() -> None:
    with pytest.raises((TelemetryContentLeakError, ValueError)):
        build_batch(
            plane="country",
            country_code="DZA",  # alpha-3, not allowed
            metrics={"request_count": 1},
        )


def test_plane_must_be_country_or_company() -> None:
    with pytest.raises((TelemetryContentLeakError, ValueError)):
        build_batch(
            plane="master",
            country_code="DZ",
            metrics={"request_count": 1},
        )


def test_whitelist_is_frozen_set() -> None:
    """Defense-in-depth: catch accidental mutation of the whitelist at import."""
    assert isinstance(TELEMETRY_ALLOWED_FIELDS, frozenset)
