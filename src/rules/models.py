"""Rule storage models — Tier 1 country, Tier 2 industry, Tier 3 customer."""

from __future__ import annotations

from dataclasses import dataclass

from src.detectors.base import EntityType, Tier


@dataclass(frozen=True, slots=True)
class Rule:
    """Versioned rule row.

    `customer_id` is None for Tier 1 + 2 (vendor-curated) and required for
    Tier 3 (customer-defined). `country_code` is required for Tier 1.
    """

    rule_id: str
    tier: Tier
    entity_type: EntityType
    description: str
    country_code: str | None
    industry: str | None
    customer_id: str | None
    enabled: bool = True
    confidence_floor: float = 0.0
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.tier == 1 and not self.country_code:
            raise ValueError("Tier 1 rules require a country_code")
        if self.tier == 3 and not self.customer_id:
            raise ValueError("Tier 3 rules require a customer_id")
