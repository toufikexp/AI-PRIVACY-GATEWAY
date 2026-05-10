from __future__ import annotations

import json
from pathlib import Path

from src.proxy.auth import CustomerDirectory
from src.proxy.customer_seed import (
    ENV_FILE,
    ENV_INLINE,
    seed_directory,
)


def test_seed_from_file(monkeypatch, tmp_path: Path) -> None:
    seed = [
        {
            "api_key": "sk-from-file",
            "customer_id": "cust-1",
            "country_code": "DZ",
            "plan": "enterprise",
            "upstream_provider_key": "sk-up-1",
        }
    ]
    path = tmp_path / "customers.json"
    path.write_text(json.dumps(seed))
    monkeypatch.setenv(ENV_FILE, str(path))
    monkeypatch.delenv(ENV_INLINE, raising=False)
    directory = CustomerDirectory()
    n = seed_directory(directory)
    assert n == 1
    record = directory.lookup("sk-from-file")
    assert record is not None
    assert record.upstream_provider_key == "sk-up-1"


def test_seed_from_inline_env(monkeypatch) -> None:
    monkeypatch.delenv(ENV_FILE, raising=False)
    monkeypatch.setenv(
        ENV_INLINE,
        json.dumps(
            [
                {
                    "api_key": "sk-inline",
                    "customer_id": "cust-2",
                    "country_code": "DZ",
                    "plan": "starter",
                    "upstream_provider_key": "sk-up-2",
                }
            ]
        ),
    )
    directory = CustomerDirectory()
    seed_directory(directory)
    record = directory.lookup("sk-inline")
    assert record is not None
    assert record.customer_id == "cust-2"


def test_seed_missing_field_skipped(monkeypatch) -> None:
    monkeypatch.delenv(ENV_FILE, raising=False)
    monkeypatch.setenv(
        ENV_INLINE,
        json.dumps([{"api_key": "incomplete"}]),
    )
    directory = CustomerDirectory()
    n = seed_directory(directory)
    assert n == 0


def test_seed_no_env_no_op(monkeypatch) -> None:
    monkeypatch.delenv(ENV_FILE, raising=False)
    monkeypatch.delenv(ENV_INLINE, raising=False)
    directory = CustomerDirectory()
    assert seed_directory(directory) == 0
