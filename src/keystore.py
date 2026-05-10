"""Crypto key resolution at startup.

Three backends, selected via `GATEWAY_KEY_STORE_BACKEND`:
  * `env`   — read keys from `GATEWAY_*_KEY` env vars (dev only).
  * `vault` — read from HashiCorp Vault KV v2 at `GATEWAY_VAULT_KEYS_PATH`.

A real Sovereign deployment uses a PKCS#11 HSM via a small shim that
implements the same `Keys` dataclass shape; the operator points
`GATEWAY_KEY_STORE_BACKEND=hsm` and we add the binding. That shim is
out of scope for this code drop because it requires a real HSM.

The `audit_hmac_key` is allowed to be longer than 32 bytes; the others
must be exactly 32.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.config import Settings


@dataclass(frozen=True, slots=True)
class Keys:
    session_map_key: bytes
    audit_encryption_key: bytes
    audit_hmac_key: bytes


def _hex_to_bytes(label: str, hex_value: str, expected_len: int | None) -> bytes:
    raw = bytes.fromhex(hex_value)
    if expected_len is not None and len(raw) != expected_len:
        raise ValueError(f"{label} must decode to {expected_len} bytes; got {len(raw)}")
    return raw


def _from_env(cfg: Settings) -> Keys:
    return Keys(
        session_map_key=_hex_to_bytes(
            "session_map_key", cfg.session_map_key.get_secret_value(), 32
        ),
        audit_encryption_key=_hex_to_bytes(
            "audit_encryption_key", cfg.audit_encryption_key.get_secret_value(), 32
        ),
        audit_hmac_key=_hex_to_bytes("audit_hmac_key", cfg.audit_hmac_key.get_secret_value(), 32),
    )


def _from_vault(cfg: Settings) -> Keys:
    if not cfg.vault_addr or not cfg.vault_token:
        raise ValueError("vault backend requires GATEWAY_VAULT_ADDR and GATEWAY_VAULT_TOKEN")
    headers = {"X-Vault-Token": cfg.vault_token.get_secret_value()}
    url = f"{cfg.vault_addr.rstrip('/')}/v1/{cfg.vault_keys_path}"
    resp = httpx.get(url, headers=headers, timeout=5.0)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", {}).get("data", {}) or payload.get("data", {})
    try:
        return Keys(
            session_map_key=_hex_to_bytes("session_map_key", data["session_map_key"], 32),
            audit_encryption_key=_hex_to_bytes(
                "audit_encryption_key", data["audit_encryption_key"], 32
            ),
            audit_hmac_key=_hex_to_bytes("audit_hmac_key", data["audit_hmac_key"], 32),
        )
    except KeyError as exc:
        raise ValueError(f"Vault payload missing required key {exc}") from exc


def resolve_keys(cfg: Settings) -> Keys:
    if cfg.key_store_backend == "vault":
        return _from_vault(cfg)
    return _from_env(cfg)
