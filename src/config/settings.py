"""Runtime configuration for the data-plane proxy.

All settings flow through this module. The standard pydantic-settings stack
reads, in priority order:
  1. explicit kwargs (used by tests)
  2. process environment (`GATEWAY_*`)
  3. `.env` file at repo root
  4. defaults below

Production deployments do NOT set crypto keys via env vars. Keys come from
the configured key store (sealed file → Vault → HSM, scaled per plan tier).
The env-var path is for local development only; the genesis hex defaults
of all-zeros are deliberately useless on disk so a missing key surfaces
the misconfiguration immediately.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

FailureMode = Literal["strict", "audit_only", "fallback"]
NERBackend = Literal["stub", "onnx", "transformers"]
VLLMBackend = Literal["stub", "http"]
KeyStoreBackend = Literal["env", "vault"]
RuleStoreBackend = Literal["memory", "postgres"]
AuditStoreBackend = Literal["memory", "postgres"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- Deployment identity -----
    country_code: str = Field(default="DZ", min_length=2, max_length=2)
    plane: Literal["country", "company"] = "country"
    environment: Literal["dev", "staging", "prod"] = "dev"
    software_version: str = "0.1.0"
    rule_pack_version: str = "DZ-1.0.0"

    # ----- Failure mode (per ARCHITECTURE §6.2) -----
    default_failure_mode: FailureMode = "strict"

    # ----- Master plane -----
    master_plane_mock: bool = True
    master_plane_url: str | None = None
    master_plane_api_key: SecretStr | None = None
    master_plane_poll_interval_s: int = 300
    telemetry_push_interval_s: int = 60

    # ----- Upstream LLM providers -----
    upstream_openai_base_url: str = "https://api.openai.com/v1"
    upstream_request_timeout_s: float = 60.0

    # ----- Crypto keys (32 bytes hex). PROD-MUST be loaded from key store. -----
    session_map_key: SecretStr = Field(default=SecretStr("0" * 64))
    audit_encryption_key: SecretStr = Field(default=SecretStr("0" * 64))
    audit_hmac_key: SecretStr = Field(default=SecretStr("0" * 64))

    # ----- Session map lifecycle -----
    session_map_idle_timeout_s: int = 30 * 60

    # ----- Detector B (NER) -----
    ner_backend: NERBackend = "stub"
    ner_model_path: str | None = None  # ONNX file, when ner_backend=onnx
    ner_tokenizer_path: str | None = None
    # transformers backend — HuggingFace model id or local snapshot path
    ner_hf_model: str = "Davlan/distilbert-base-multilingual-cased-ner-hrl"
    ner_aggregation: Literal["simple", "first", "average", "max"] = "simple"

    # ----- Key store -----
    key_store_backend: KeyStoreBackend = "env"
    vault_addr: str | None = None
    vault_token: SecretStr | None = None
    vault_keys_path: str = "secret/data/llm-privacy-gateway/keys"

    # ----- Customer auth -----
    customer_store_backend: Literal["memory", "postgres"] = "memory"

    # ----- Licensing -----
    license_public_key_pem: str | None = None  # PEM contents, multi-line env var
    license_required: bool = False  # if True, fail startup without a valid license
    license_token: SecretStr | None = None  # the signed license JWT

    # ----- Observability -----
    metrics_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None  # e.g. http://otel-collector:4318

    # ----- Detector C (contextual LLM via vLLM HTTP) -----
    vllm_backend: VLLMBackend = "stub"
    vllm_url: str | None = None
    vllm_model: str = "Qwen/Qwen2.5-7B-Instruct-AWQ"
    vllm_request_timeout_s: float = 30.0
    vllm_top_k_rules: int = 10  # Tier 3 rules retrieved per request

    # ----- Detection thresholds (per tier) -----
    tier1_confidence_threshold: float = 0.80
    tier2_confidence_threshold: float = 0.70
    tier3_confidence_threshold: float = 0.65

    # ----- Storage -----
    rule_store_backend: RuleStoreBackend = "memory"
    audit_store_backend: AuditStoreBackend = "memory"
    postgres_dsn: SecretStr | None = None  # postgresql://user:pass@host:5432/db
    redis_url: str | None = None  # redis://localhost:6379/0

    # ----- Logging -----
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; instantiated once per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Invalidate the cached Settings. Used by tests when env-driven config changes."""
    get_settings.cache_clear()
