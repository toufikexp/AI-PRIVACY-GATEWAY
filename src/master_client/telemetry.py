"""Master-plane telemetry.

Hard rule (CLAUDE.md #1, ARCHITECTURE §3.2): the master plane never sees
customer content. Telemetry is structured numeric/categorical only — no
free-text fields, no entity values, no prompts, no responses.

This is enforced at construction time by `build_batch`, which:
- Only allows fields in `TELEMETRY_ALLOWED_FIELDS` (whitelist).
- Rejects any non-numeric, non-bool, non-enum-categorical value.
- Raises `TelemetryContentLeakError` on violation. CI test
  `tests/unit/test_no_content_in_telemetry.py` enforces it.

Never weaken or bypass this check — it is the architectural guarantee that
keeps customer prompts inside the country boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, Field, ValidationError, field_validator


class TelemetryContentLeakError(ValueError):
    """A telemetry payload contained a field or value that may carry content."""


# Whitelist of every field the data plane is allowed to send to master.
# Adding a field here is a security-review-gated change.
TELEMETRY_ALLOWED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "request_count",
        "token_count_in",
        "token_count_out",
        "detection_count",  # total spans across all entity types
        "detection_count_tier_1",
        "detection_count_tier_2",
        "detection_count_tier_3",
        "exception_count",  # Tier 1 exceptions applied
        "latency_p50_ms",
        "latency_p99_ms",
        "vllm_up",  # bool
        "rule_pack_version",  # short version string
        "software_version",
        "plan",  # categorical: starter/pro/enterprise/sovereign
        "country_code",  # ISO alpha-2
        "window_start_epoch",
        "window_end_epoch",
    }
)

# Categorical fields whose string values are constrained.
_CATEGORICAL_VALUES: Final[dict[str, frozenset[str]]] = {
    "plan": frozenset({"starter", "professional", "enterprise", "sovereign"}),
}


class TelemetryDatum(BaseModel):
    """A single key/value telemetry data point.

    Pydantic enforces the field-level invariants; `build_batch` enforces the
    set-level invariant (no unexpected fields, no surprise types).
    """

    name: str
    value: int | float | bool | str

    @field_validator("name")
    @classmethod
    def _name_in_whitelist(cls, v: str) -> str:
        if v not in TELEMETRY_ALLOWED_FIELDS:
            raise TelemetryContentLeakError(
                f"telemetry field {v!r} is not in the allowed whitelist; "
                "adding fields requires security review"
            )
        return v

    @field_validator("value")
    @classmethod
    def _value_safe(cls, v: int | float | bool | str) -> int | float | bool | str:
        if isinstance(v, str) and len(v) > 64:
            raise TelemetryContentLeakError(
                "telemetry string values are capped at 64 chars to prevent "
                "free-text content from being smuggled through"
            )
        return v


class TelemetryBatch(BaseModel):
    """One outbound batch from data plane → master plane."""

    plane: str = Field(pattern=r"^(country|company)$")
    country_code: str = Field(min_length=2, max_length=2)
    data: list[TelemetryDatum]

    @field_validator("data")
    @classmethod
    def _validate_categorical(cls, data: list[TelemetryDatum]) -> list[TelemetryDatum]:
        for d in data:
            allowed = _CATEGORICAL_VALUES.get(d.name)
            if allowed is None:
                continue
            if not isinstance(d.value, str) or d.value not in allowed:
                raise TelemetryContentLeakError(
                    f"{d.name} must be one of {sorted(allowed)}; got {d.value!r}"
                )
        return data


@dataclass(frozen=True, slots=True)
class _BatchInput:
    plane: str
    country_code: str
    metrics: dict[str, int | float | bool | str]


def build_batch(
    *,
    plane: str,
    country_code: str,
    metrics: dict[str, int | float | bool | str],
) -> TelemetryBatch:
    """Construct a telemetry batch with full invariant checks.

    Raises:
        TelemetryContentLeakError: if any field is outside the whitelist or
            its value type is not numeric/bool/short-categorical.
    """
    _ = _BatchInput(plane=plane, country_code=country_code, metrics=metrics)
    try:
        return TelemetryBatch(
            plane=plane,
            country_code=country_code,
            data=[TelemetryDatum(name=k, value=v) for k, v in metrics.items()],
        )
    except ValidationError as exc:
        # Unwrap pydantic ValidationError so the leak invariant raises a
        # single, named exception type that callers and CI can match on.
        for err in exc.errors():
            ctx = err.get("ctx") or {}
            inner = ctx.get("error")
            if isinstance(inner, TelemetryContentLeakError):
                raise inner from exc
        raise TelemetryContentLeakError(str(exc)) from exc
