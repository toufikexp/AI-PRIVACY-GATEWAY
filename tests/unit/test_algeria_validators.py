"""Unit tests for Algeria Tier-1 structural validators.

ROADMAP Phase 1 verification: every entity type has at least 5 unit tests
covering valid, invalid checksum, and edge cases.
"""

from __future__ import annotations

import pytest
from src.detectors.countries.algeria import (
    _rib_key_valid,
    detect_nif,
    detect_nin,
    detect_phone,
    detect_rib,
)

# ---------- NIN: 18 digits ----------


class TestNIN:
    def test_valid_18_digit_match(self) -> None:
        text = "Citoyen NIN 109876543210123456 enregistré."
        out = detect_nin(text)
        assert len(out) == 1
        assert out[0].text == "109876543210123456"
        assert out[0].entity_type == "national_id"
        assert out[0].tier == 1

    def test_too_short_no_match(self) -> None:
        assert detect_nin("only 17 digits: 12345678901234567") == []

    def test_too_long_no_match(self) -> None:
        # 19 digits should not match — the boundary guards reject the run.
        assert detect_nin("digits 1234567890123456789") == []

    def test_multiple_matches(self) -> None:
        text = "First 109876543210123456 and second 209876543210123456."
        out = detect_nin(text)
        assert {d.text for d in out} == {
            "109876543210123456",
            "209876543210123456",
        }

    def test_no_digits_no_match(self) -> None:
        assert detect_nin("Bonjour, comment ça va?") == []


# ---------- NIF: 15 digits ----------


class TestNIF:
    def test_valid_15_digit_match(self) -> None:
        out = detect_nif("NIF 123456789012345 du contribuable.")
        assert len(out) == 1
        assert out[0].text == "123456789012345"
        assert out[0].entity_type == "tax_id"

    def test_14_digits_no_match(self) -> None:
        assert detect_nif("nif 12345678901234") == []

    def test_16_digits_no_match(self) -> None:
        assert detect_nif("nif 1234567890123456") == []

    def test_embedded_in_longer_run_no_match(self) -> None:
        # The 15-digit run is part of a 17-digit run; guards must reject it.
        assert detect_nif("12345678901234567890") == []

    def test_two_separate_nifs(self) -> None:
        out = detect_nif("A 111111111111111 B 222222222222222")
        assert {d.text for d in out} == {"111111111111111", "222222222222222"}


# ---------- RIB: 20 digits, mod-97 key ----------


class TestRIB:
    VALID_A = "00400123456789012375"  # generated to satisfy mod-97 key
    VALID_B = "01200345678901234548"

    def test_valid_rib_key_passes(self) -> None:
        assert _rib_key_valid(self.VALID_A) is True
        assert _rib_key_valid(self.VALID_B) is True

    def test_invalid_rib_key_rejected(self) -> None:
        # Flip last two digits — almost certainly invalid.
        bad = self.VALID_A[:-2] + "00"
        assert _rib_key_valid(bad) is False
        assert detect_rib(f"compte {bad}") == []

    def test_valid_rib_detected_and_tier1(self) -> None:
        out = detect_rib(f"RIB: {self.VALID_A}")
        assert len(out) == 1
        d = out[0]
        assert d.text == self.VALID_A
        assert d.entity_type == "bank_account"
        assert d.tier == 1
        assert d.confidence >= 0.99

    def test_non_digit_rib_not_validated(self) -> None:
        assert _rib_key_valid("00400123456789012abc") is False

    def test_two_valid_ribs_in_text(self) -> None:
        out = detect_rib(f"old {self.VALID_A} new {self.VALID_B}")
        assert {d.text for d in out} == {self.VALID_A, self.VALID_B}


# ---------- Phone (libphonenumber, region DZ) ----------


class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "+213 555 12 34 56",
            "+213555123456",
            "0555123456",  # national format
            "021 73 22 11",  # landline national
        ],
    )
    def test_valid_phones_detected(self, raw: str) -> None:
        out = detect_phone(f"Contact: {raw} merci.")
        assert len(out) == 1, f"expected match for {raw!r}"
        assert out[0].entity_type == "phone"
        assert out[0].tier == 1

    def test_garbage_no_match(self) -> None:
        assert detect_phone("appelle au plus vite") == []
