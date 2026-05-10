from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# Tier 1 = country regulatory (immutable). Tier 2 = industry. Tier 3 = customer.
Tier = Literal[1, 2, 3]

# MVP entity vocabulary; expanded as country/industry packs land.
EntityType = Literal[
    "national_id",  # NIN, Iqama, Emirates ID, etc.
    "tax_id",  # NIF, VAT, etc.
    "social_security",  # NSS, NSF, etc.
    "bank_account",  # IBAN, RIB, RIP
    "card_number",  # PAN (Luhn-validated)
    "passport",
    "driving_licence",
    "vehicle_plate",
    "healthcare_id",  # carte chifa, etc.
    "professional_licence",  # avocat, médecin, expert
    "phone",
    "email",
    "person",
    "organization",
    "location",
    "date",
    "monetary",
    "ip_address",
    "custom",  # Tier 3 customer-defined
]


@dataclass(frozen=True, slots=True)
class Detection:
    """A single sensitive-entity span produced by a detector.

    Spans are validated against the original input text by the merge engine
    (CLAUDE.md hard rule #6: vLLM detector outputs are span-validated).
    """

    entity_type: EntityType
    start: int
    end: int
    text: str
    confidence: float
    tier: Tier
    detector: str
    rule_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"confidence must be in [0,1]; got {self.confidence}")
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid span: start={self.start} end={self.end}")


@runtime_checkable
class Detector(Protocol):
    """Common interface implemented by Detector A, B, and C.

    All detectors are async because B (NER) and C (vLLM) are I/O bound.
    A (structural) is CPU-only but conforms to the same contract so the
    merge engine can run them via `asyncio.gather` without special-casing.
    """

    name: str

    async def detect(self, text: str) -> list[Detection]: ...
