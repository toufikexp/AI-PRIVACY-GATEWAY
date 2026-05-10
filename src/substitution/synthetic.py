"""Synthetic value generation per entity type.

ARCHITECTURE §4.5.4 / §3.2 substitution strategy: generate culturally
consistent synthetic values, NOT placeholder tags. The aim is to preserve
the LLM's ability to reason with cultural context, gender, social register.

Dictionaries below are deliberately small; production deployments load
country-pack dictionaries at startup. New packs slot in via
`register_pack`.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from src.detectors.base import EntityType


@dataclass(frozen=True, slots=True)
class _Pack:
    persons: tuple[str, ...]
    organizations: tuple[str, ...]
    locations: tuple[str, ...]


_DZ_PACK = _Pack(
    persons=(
        "Karim Hadji",
        "Yacine Mansouri",
        "Amina Belkacem",
        "Sara Bouzid",
        "Walid Ouali",
        "Nadia Cherif",
        "Mehdi Lounis",
        "Lina Saadi",
    ),
    organizations=(
        "Société Générale Algérie",
        "Naftal SPA",
        "Mobilis Telecom",
        "Sonelgaz",
        "Algérie Poste",
    ),
    locations=(
        "Alger",
        "Oran",
        "Constantine",
        "Annaba",
        "Tlemcen",
        "Sétif",
    ),
)

_PACKS: dict[str, _Pack] = {"DZ": _DZ_PACK}


def register_pack(country_code: str, pack: _Pack) -> None:
    _PACKS[country_code] = pack


def _pick(seed: str, pool: tuple[str, ...]) -> str:
    if not pool:
        return f"<{seed[:6]}>"
    digest = hashlib.sha256(seed.encode()).digest()
    idx = int.from_bytes(digest[:8], "big") % len(pool)
    return pool[idx]


def synthetic_for(
    *,
    entity_type: EntityType,
    original: str,
    country_code: str,
    salt: str | None = None,
) -> str:
    """Deterministic-by-salt synthetic substitute for `original`.

    Same `(entity_type, original, salt, country)` always returns the same
    synthetic value within a request — but different requests use different
    salts, so the substitution is unlinkable across requests.
    """
    pack = _PACKS.get(country_code, _DZ_PACK)
    seed = f"{salt or secrets.token_hex(8)}|{entity_type}|{original}|{country_code}"

    if entity_type == "person":
        return _pick(seed, pack.persons)
    if entity_type == "organization":
        return _pick(seed, pack.organizations)
    if entity_type == "location":
        return _pick(seed, pack.locations)
    if entity_type == "phone":
        return _phone_for(seed, country_code)
    if entity_type == "national_id":
        return _digit_string_for(seed, length=18)
    if entity_type == "tax_id":
        return _digit_string_for(seed, length=15)
    if entity_type == "bank_account":
        return _rib_for(seed)
    if entity_type == "card_number":
        return _luhn_for(seed)
    if entity_type == "email":
        return _email_for(seed)
    if entity_type == "date":
        return original  # dates rarely identify; keep verbatim
    if entity_type == "monetary":
        return original
    return f"<{entity_type[:6]}-{seed[:6]}>"


def _digit_string_for(seed: str, *, length: int) -> str:
    digest = hashlib.sha256(seed.encode()).digest()
    digits = "".join(str(b % 10) for b in digest)
    while len(digits) < length:
        digest = hashlib.sha256(digest).digest()
        digits += "".join(str(b % 10) for b in digest)
    return digits[:length]


def _phone_for(seed: str, country_code: str) -> str:
    suffix = _digit_string_for(seed, length=9)
    if country_code == "DZ":
        return f"+213 5{suffix[:2]} {suffix[2:4]} {suffix[4:6]} {suffix[6:8]}"
    return f"+1 555 {suffix[:3]} {suffix[3:7]}"


def _rib_for(seed: str) -> str:
    body = _digit_string_for(seed, length=18)
    key = 97 - (int(body + "00") % 97)
    return f"{body}{key:02d}"


def _luhn_for(seed: str) -> str:
    base = _digit_string_for(seed, length=15)
    digits = [int(d) for d in base]
    # Compute Luhn check digit
    s = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 0:
            doubled = digit * 2
            s += doubled - 9 if doubled > 9 else doubled
        else:
            s += digit
    check = (10 - (s % 10)) % 10
    return base + str(check)


def _email_for(seed: str) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return f"user{digest[:8]}@example.com"
