"""Postgres-backed rule store and exception store."""

from __future__ import annotations

import asyncpg

from src.merge.engine import ExceptionEntry
from src.rules.exceptions import RuleException
from src.rules.models import Rule
from src.tenancy import require_customer


class PostgresRuleStore:
    def __init__(self, *, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_customer(self, *, industries: list[str] | None = None) -> list[Rule]:
        ctx = require_customer()
        params: list[object] = [ctx.country_code, ctx.customer_id]
        clauses = [
            "(tier = 1 AND country_code = $1)",
            "(tier = 3 AND customer_id = $2)",
        ]
        if industries:
            clauses.append("(tier = 2 AND industry = ANY($3))")
            params.append(industries)
        else:
            clauses.append("(tier = 2)")
        sql = f"SELECT * FROM rules WHERE enabled AND ({' OR '.join(clauses)})"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_row_to_rule(r) for r in rows]

    async def search_keywords(self, *, terms: list[str], limit: int) -> list[Rule]:
        ctx = require_customer()
        if not terms:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM rules
                WHERE enabled
                  AND (
                    (tier = 1 AND country_code = $1)
                    OR (tier = 2)
                    OR (tier = 3 AND customer_id = $2)
                  )
                  AND (
                    description ILIKE ANY ($3::text[])
                    OR keywords && $4::text[]
                  )
                LIMIT $5
                """,
                ctx.country_code,
                ctx.customer_id,
                [f"%{t}%" for t in terms],
                terms,
                limit,
            )
        return [_row_to_rule(r) for r in rows]

    async def upsert_tier3(self, rule: Rule) -> None:
        ctx = require_customer()
        if rule.tier != 3 or rule.customer_id != ctx.customer_id:
            raise PermissionError("Tier 3 rule must belong to the current customer")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rules (rule_id, tier, entity_type, description, country_code,
                                   industry, customer_id, enabled, confidence_floor, keywords)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (rule_id) DO UPDATE SET
                    description = EXCLUDED.description,
                    entity_type = EXCLUDED.entity_type,
                    enabled = EXCLUDED.enabled,
                    confidence_floor = EXCLUDED.confidence_floor,
                    keywords = EXCLUDED.keywords,
                    updated_at = now()
                WHERE rules.customer_id = EXCLUDED.customer_id
                """,
                rule.rule_id,
                rule.tier,
                rule.entity_type,
                rule.description,
                rule.country_code,
                rule.industry,
                rule.customer_id,
                rule.enabled,
                rule.confidence_floor,
                list(rule.keywords),
            )

    async def delete_tier3(self, rule_id: str) -> None:
        ctx = require_customer()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM rules WHERE rule_id = $1 AND tier = 3 AND customer_id = $2",
                rule_id,
                ctx.customer_id,
            )


class PostgresExceptionStore:
    def __init__(self, *, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_active(self) -> list[ExceptionEntry]:
        ctx = require_customer()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT rule_id, entity_type, text_match FROM rule_exceptions WHERE customer_id = $1",
                ctx.customer_id,
            )
        return [
            ExceptionEntry(
                rule_id=r["rule_id"],
                entity_type=r["entity_type"],
                text_match=r["text_match"],
            )
            for r in rows
        ]

    async def add(self, exc: RuleException) -> None:
        ctx = require_customer()
        if exc.customer_id != ctx.customer_id:
            raise PermissionError("exception customer_id does not match request scope")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO rule_exceptions
                  (exception_id, customer_id, rule_id, entity_type, text_match, note)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                exc.exception_id,
                exc.customer_id,
                exc.rule_id,
                exc.entity_type,
                exc.text_match,
                exc.note,
            )

    async def remove(self, exception_id: str) -> None:
        ctx = require_customer()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM rule_exceptions WHERE exception_id = $1 AND customer_id = $2",
                exception_id,
                ctx.customer_id,
            )


def _row_to_rule(row: asyncpg.Record) -> Rule:
    return Rule(
        rule_id=row["rule_id"],
        tier=row["tier"],
        entity_type=row["entity_type"],
        description=row["description"],
        country_code=row["country_code"],
        industry=row["industry"],
        customer_id=row["customer_id"],
        enabled=row["enabled"],
        confidence_floor=row["confidence_floor"],
        keywords=tuple(row["keywords"] or ()),
    )
