from __future__ import annotations

import os

import pytest
from src.detectors.base import Detection
from src.substitution import (
    SessionMapStore,
    apply_substitution,
    reverse_substitution,
)
from src.substitution.synthetic import _rib_for, synthetic_for
from src.tenancy import CustomerContext, bind_customer, reset_customer


@pytest.fixture
def bound_dz() -> object:
    ctx = CustomerContext(customer_id="cust-x", country_code="DZ", plan="enterprise")
    token = bind_customer(ctx)
    yield token
    reset_customer(token)


@pytest.fixture
def session_store(bound_dz: object) -> SessionMapStore:
    return SessionMapStore(key=os.urandom(32), idle_timeout_s=300)


def test_synthetic_phone_format_dz() -> None:
    s = synthetic_for(entity_type="phone", original="0555000000", country_code="DZ", salt="abc")
    assert s.startswith("+213 ")


def test_synthetic_rib_satisfies_mod97() -> None:
    s = _rib_for("seedX")
    assert len(s) == 20 and s.isdigit()
    body, key = s[:18], s[18:]
    assert (97 - (int(body + "00") % 97)) == int(key)


def test_forward_substitution_replaces_and_records(
    session_store: SessionMapStore, bound_dz: object
) -> None:
    text = "Mon numéro est +213 555 12 34 56 voici."
    det = Detection(
        entity_type="phone",
        start=15,
        end=33,
        text=text[15:33],
        confidence=0.99,
        tier=1,
        detector="structural.dz",
        rule_id="dz.phone",
    )
    sm = session_store.open(request_id="r-1")
    res = apply_substitution(
        original_text=text,
        detections=[det],
        session_map=sm,
        country_code="DZ",
        salt="salt-1",
    )
    assert det.text not in res.sanitised_text
    assert "+213 " in res.sanitised_text
    # Synthetic appears in the components map
    assert any("+213" in syn for syn in res.components_by_synthetic)


def test_reverse_substitution_round_trip(session_store: SessionMapStore, bound_dz: object) -> None:
    text = "Hello Mohamed Benali, your account is open."
    det = Detection(
        entity_type="person",
        start=6,
        end=20,
        text="Mohamed Benali",
        confidence=0.95,
        tier=2,
        detector="ner",
    )
    sm = session_store.open(request_id="r-2")
    forward = apply_substitution(
        original_text=text,
        detections=[det],
        session_map=sm,
        country_code="DZ",
        salt="salt-2",
    )
    # Pretend the upstream LLM repeated the synthetic verbatim
    rev = reverse_substitution(
        response_text=forward.sanitised_text,
        session_map=sm,
        components_by_synthetic=forward.components_by_synthetic,
    )
    assert "Mohamed Benali" in rev.text
    assert rev.direct_reversed >= 1


def test_reverse_substitution_handles_first_name_only(
    session_store: SessionMapStore, bound_dz: object
) -> None:
    text = "Hello Mohamed Benali."
    det = Detection(
        entity_type="person",
        start=6,
        end=20,
        text="Mohamed Benali",
        confidence=0.95,
        tier=2,
        detector="ner",
    )
    sm = session_store.open(request_id="r-3")
    forward = apply_substitution(
        original_text=text,
        detections=[det],
        session_map=sm,
        country_code="DZ",
        salt="salt-3",
    )
    # Find the synthetic first name and use it alone in the response.
    full_synthetic = next(iter(forward.components_by_synthetic.keys()))
    first_synth = full_synthetic.split()[0]
    response = f"OK {first_synth} thanks."
    rev = reverse_substitution(
        response_text=response,
        session_map=sm,
        components_by_synthetic=forward.components_by_synthetic,
    )
    # First-name component reversed → original first name back in the text
    original_first = "Mohamed"
    assert original_first in rev.text


def test_session_map_purges_after_close(session_store: SessionMapStore, bound_dz: object) -> None:
    sm = session_store.open(request_id="r-purge")
    assert sm.is_purged is False
    session_store.close("r-purge")
    assert sm.is_purged is True
