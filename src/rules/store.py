"""Rule store — read-mostly access to the layered rule base.

Two backends:
  * `InMemoryRuleStore` — used in tests and dev when no Postgres DSN is set.
  * `PostgresRuleStore` — production backend. See `src/rules/postgres.py`.

CLAUDE.md hard rule #3 (multi-tenant isolation): every customer-scoped
read goes through `require_customer()` and pins the query to that
customer_id. Tier 1 / Tier 2 rules are keyed by country and industry
respectively — they're shared across customers but immutable from
customer-facing APIs (CLAUDE.md hard rule #4).
"""

from __future__ import annotations

from typing import Protocol

from src.rules.models import Rule
from src.tenancy import require_customer


class RuleStore(Protocol):
    async def list_for_customer(self, *, industries: list[str] | None = None) -> list[Rule]: ...
    async def search_keywords(self, *, terms: list[str], limit: int) -> list[Rule]: ...
    async def upsert_tier3(self, rule: Rule) -> None: ...
    async def delete_tier3(self, rule_id: str) -> None: ...


class InMemoryRuleStore:
    """Lightweight rule store; NOT shared across processes."""

    def __init__(self, seed: list[Rule] | None = None) -> None:
        self._rules: dict[str, Rule] = {}
        for r in seed or ():
            self._rules[r.rule_id] = r

    async def list_for_customer(self, *, industries: list[str] | None = None) -> list[Rule]:
        ctx = require_customer()
        out: list[Rule] = []
        for r in self._rules.values():
            if not r.enabled:
                continue
            if r.tier == 1 and r.country_code == ctx.country_code:
                out.append(r)
                continue
            if r.tier == 2 and (not industries or r.industry in industries):
                out.append(r)
                continue
            if r.tier == 3 and r.customer_id == ctx.customer_id:
                out.append(r)
        return out

    async def search_keywords(self, *, terms: list[str], limit: int) -> list[Rule]:
        ctx = require_customer()
        scored: list[tuple[int, Rule]] = []
        for r in self._rules.values():
            if not r.enabled:
                continue
            if r.tier == 3 and r.customer_id != ctx.customer_id:
                continue
            if r.tier == 1 and r.country_code != ctx.country_code:
                continue
            score = sum(
                1
                for t in terms
                if t.lower() in r.description.lower()
                or t.lower() in (k.lower() for k in r.keywords)
            )
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    async def upsert_tier3(self, rule: Rule) -> None:
        ctx = require_customer()
        if rule.tier != 3:
            raise ValueError("only Tier 3 rules can be upserted via this method")
        if rule.customer_id != ctx.customer_id:
            raise PermissionError("Tier 3 rule must belong to the current customer")
        self._rules[rule.rule_id] = rule

    async def delete_tier3(self, rule_id: str) -> None:
        ctx = require_customer()
        existing = self._rules.get(rule_id)
        if existing is None:
            return
        if existing.tier != 3 or existing.customer_id != ctx.customer_id:
            raise PermissionError("can only delete own Tier 3 rules")
        self._rules.pop(rule_id, None)
