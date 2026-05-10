from __future__ import annotations

import os

import pytest
from src.config import Settings
from src.keystore import resolve_keys


def _hex32() -> str:
    return os.urandom(32).hex()


def test_resolve_env_keys() -> None:
    cfg = Settings(
        session_map_key=_hex32(),
        audit_encryption_key=_hex32(),
        audit_hmac_key=_hex32(),
    )
    keys = resolve_keys(cfg)
    assert len(keys.session_map_key) == 32
    assert len(keys.audit_encryption_key) == 32
    assert len(keys.audit_hmac_key) == 32


def test_resolve_env_rejects_short_key() -> None:
    cfg = Settings(
        session_map_key="dead",
        audit_encryption_key=_hex32(),
        audit_hmac_key=_hex32(),
    )
    with pytest.raises(ValueError):
        resolve_keys(cfg)


def test_vault_backend_requires_addr() -> None:
    cfg = Settings(key_store_backend="vault")
    with pytest.raises(ValueError):
        resolve_keys(cfg)
