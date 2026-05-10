"""Seed the in-memory customer directory at startup from a JSON file or env var.

Two sources, in priority order:
  1. `GATEWAY_CUSTOMERS_FILE`  — path to a JSON file with the schema below.
  2. `GATEWAY_CUSTOMERS_JSON`  — the JSON value inline as an env var.

Schema:
  [
    {
      "api_key": "sk-dev-1",
      "customer_id": "cust-dev",
      "country_code": "DZ",
      "plan": "enterprise",
      "upstream_provider_key": "sk-proj-..."
    }
  ]

Production deployments seed the directory from the `customer_config` table
instead — this module is for dev/CI/sovereign-offline scenarios where a
file-shipped seed is the right interface.

Reads only at startup; no hot-reload. Restart the proxy after edits.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable

import structlog

from src.proxy.auth import CustomerDirectory, CustomerRecord

log = structlog.get_logger(__name__)

ENV_FILE = "GATEWAY_CUSTOMERS_FILE"
ENV_INLINE = "GATEWAY_CUSTOMERS_JSON"


def seed_directory(directory: CustomerDirectory) -> int:
    """Apply seed config to `directory`. Returns the number of records added."""
    records = list(_load_records())
    for r in records:
        directory.register(r.api_key, r.record)
    if records:
        log.info(
            "customer_directory_seeded",
            count=len(records),
            customer_ids=[r.record.customer_id for r in records],
        )
    return len(records)


class _SeedRecord:
    __slots__ = ("api_key", "record")

    def __init__(self, api_key: str, record: CustomerRecord) -> None:
        self.api_key = api_key
        self.record = record


def _load_records() -> Iterable[_SeedRecord]:
    path = os.environ.get(ENV_FILE)
    inline = os.environ.get(ENV_INLINE)
    # Typed as `object` so the post-parse `isinstance(raw, list)` guard
    # is reachable — `json.load` and `json.loads` return `Any`, and we
    # treat malformed input as a soft error.
    raw: object = []
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            log.warning("customers_file_missing", path=path)
            return
        except json.JSONDecodeError as exc:
            log.error("customers_file_invalid_json", path=path, error=str(exc))
            return
    elif inline:
        try:
            raw = json.loads(inline)
        except json.JSONDecodeError as exc:
            log.error("customers_inline_invalid_json", error=str(exc))
            return
    else:
        return

    if not isinstance(raw, list):
        log.error("customers_seed_must_be_list")
        return

    for entry in raw:
        try:
            yield _SeedRecord(
                api_key=str(entry["api_key"]),
                record=CustomerRecord(
                    customer_id=str(entry["customer_id"]),
                    country_code=str(entry["country_code"]),
                    plan=str(entry["plan"]),
                    upstream_provider_key=str(entry["upstream_provider_key"]),
                ),
            )
        except KeyError as exc:
            log.error("customer_seed_entry_missing_field", field=str(exc), entry=entry)
            continue
