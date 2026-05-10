"""Unit tests for Algeria Tier-1 structural validators (full pack).

Every entity type has at least 5 cases: positive, malformed, edge-of-range,
ambiguous, and a multi-occurrence test where applicable.
"""

from __future__ import annotations

import pytest
from src.detectors.countries.algeria import (
    _iban_valid,
    _luhn_ok,
    _mod97_key_valid,
    _nin_plausible,
    detect_card,
    detect_driving_licence,
    detect_email,
    detect_healthcare_card,
    detect_iban,
    detect_ip,
    detect_nif,
    detect_nin,
    detect_nis,
    detect_nss,
    detect_passport,
    detect_phone,
    detect_rib,
    detect_vehicle_plate,
)

# ---------- NIN: 18 digits, gender + plausible YOB ----------


class TestNIN:
    def test_valid_male_2000(self) -> None:
        out = detect_nin("NIN 120004500001234567")
        assert len(out) == 1 and out[0].text == "120004500001234567"

    def test_invalid_gender_zero(self) -> None:
        assert detect_nin("citoyen 020004500001234567") == []

    def test_invalid_year_too_old(self) -> None:
        assert detect_nin("citoyen 117004500001234567") == []

    def test_too_short(self) -> None:
        assert detect_nin("only 17 digits: 12345678901234567") == []

    def test_two_matches(self) -> None:
        out = detect_nin("A 119804500001234567 B 220154500009876543")
        assert {d.text for d in out} == {
            "119804500001234567",
            "220154500009876543",
        }


# ---------- NIF: 15 digits ----------


class TestNIF:
    def test_valid(self) -> None:
        out = detect_nif("NIF 123456789012345")
        assert len(out) == 1

    def test_too_short(self) -> None:
        assert detect_nif("nif 12345678901234") == []

    def test_too_long(self) -> None:
        assert detect_nif("nif 1234567890123456") == []

    def test_embedded_in_longer_run(self) -> None:
        assert detect_nif("12345678901234567890") == []

    def test_two_matches(self) -> None:
        out = detect_nif("A 111111111111111 B 222222222222222")
        assert len(out) == 2


# ---------- NIS: 14 digits ----------


class TestNIS:
    def test_valid(self) -> None:
        out = detect_nis("NIS 12345678901234")
        assert len(out) == 1

    def test_too_short(self) -> None:
        assert detect_nis("ns 1234567890123") == []

    def test_too_long(self) -> None:
        assert detect_nis("ns 123456789012345") == []

    def test_no_digits(self) -> None:
        assert detect_nis("ABCDE") == []

    def test_two_matches(self) -> None:
        out = detect_nis("A 11111111111111 B 22222222222222")
        assert len(out) == 2


# ---------- NSS: 12 digits, gender prefix ----------


class TestNSS:
    def test_valid_male(self) -> None:
        out = detect_nss("NSS 197503040001")
        assert len(out) == 1

    def test_valid_female(self) -> None:
        out = detect_nss("NSS 285012340009")
        assert len(out) == 1

    def test_invalid_prefix(self) -> None:
        assert detect_nss("NSS 397503040001") == []

    def test_too_short(self) -> None:
        assert detect_nss("ns 12345678901") == []

    def test_two_matches(self) -> None:
        out = detect_nss("A 197503040001 B 285012340009")
        assert len(out) == 2


# ---------- RIB / RIP — 20 digits mod-97 ----------


class TestRIB:
    VALID = "00400123456789012375"  # arbitrary valid mod-97 key
    VALID_RIP = "00700123456789012337"  # bank prefix 007 = Algérie Poste

    def test_key_valid(self) -> None:
        assert _mod97_key_valid(self.VALID) is True

    def test_invalid_key(self) -> None:
        assert _mod97_key_valid(self.VALID[:-2] + "00") is False
        assert detect_rib("compte 00400123456789012300") == []

    def test_detected_as_rib(self) -> None:
        out = detect_rib(f"RIB: {self.VALID}")
        assert len(out) == 1 and out[0].rule_id == "dz.rib"

    def test_rip_detected_as_postal(self) -> None:
        # Synthesise a valid Algérie Poste RIP: 18-digit body starting "007".
        body = "007" + "001234567890123"  # exactly 18 digits
        key = 97 - (int(body + "00") % 97)
        rip = f"{body}{key:02d}"
        assert _mod97_key_valid(rip) and rip.startswith("007")
        out = detect_rib(f"CCP: {rip}")
        assert out and out[0].rule_id == "dz.rip"

    def test_two_valid_in_text(self) -> None:
        out = detect_rib(f"old {self.VALID} new {self.VALID}")
        assert len(out) == 2


# ---------- IBAN-DZ — 24 chars, ISO 13616 mod-97 ----------


def _iban_for(body18: str) -> str:
    """Construct a valid DZ IBAN given an 18-digit body."""
    bban = body18 + "00"  # placeholder check digits
    rearranged = bban + "DZ" + "00"
    digits = "".join(c if c.isdigit() else str(ord(c) - 55) for c in rearranged)
    check = 98 - (int(digits) % 97)
    return f"DZ{check:02d}{body18}00"


class TestIBAN:
    def test_iban_helper_returns_valid(self) -> None:
        iban = _iban_for("004001234567890123")
        assert _iban_valid(iban)

    def test_invalid_iban(self) -> None:
        assert _iban_valid("DZ00000000000000000000000") is False

    def test_detected(self) -> None:
        iban = _iban_for("012003456789012345")
        out = detect_iban(f"IBAN: {iban}")
        assert len(out) == 1 and out[0].rule_id == "dz.iban"

    def test_wrong_country_no_match(self) -> None:
        assert detect_iban("FR7630006000011234567890189") == []

    def test_two_in_text(self) -> None:
        a = _iban_for("004001234567890123")
        b = _iban_for("012003456789012345")
        out = detect_iban(f"old {a} new {b}")
        assert len(out) == 2


