"""Admin CLI for the master plane.

Usage:
    python -m src.master_plane.admin keygen --out keys/master
    python -m src.master_plane.admin create-customer --id cust-1 \\
        --company "Acme Bank" --country DZ --plan enterprise
    python -m src.master_plane.admin issue-license --id cust-1 --days 365
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
import httpx

from src.master_plane.settings import get_master_settings


def cmd_keygen(args: argparse.Namespace) -> None:
    """Generate an RSA keypair for license signing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (out / "license.private.pem").write_bytes(priv_pem)
    (out / "license.public.pem").write_bytes(pub_pem)
    sys.stdout.write(f"wrote {out / 'license.private.pem'} and {out / 'license.public.pem'}\n")


async def cmd_create_customer(args: argparse.Namespace) -> None:
    cfg = get_master_settings()
    base_url = args.master_url
    headers = {"Authorization": f"Bearer {cfg.admin_token.get_secret_value()}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10) as client:
        resp = await client.post(
            "/admin/customers",
            json={
                "customer_id": args.id,
                "company_name": args.company,
                "contact_email": args.email,
                "country_code": args.country,
                "plan": args.plan,
                "failure_mode": args.failure_mode,
            },
        )
        resp.raise_for_status()
        sys.stdout.write(resp.text + "\n")


async def cmd_issue_license(args: argparse.Namespace) -> None:
    cfg = get_master_settings()
    base_url = args.master_url
    headers = {"Authorization": f"Bearer {cfg.admin_token.get_secret_value()}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10) as client:
        resp = await client.post(
            "/admin/licenses",
            json={"customer_id": args.id, "validity_days": args.days},
        )
        resp.raise_for_status()
        sys.stdout.write(resp.text + "\n")


async def cmd_init_db(args: argparse.Namespace) -> None:
    cfg = get_master_settings()
    pool = await asyncpg.create_pool(dsn=cfg.postgres_dsn.get_secret_value())
    from src.master_plane.db import migrate

    await migrate(pool)
    await pool.close()
    sys.stdout.write("schema applied\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="master_admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("--out", default="keys/master")

    cc = sub.add_parser("create-customer")
    cc.add_argument("--master-url", default="http://localhost:9090")
    cc.add_argument("--id", required=True)
    cc.add_argument("--company", required=True)
    cc.add_argument("--country", required=True)
    cc.add_argument(
        "--plan", required=True, choices=["starter", "professional", "enterprise", "sovereign"]
    )
    cc.add_argument("--email", default=None)
    cc.add_argument(
        "--failure-mode", default="strict", choices=["strict", "audit_only", "fallback"]
    )

    il = sub.add_parser("issue-license")
    il.add_argument("--master-url", default="http://localhost:9090")
    il.add_argument("--id", required=True)
    il.add_argument("--days", type=int, default=365)

    sub.add_parser("init-db")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "keygen":
        cmd_keygen(args)
    elif args.cmd == "create-customer":
        asyncio.run(cmd_create_customer(args))
    elif args.cmd == "issue-license":
        asyncio.run(cmd_issue_license(args))
    elif args.cmd == "init-db":
        asyncio.run(cmd_init_db(args))


if __name__ == "__main__":
    main()
