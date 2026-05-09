from __future__ import annotations

import os
from dataclasses import replace

import pytest
from src.audit import (
    AuditChainBroken,
    AuditWriter,
    InMemoryAuditBackend,
)
from src.tenancy import CustomerContext, MissingTenantScopeError, bind_customer, reset_customer


@pytest.fixture
def writer() -> AuditWriter:
    return AuditWriter(
        backend=InMemoryAuditBackend(),
        encryption_key=os.urandom(32),
        hmac_key=os.urandom(32),
    )


@pytest.fixture
def bound() -> object:
    ctx = CustomerContext(customer_id="cust-audit", country_code="DZ", plan="enterprise")
    token = bind_customer(ctx)
    yield token
    reset_customer(token)


def test_record_requires_tenant_scope(writer: AuditWriter) -> None:
    # No bound customer → fail closed.
    with pytest.raises(MissingTenantScopeError):
        writer.record(
            request_id="r1",
            event_type="detection",
            payload={"detection_count": 3},
        )


def test_chain_links_correctly(writer: AuditWriter, bound: object) -> None:
    r1 = writer.record(request_id="r1", event_type="detection", payload={"detection_count": 1})
    r2 = writer.record(request_id="r2", event_type="detection", payload={"detection_count": 2})
    r3 = writer.record(request_id="r3", event_type="detection", payload={"detection_count": 3})
    assert r1.prev_hash == "0" * 64
    assert r2.prev_hash == r1.content_hash
    assert r3.prev_hash == r2.content_hash
    writer.verify_chain()  # must not raise


def test_tampered_payload_breaks_chain(writer: AuditWriter, bound: object) -> None:
    writer.record(request_id="r1", event_type="detection", payload={"detection_count": 1})
    backend = writer._backend
    original = backend._records[0]
    # Replace ciphertext with garbage; content_hash will no longer match.
    backend._records[0] = replace(original, payload_ciphertext=b"\x00" * 32)
    with pytest.raises(AuditChainBroken):
        writer.verify_chain()


def test_broken_link_detected(writer: AuditWriter, bound: object) -> None:
    writer.record(request_id="r1", event_type="detection", payload={"detection_count": 1})
    writer.record(request_id="r2", event_type="detection", payload={"detection_count": 2})
    backend = writer._backend
    second = backend._records[1]
    backend._records[1] = replace(second, prev_hash="f" * 64)
    with pytest.raises(AuditChainBroken):
        writer.verify_chain()


def test_payload_is_encrypted_not_plaintext(writer: AuditWriter, bound: object) -> None:
    writer.record(
        request_id="r1",
        event_type="detection",
        payload={"detection_count": 7, "marker": "SENTINEL_VALUE_XYZ"},
    )
    backend = writer._backend
    rec = backend._records[0]
    assert b"SENTINEL_VALUE_XYZ" not in rec.payload_ciphertext