# ---------- Card — Luhn ----------


class TestCard:
    def test_luhn_helper(self) -> None:
        assert _luhn_ok("4242424242424242")
        assert not _luhn_ok("1234567812345678")

    def test_visa_detected(self) -> None:
        out = detect_card("paiement avec 4242 4242 4242 4242 stop.")
        assert out and out[0].rule_id == "dz.card"

    def test_mastercard_detected(self) -> None:
        out = detect_card("MC: 5555555555554444")
        assert out

    def test_invalid_luhn_rejected(self) -> None:
        assert detect_card("CC 1234567812345678") == []

    def test_amex_15(self) -> None:
        out = detect_card("Amex 378282246310005")
        assert out


# ---------- Passport ----------


class TestPassport:
    def test_p_format(self) -> None:
        out = detect_passport("Passport P1234567 issued.")
        assert out and out[0].text.lower().startswith("p")

    def test_8_digit_biometric(self) -> None:
        out = detect_passport("Carte 12345678")
        assert out

    def test_too_short(self) -> None:
        assert detect_passport("PA12345") == []

    def test_letter_only_rejected(self) -> None:
        assert detect_passport("ABCDEFGH") == []

    def test_two_in_text(self) -> None:
        out = detect_passport("First P1234567 second P7654321.")
        assert len(out) == 2


# ---------- Driving licence ----------


class TestDrivingLicence:
    def test_valid_with_dash(self) -> None:
        out = detect_driving_licence("Permis 16-1234567 délivré.")
        assert out

    def test_valid_with_space(self) -> None:
        out = detect_driving_licence("Permis 16 1234567")
        assert out

    def test_invalid_wilaya_high(self) -> None:
        assert detect_driving_licence("Permis 99 1234567") == []

    def test_invalid_wilaya_zero(self) -> None:
        assert detect_driving_licence("Permis 00 1234567") == []

    def test_no_match_short(self) -> None:
        assert detect_driving_licence("16 12345") == []


# ---------- Vehicle plate ----------


class TestVehiclePlate:
    def test_valid_alger(self) -> None:
        out = detect_vehicle_plate("véhicule 12345-110-16 stationné.")
        assert out

    def test_valid_with_spaces(self) -> None:
        out = detect_vehicle_plate("plaque 12345 110 16")
        assert out

    def test_invalid_wilaya(self) -> None:
        assert detect_vehicle_plate("plaque 12345-110-99") == []

    def test_too_short(self) -> None:
        assert detect_vehicle_plate("plaque 1-2-3") == []

    def test_two_in_text(self) -> None:
        out = detect_vehicle_plate("A 12345-110-16 B 99876-220-31")
        assert len(out) == 2


# ---------- Healthcare card (CHIFA) ----------


class TestHealthcareCard:
    def test_with_chifa_keyword(self) -> None:
        out = detect_healthcare_card("Carte CHIFA 197503040001 valide.")
        assert out and out[0].rule_id == "dz.chifa"

    def test_with_cnas_keyword(self) -> None:
        out = detect_healthcare_card("dossier CNAS no 285012340009 ouvert")
        assert out

    def test_no_keyword_no_match(self) -> None:
        # Same number but no healthcare context — must NOT detect as chifa.
        assert detect_healthcare_card("identifiant 197503040001 only") == []

    def test_invalid_prefix(self) -> None:
        assert detect_healthcare_card("CHIFA 397503040001") == []

    def test_two_in_text(self) -> None:
        out = detect_healthcare_card("Carte CHIFA 197503040001 et CHIFA 285012340009")
        assert len(out) == 2


# ---------- Phone ----------


class TestPhone:
    @pytest.mark.parametrize(
        "raw",
        [
            "+213 555 12 34 56",
            "+213555123456",
            "0555123456",
            "021 73 22 11",
        ],
    )
    def test_valid(self, raw: str) -> None:
        assert detect_phone(f"contact: {raw}")

    def test_garbage(self) -> None:
        assert detect_phone("appelle au plus vite") == []


# ---------- Email ----------


class TestEmail:
    def test_basic(self) -> None:
        assert detect_email("écris à user@example.dz svp.")

    def test_with_plus(self) -> None:
        assert detect_email("alias user+tag@example.dz")

    def test_at_alone(self) -> None:
        assert detect_email("@") == []

    def test_no_tld(self) -> None:
        assert detect_email("user@host") == []

    def test_two_in_text(self) -> None:
        out = detect_email("a@x.dz et b@y.dz")
        assert len(out) == 2


# ---------- IP ----------


class TestIP:
    def test_v4(self) -> None:
        assert detect_ip("server 10.0.1.1 down")

    def test_v6(self) -> None:
        out = detect_ip("addr 2001:db8::1 here")
        assert out

    def test_invalid_v4_octet(self) -> None:
        assert detect_ip("ip 999.0.0.1") == []

    def test_no_match(self) -> None:
        assert detect_ip("la version est 1.2") == []

    def test_two_in_text(self) -> None:
        out = detect_ip("a 10.0.0.1 b 192.168.1.1")
        assert len(out) == 2


# ---------- nin plausibility helper unit tests ----------


def test_nin_plausibility_edges() -> None:
    assert _nin_plausible("119504500001234567")
    assert not _nin_plausible("019504500001234567")  # gender 0
    assert not _nin_plausible("184005500001234567")  # year 1840 < 1850
    assert not _nin_plausible("1abcd")  # not enough digits
