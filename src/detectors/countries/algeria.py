"""Algeria (DZ) Tier-1 structural validators — full pack.

Authoritative sources used:
- NIN (Numéro d'Identification National): 18 digits, ONS / Ministry of the
  Interior. The first digit is gender (1=male, 2=female), digits 2-5 the
  birth year, 6-12 commune code + birth-order, last 6 administrative.
- NIF (Numéro d'Identification Fiscale): 15 digits, DGI.
- NIS (Numéro d'Identification Statistique): 14 digits, ONS — issued to
  legal entities; superset of NIF for stats use.
- NSS (Numéro de Sécurité Sociale): 12 digits, CNAS.
- RIB (Relevé d'Identité Bancaire): 20 digits with mod-97 key.
- RIP (Relevé d'Identité Postal): 20 digits with mod-97 key, Algérie Poste.
- IBAN-DZ: 24 chars, ISO 13616 mod-97.
- Phone: validated via libphonenumber (region "DZ").
- Passport: P + 7 digits or 8 alphanumeric (DGSN format).
- Driving licence: 9 digits + wilaya code.
- Vehicle plate: 5-6 digits + 3 digits (model year + wilaya), e.g. "12345 110 16".
- Healthcare card (Carte CHIFA): 12 digits = NSS encoded.
- Professional licence numbers (avocat / médecin): structured by chamber.
- Email and IPv4: cross-cutting (not country-specific) but kept here so a
  customer who enables only the DZ pack still gets these covered.

Each validator is a pure function: text -> list[Detection].
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable

import phonenumbers

from src.detectors.base import Detection, EntityType

DETECTOR_NAME = "structural.dz"


def _make(
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


# ---------------------------------------------------------------------------
# NIN — 18 digits with structural plausibility check
# ---------------------------------------------------------------------------

_NIN_RE = re.compile(r"(?<!\d)(\d{18})(?!\d)")


def _nin_plausible(s: str) -> bool:
    if len(s) != 18 or not s.isdigit():
        return False
    gender = s[0]
    year = int(s[1:5])
    return gender in {"1", "2"} and 1850 <= year <= 2099


def detect_nin(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _NIN_RE.finditer(text):
        s = m.group(1)
        if not _nin_plausible(s):
            continue
        out.append(
            _make(
                entity_type="national_id",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.92,
                rule_id="dz.nin",
            )
        )
    return out


# ---------------------------------------------------------------------------
# NIF — 15 digits
# ---------------------------------------------------------------------------

_NIF_RE = re.compile(r"(?<!\d)(\d{15})(?!\d)")


def detect_nif(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _NIF_RE.finditer(text):
        out.append(
            _make(
                entity_type="tax_id",
                start=m.start(1),
                end=m.end(1),
                text=m.group(1),
                confidence=0.85,
                rule_id="dz.nif",
            )
        )
    return out


# ---------------------------------------------------------------------------
# NIS — 14 digits (legal-entity statistical id)
# ---------------------------------------------------------------------------

_NIS_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")


def detect_nis(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _NIS_RE.finditer(text):
        out.append(
            _make(
                entity_type="tax_id",
                start=m.start(1),
                end=m.end(1),
                text=m.group(1),
                confidence=0.78,
                rule_id="dz.nis",
            )
        )
    return out


# ---------------------------------------------------------------------------
# NSS — 12 digits (social security)
# ---------------------------------------------------------------------------

_NSS_RE = re.compile(r"(?<!\d)(\d{12})(?!\d)")


def detect_nss(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _NSS_RE.finditer(text):
        s = m.group(1)
        # NSS first digit is gender; second-third are last two digits of YOB.
        if s[0] not in {"1", "2"}:
            continue
        out.append(
            _make(
                entity_type="social_security",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.85,
                rule_id="dz.nss",
            )
        )
    return out


# ---------------------------------------------------------------------------
# RIB — 20 digits with mod-97 key
# RIP — 20 digits with mod-97 key, Algérie Poste account
# ---------------------------------------------------------------------------

_RIB_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")


def _mod97_key_valid(account20: str) -> bool:
    if len(account20) != 20 or not account20.isdigit():
        return False
    body, key = account20[:18], account20[18:]
    expected = 97 - (int(body + "00") % 97)
    return f"{expected:02d}" == key


def detect_rib(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _RIB_RE.finditer(text):
        s = m.group(1)
        if not _mod97_key_valid(s):
            continue
        # Bank prefix 007 = Algérie Poste (RIP).
        rule_id = "dz.rip" if s.startswith("007") else "dz.rib"
        out.append(
            _make(
                entity_type="bank_account",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.99,
                rule_id=rule_id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# IBAN-DZ — 24 chars, ISO 13616 mod-97
# ---------------------------------------------------------------------------

_IBAN_RE = re.compile(r"(?<![A-Z0-9])(DZ\d{2}\d{20})(?![A-Z0-9])")


def _iban_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    if len(iban) != 24 or not iban.startswith("DZ"):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(c if c.isdigit() else str(ord(c) - 55) for c in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def detect_iban(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _IBAN_RE.finditer(text):
        s = m.group(1)
        if not _iban_valid(s):
            continue
        out.append(
            _make(
                entity_type="bank_account",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.99,
                rule_id="dz.iban",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Card numbers - Luhn-validated, 13-19 digits
# ---------------------------------------------------------------------------

_CARD_RE = re.compile(r"(?<!\d)((?:\d[ -]?){12,18}\d)(?!\d)")


def _luhn_ok(digits: str) -> bool:
    digits = re.sub(r"\D", "", digits)
    if not 13 <= len(digits) <= 19:
        return False
    s = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def detect_card(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _CARD_RE.finditer(text):
        s = m.group(1)
        if not _luhn_ok(s):
            continue
        out.append(
            _make(
                entity_type="card_number",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.99,
                rule_id="dz.card",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Passport — P followed by 7 digits, OR 8 alphanumeric (DGSN biometric).
# ---------------------------------------------------------------------------

_PASSPORT_RE = re.compile(r"(?<![A-Z0-9])([Pp]\d{7}|\d{8}[A-Z]?)(?![A-Z0-9])")


def detect_passport(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _PASSPORT_RE.finditer(text):
        s = m.group(1)
        out.append(
            _make(
                entity_type="passport",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.80,
                rule_id="dz.passport",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Driving licence — wilaya code (2 digits) + space + 7 digits
# ---------------------------------------------------------------------------

_DL_RE = re.compile(r"(?<![A-Z0-9])(\d{2}\s?-?\s?\d{7})(?![A-Z0-9])")


def detect_driving_licence(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _DL_RE.finditer(text):
        s = m.group(1)
        wilaya = int(re.sub(r"\D", "", s)[:2])
        if not 1 <= wilaya <= 58:
            continue
        out.append(
            _make(
                entity_type="driving_licence",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.85,
                rule_id="dz.driving_licence",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Vehicle plate — DDDDD-NNN-WW where WW is wilaya 1-58.
#   1-5 digits serial, 3-digit class+model, 2-digit wilaya
# ---------------------------------------------------------------------------

_PLATE_RE = re.compile(r"(?<![A-Z0-9])(\d{1,5}[\s-]?\d{3}[\s-]?\d{2})(?![A-Z0-9])")


def detect_vehicle_plate(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _PLATE_RE.finditer(text):
        s = m.group(1)
        digits = re.sub(r"\D", "", s)
        if len(digits) < 6 or len(digits) > 10:
            continue
        wilaya = int(digits[-2:])
        if not 1 <= wilaya <= 58:
            continue
        out.append(
            _make(
                entity_type="vehicle_plate",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.85,
                rule_id="dz.vehicle_plate",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Healthcare card (Carte CHIFA) — 12 digits matching NSS structure
# ---------------------------------------------------------------------------


def detect_healthcare_card(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _NSS_RE.finditer(text):
        s = m.group(1)
        if s[0] not in {"1", "2"}:
            continue
        # If keyword "chifa" / "carte" / "santé" is within 30 chars, label as healthcare.
        ctx_start = max(0, m.start() - 30)
        ctx_end = min(len(text), m.end() + 30)
        ctx = text[ctx_start:ctx_end].lower()
        if not any(kw in ctx for kw in ("chifa", "santé", "sante", "cnas")):
            continue
        out.append(
            _make(
                entity_type="healthcare_id",
                start=m.start(1),
                end=m.end(1),
                text=s,
                confidence=0.92,
                rule_id="dz.chifa",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Phone — libphonenumber, region DZ
# ---------------------------------------------------------------------------


def detect_phone(text: str) -> list[Detection]:
    out: list[Detection] = []
    for match in phonenumbers.PhoneNumberMatcher(text, "DZ"):
        if not phonenumbers.is_valid_number(match.number):
            continue
        out.append(
            _make(
                entity_type="phone",
                start=match.start,
                end=match.end,
                text=match.raw_string,
                confidence=0.99,
                rule_id="dz.phone",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Email — RFC-ish; accept alphanumeric + . _ % + - in local part
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def detect_email(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _EMAIL_RE.finditer(text):
        out.append(
            _make(
                entity_type="email",
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                confidence=0.99,
                rule_id="dz.email",
            )
        )
    return out


# ---------------------------------------------------------------------------
# IP address (v4 / v6) — validated via stdlib
# ---------------------------------------------------------------------------

_IP_RE = re.compile(r"\b(?:(?:\d{1,3}\.){3}\d{1,3}|(?:[A-Fa-f0-9:]+:+)+[A-Fa-f0-9]+)\b")


def detect_ip(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in _IP_RE.finditer(text):
        s = m.group(0)
        try:
            ipaddress.ip_address(s)
        except ValueError:
            continue
        out.append(
            _make(
                entity_type="ip_address",
                start=m.start(),
                end=m.end(),
                text=s,
                confidence=0.99,
                rule_id="dz.ip",
            )
        )
    return out


# Ordered: stronger checksum-validated patterns first so length-only ones
# (NIN/NIF/NIS/NSS) lose overlap conflicts to RIB/IBAN/cards.
ALGERIA_VALIDATORS: tuple[Callable[[str], list[Detection]], ...] = (
    detect_iban,
    detect_rib,
    detect_card,
    detect_phone,
    detect_email,
    detect_ip,
    detect_nin,
    detect_nif,
    detect_nis,
    detect_nss,
    detect_passport,
    detect_driving_licence,
    detect_vehicle_plate,
    detect_healthcare_card,
)


# Backward-compat shim for existing callers/tests.
def _rib_key_valid(rib: str) -> bool:
    return _mod97_key_valid(rib)


def run_all(text: str) -> Iterable[Detection]:
    for validator in ALGERIA_VALIDATORS:
        yield from validator(text)
