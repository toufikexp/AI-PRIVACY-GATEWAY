"""Detector A — structural validators with checksum/format checks.

Composes per-country validator packs. Country selection comes from the
current `CustomerContext` so customers in different countries get different
Tier-1 packs without mutating shared state.
"""

from __future__ import annotations

from collections.abc import Callable

from src.detectors.base import Detection
from src.detectors.countries.algeria import ALGERIA_VALIDATORS
from src.tenancy import require_customer

ValidatorFn = Callable[[str], list[Detection]]

# Country-pack registry. New packs (UAE, Saudi, Morocco...) plug in here
# during Phase 2+ — see ROADMAP "Post-MVP — Expansion".
_COUNTRY_PACKS: dict[str, tuple[ValidatorFn, ...]] = {
    "DZ": ALGERIA_VALIDATORS,
}


class StructuralDetector:
    """Detector A. CPU-only, sub-10ms per request.

    Stateless across requests; safe to share a single instance process-wide.
    """

    name = "structural"

    async def detect(self, text: str) -> list[Detection]:
        ctx = require_customer()
        validators = _COUNTRY_PACKS.get(ctx.country_code, ())
        results: list[Detection] = []
        for validator in validators:
            results.extend(validator(text))
        return results
