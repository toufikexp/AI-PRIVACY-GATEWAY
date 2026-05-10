"""License token issuance (master plane) and verification (data plane).

A license token is a JWT signed with RS256 by the master plane's private
key. The data plane carries only the master plane's public key (PEM). The
token's claims are:

  iss   = "llm-privacy-gateway-master"
  sub   = customer_id
  aud   = country_code   ("DZ", "AE", ...)
  plan  = "starter" | "professional" | "enterprise" | "sovereign"
  iat, exp = standard JWT timestamps
  features = list[str]   — feature flags enabled for this plan

Sovereign tier deployments embed the token in `GATEWAY_LICENSE_TOKEN` and
operate offline for the entire validity period — the data plane never
needs to reach the master plane to decide whether to serve.

If `GATEWAY_LICENSE_REQUIRED=true` and the token is missing or invalid,
`create_app` aborts startup. This is the fail-closed default for
production deployments. Dev/CI deployments set `GATEWAY_LICENSE_REQUIRED=false`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt


class LicenseInvalidError(RuntimeError):
    """Raised when license verification fails."""


@dataclass(frozen=True, slots=True)
class License:
    customer_id: str
    country_code: str
    plan: str
    issued_at: int
    expires_at: int
    features: tuple[str, ...]


def issue(
    *,
    private_key_pem: str,
    customer_id: str,
    country_code: str,
    plan: str,
    features: list[str] | None = None,
    validity_seconds: int = 365 * 24 * 3600,
) -> str:
    """Sign a license token. Used by the master plane CLI."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "llm-privacy-gateway-master",
        "sub": customer_id,
        "aud": country_code,
        "plan": plan,
        "iat": now,
        "exp": now + validity_seconds,
        "features": list(features or []),
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def verify(*, token: str, public_key_pem: str, country_code: str) -> License:
    """Verify a license token. Raises `LicenseInvalidError` on any failure."""
    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=["RS256"],
            audience=country_code,
            issuer="llm-privacy-gateway-master",
        )
    except jwt.InvalidTokenError as exc:
        raise LicenseInvalidError(str(exc)) from exc

    return License(
        customer_id=str(payload["sub"]),
        country_code=str(payload["aud"]),
        plan=str(payload["plan"]),
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
        features=tuple(payload.get("features", [])),
    )
