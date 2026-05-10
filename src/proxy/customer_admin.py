"""Admin CLI for the data-plane customer directory.

Usage:
    python -m src.proxy.customer_admin create \\
        --customer cust-1 --country DZ --plan enterprise \\
        --upstream-key sk-proj-XXXX

    python -m src.proxy.customer_admin rotate --customer cust-1
    python -m src.proxy.customer_admin disable --customer cust-1

Reads `GATEWAY_POSTGRES_DSN` and `GATEWAY_AUDIT_ENCRYPTION_KEY` (or the
configured key store) from the environment, exactly like the proxy does.
The created/rotated raw API key is printed to stdout — print once, store
in your key vault, never log again.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from src.config import get_settings
from src.db import migrate
from src.keystore import resolve_keys
from src.proxy.customer_store import PostgresCustomerStore


async def _store() -> PostgresCustomerStore:
    cfg = get_settings()
    if cfg.postgres_dsn is None:
        raise SystemExit("GATEWAY_POSTGRES_DSN must be set")
    pool = await asyncpg.create_pool(dsn=cfg.postgres_dsn.get_secret_value())
    await migrate(pool)
    keys = resolve_keys(cfg)
    return PostgresCustomerStore(pool=pool, encryption_key=keys.audit_encryption_key)


async def cmd_create(args: argparse.Namespace) -> None:
    store = await _store()
    api_key = await store.create(
        customer_id=args.customer,
        country_code=args.country,
        plan=args.plan,
        upstream_provider_key=args.upstream_key,
        failure_mode=args.failure_mode,
    )
    sys.stdout.write(f"{api_key}\n")


async def cmd_rotate(args: argparse.Namespace) -> None:
    store = await _store()
    api_key = await store.rotate_key(customer_id=args.customer)
    sys.stdout.write(f"{api_key}\n")


async def cmd_disable(args: argparse.Namespace) -> None:
    store = await _store()
    await store.disable(customer_id=args.customer)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="customer_admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create")
    create.add_argument("--customer", required=True)
    create.add_argument("--country", required=True)
    create.add_argument(
        "--plan", required=True, choices=["starter", "professional", "enterprise", "sovereign"]
    )
    create.add_argument("--upstream-key", required=True)
    create.add_argument(
        "--failure-mode", default="strict", choices=["strict", "audit_only", "fallback"]
    )

    rotate = sub.add_parser("rotate")
    rotate.add_argument("--customer", required=True)

    disable = sub.add_parser("disable")
    disable.add_argument("--customer", required=True)

    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "create":
        asyncio.run(cmd_create(args))
    elif args.cmd == "rotate":
        asyncio.run(cmd_rotate(args))
    elif args.cmd == "disable":
        asyncio.run(cmd_disable(args))


if __name__ == "__main__":
    main()
