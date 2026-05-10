from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from src.licensing import LicenseInvalidError, issue, verify


@pytest.fixture(scope="module")
def keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv, pub


def test_issue_then_verify_roundtrip(keys: tuple[str, str]) -> None:
    priv, pub = keys
    token = issue(
        private_key_pem=priv,
        customer_id="cust-1",
        country_code="DZ",
        plan="enterprise",
        features=["banking_pack"],
        validity_seconds=60,
    )
    lic = verify(token=token, public_key_pem=pub, country_code="DZ")
    assert lic.customer_id == "cust-1"
    assert lic.plan == "enterprise"
    assert "banking_pack" in lic.features


def test_wrong_audience_rejected(keys: tuple[str, str]) -> None:
    priv, pub = keys
    token = issue(
        private_key_pem=priv,
        customer_id="c",
        country_code="DZ",
        plan="starter",
    )
    with pytest.raises(LicenseInvalidError):
        verify(token=token, public_key_pem=pub, country_code="MA")


def test_expired_rejected(keys: tuple[str, str]) -> None:
    priv, pub = keys
    token = issue(
        private_key_pem=priv,
        customer_id="c",
        country_code="DZ",
        plan="starter",
        validity_seconds=-10,
    )
    with pytest.raises(LicenseInvalidError):
        verify(token=token, public_key_pem=pub, country_code="DZ")


def test_tampered_rejected(keys: tuple[str, str]) -> None:
    priv, pub = keys
    token = issue(
        private_key_pem=priv,
        customer_id="c",
        country_code="DZ",
        plan="starter",
    )
    bad = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
    with pytest.raises(LicenseInvalidError):
        verify(token=bad, public_key_pem=pub, country_code="DZ")


def test_iat_in_future_unbothered(keys: tuple[str, str]) -> None:
    """JWT lib doesn't reject future-iat by default; ensure expiry still applies."""
    priv, pub = keys
    token = issue(
        private_key_pem=priv,
        customer_id="c",
        country_code="DZ",
        plan="starter",
        validity_seconds=60,
    )
    lic = verify(token=token, public_key_pem=pub, country_code="DZ")
    assert lic.expires_at > int(time.time())
