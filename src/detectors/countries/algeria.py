"""Algeria (DZ) Tier-1 structural validators.

Authoritative sources:
- NIN (Numéro d'Identification National): 18 digits, issued by ONS / Ministry
  of the Interior. Format documented by national identity authority.
- NIF (Numéro d'Identification Fiscale): 15 digits, issued by DGI.
- RIB (Relevé d'Identité Bancaire): 20 digits = bank(3) + branch(5) +
  account(11) + RIB key(2). Key is computed mod 97 over a transformed digit
  string (standard Algerian bank format).
- Phone: validated via libphonenumber under region "DZ".

Each validator is a pure function: text -> list[Detection]. The structural
detector composes them. Per ROADMAP Phase 1 verification: every entity type
has at least 5 unit tests covering valid, invalid checksum, and edge cases.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

import phonenumbers

from src.detectors.base import Detection, EntityType

DETECTOR_NAME = "structural.dz"

# Word-boundary-style guards: digits should not be preceded/followed by
# another digit (avoids matching the inside of a longer numeric run).
# We reject if the surrounding char is a digit.

_NIN_RE = re.compile(r"(?<!\d)(\d{18})(?!\d)")
_NIF_RE = re.compile(r"(?<!\d)(\d{15})(?!\d)")
_RIB_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")


def _make_detection(
    *,
    entity_type: EntityType,
    start: int,
    end: int,
    text: str,
    confidence: float,
    rule_id: str,
) -> Detection:
    return Detection(
        entity_type=entity_type,
        start=start,
        end=end,
        text=text,
        confidence=confidence,
        tier=1,
        detector=DETECTOR_NAME,
        rule_id=rule_id,
    )


def detect_nin(text: str) -> list[Detection]:
    """Algerian NIN: 18 digits. No public checksum spec; accept by length+context.

    To reduce false positives on raw 18-digit numeric strings we use a
    confidence ceiling of 0.85 (vs. 0.99 for checksum-validated identifiers).
    """
    out: list[Detection] = []
    for m in _NIN_RE.finditer(text):
        out.append(
            _make_detection(
                entity_type="national_id",
                start=m.start(1),
                end=m.end(1),
                text=m.group(1),
                confidence=0.85,
                rule_id="dz.nin",
            )
        )
    return out


def detect_nif(text: str) -> list[Detection]:
    """Algerian NIF: 15 digits. Length-only validation; confidence 0.85."""
    out: list[Detection] = []
    for m in _NIF_RE.finditer(text):
        out.append(
            _make_detection(
                entity_type="tax_id",
                start=m.start(1),
                end=m.end(1),
                text=m.group(1),
                confidence=0.85,
                rule_id="dz.nif",
            )
        )
    return out


def _rib_key_valid(rib: str) -> bool:
    """Verify the 2-digit RIB key (last 2 digits) using mod-97 standard.

    The standard Algerian RIB key check is the same family as French RIB:
        key = 97 - (int(bank + branch + account + "00") mod 97)
    We accept the value when computed key equals the trailing 2 digits.
    """
    if len(rib) != 20 or not rib.isdigit():
        return False
    body, key = rib[:18], rib[18:]
    expected = 97 - (int(body + "00") % 97)
    return f"{expected:02d}" == key


def detect_rib(text: str) -> list[Detection]:
    """Algerian RIB: 20 digits with mod-97 key. Validated → confidence 0.99."""
    out: list[Detection] = []
    for m in _RIB_RE.finditer(text):
        rib = m.group(1)
        if not _rib_key_valid(rib):
            continue
        out.append(
            _make_detection(
                entity_type="bank_account",
                start=m.start(1),
                end=m.end(1),
                text=rib,
                confidence=0.99,
                rule_id="dz.rib",
            )
        )
    return out


def detect_phone(text: str) -> list[Detection]:
    """Algerian phone via libphonenumber. Validated → confidence 0.99."""
    out: list[Detection] = []
    for match in phonenumbers.PhoneNumberMatcher(text, "DZ"):
        if not phonenumbers.is_valid_number(match.number):
            continue
        out.append(
            _make_detection(
                entity_type="phone",
                start=match.start,
                end=match.end,
                text=match.raw_string,
                confidence=0.99,
                rule_id="dz.phone",
            )
        )
    return out


ALGERIA_VALIDATORS: tuple[Callable[[str], list[Detection]], ...] = (
    detect_nin,
    detect_nif,
    detect_rib,
    detect_phone,
)


def run_all(text: str) -> Iterable[Detection]:
    for validator in ALGERIA_VALIDATORS:
        yield from validator(text)
