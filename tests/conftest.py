from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

# Ensure the src/ layout is importable without installing the package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tenancy import CustomerContext, bind_customer, reset_customer  # noqa: E402


@pytest.fixture
def algeria_customer() -> Iterator[CustomerContext]:
    ctx = CustomerContext(
        customer_id="cust-test-dz",
        country_code="DZ",
        plan="professional",
    )
    token = bind_customer(ctx)
    try:
        yield ctx
    finally:
        reset_customer(token)
