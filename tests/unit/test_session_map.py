from __future__ import annotations

import os
import time

import pytest
from src.substitution import (
    EncryptedSessionMap,
    SessionMapEntry,
    SessionMapStore,
    SessionPurgedError,
)
from src.tenancy import CustomerContext, bind_customer, reset_customer


@pytest.fixture
def key() -> bytes:
    return os.urandom(32)


@pytest.fixture
def bound_customer() -> object:
    ctx = CustomerContext(customer_id="cust-x", country_code="DZ", plan="starter")
    token = bind_customer(ctx)
    yield token
    reset_customer(token)


def test_roundtrip(key: bytes, bound_customer: object) -> None:
    sm = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-1")
    sm.add(SessionMapEntry("Mohamed Benali", "Karim Hadji", "person"))
    got = sm.reverse("Karim Hadji")
    assert got is not None
    assert got.original == "Mohamed Benali"
    assert got.entity_type == "person"


def test_reverse_unknown_returns_none(key: bytes, bound_customer: object) -> None:
    sm = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-2")
    assert sm.reverse("not present") is None


def test_purge_blocks_further_access(key: bytes, bound_customer: object) -> None:
    sm = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-3")
    sm.add(SessionMapEntry("orig", "syn", "person"))
    sm.purge()
    assert sm.is_purged is True
    with pytest.raises(SessionPurgedError):
        sm.reverse("syn")
    with pytest.raises(SessionPurgedError):
        sm.add(SessionMapEntry("a", "b", "person"))


def test_idle_timeout(key: bytes, bound_customer: object) -> None:
    sm = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-4")
    assert sm.is_idle(timeout_s=1) is False
    # Force last_access into the past
    sm.last_access = time.monotonic() - 100
    assert sm.is_idle(timeout_s=1) is True


def test_store_sweeps_idle_maps(key: bytes, bound_customer: object) -> None:
    store = SessionMapStore(key=key, idle_timeout_s=1)
    sm1 = store.open(request_id="r1")
    sm2 = store.open(request_id="r2")
    sm1.last_access = time.monotonic() - 100  # stale
    purged = store.sweep_idle()
    assert purged == 1
    assert sm1.is_purged is True
    assert sm2.is_purged is False


def test_aad_binds_ciphertext_to_request(key: bytes, bound_customer: object) -> None:
    sm = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-A")
    sm.add(SessionMapEntry("orig", "syn", "person"))
    # Steal the ciphertext, plant it in a different request — decryption must fail.
    nonce, ct = sm._encrypted["syn"]
    other = EncryptedSessionMap(key=key, customer_id="cust-x", request_id="req-B")
    other._encrypted["syn"] = (nonce, ct)
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        other.reverse("syn")
