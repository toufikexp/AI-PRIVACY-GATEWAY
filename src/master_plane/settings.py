"""Master-plane configuration."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MasterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MASTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    postgres_dsn: SecretStr = Field(
        default=SecretStr("postgresql://master:master@localhost:5433/master")
    )
    admin_token: SecretStr = Field(
        default=SecretStr("admin-dev-token"),
        description="Token required by the admin API (POST /admin/*).",
    )
    license_private_key_pem: SecretStr | None = Field(
        default=None,
        description="RSA private key (PEM, multiline) used to sign license tokens.",
    )
    license_public_key_pem: str | None = Field(
        default=None,
        description="RSA public key counterpart, served at GET /v1/license/public-key.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True


@lru_cache(maxsize=1)
def get_master_settings() -> MasterSettings:
    return MasterSettings()
