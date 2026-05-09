from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

FailureMode = Literal["strict", "audit_only", "fallback"]


class Settings(BaseSettings):
    """Runtime configuration for the data-plane proxy.

    All sensitive material (DB DSN, upstream API keys, encryption keys) flows
    through `SecretStr` so it never lands in repr()/log output by default.
    """

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Deployment identity
    country_code: str = Field(
        default="DZ",
        description="ISO 3166-1 alpha-2 country code for this data plane.",
    )
    plane: Literal["country", "company"] = "country"
    environment: Literal["dev", "staging", "prod"] = "dev"

    # Failure mode (per ARCHITECTURE.md §6.2)
    default_failure_mode: FailureMode = "strict"

    # Master plane
    master_plane_mock: bool = Field(
        default=True,
        description="Skip master-plane polling and use dev_plan_flags.json. Dev only.",
    )
    master_plane_url: str | None = None

    # Upstream LLM providers
    upstream_openai_base_url: str = "https://api.openai.com/v1"
    upstream_request_timeout_s: float = 60.0

    # Crypto keys (32 bytes hex-encoded). Keys are tier-managed in production
    # (sealed file → Vault → HSM), see audit-and-security skill.
    session_map_key: SecretStr = Field(
        default=SecretStr("0" * 64),
        description="AES-256-GCM key for session map encryption (hex-encoded 32 bytes).",
    )
    audit_encryption_key: SecretStr = Field(
        default=SecretStr("0" * 64),
        description="AES-256-GCM key for audit field encryption (hex-encoded 32 bytes).",
    )
    audit_hmac_key: SecretStr = Field(
        default=SecretStr("0" * 64),
        description="HMAC-SHA256 key for audit chain signatures (hex-encoded 32 bytes).",
    )

    # Session map lifecycle
    session_map_idle_timeout_s: int = Field(
        default=30 * 60,
        description="Auto-purge session maps after this many seconds of inactivity.",
    )

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; instantiated once per process."""
    return Settings()
