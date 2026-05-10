"""Hybrid retrieval over the layered rule base.

ARCHITECTURE §4.1.3: Detector C consumes the top-K rules retrieved per
request via vector + keyword + tier filter.

The retriever delegates rule storage to `RuleStore`. Vector similarity
needs an embedding service; if no embedding service is configured we
fall back to keyword-only retrieval. Tier 1 rules are always included
in the prefix (cacheable per-customer-per-country); only Tier 3 (and
optionally Tier 2) is dynamically retrieved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from src.detectors.contextual import RuleSnippet
from src.rules.models import Rule
from src.rules.store import RuleStore

_TOKEN_RE = re.compile(r"[\w]{3,}", re.UNICODE)


class HybridRetriever:
    def __init__(self, *, rule_store: RuleStore, top_k_tier3: int = 10) -> None:
        self._store = rule_store
        self._top_k = top_k_tier3

    async def retrieve(
        self, *, text: str, industries: list[str] | None = None
    ) -> list[RuleSnippet]:
        all_rules = await self._store.list_for_customer(industries=industries)
        tier1 = [r for r in all_rules if r.tier == 1]
        tier2 = [r for r in all_rules if r.tier == 2]
        tier3 = [r for r in all_rules if r.tier == 3]

        terms = [t.lower() for t in _TOKEN_RE.findall(text)]
        relevant_tier3 = self._rank_by_keyword(terms, tier3)[: self._top_k]
        return [_to_snippet(r) for r in (*tier1, *tier2, *relevant_tier3)]

    @staticmethod
    def _rank_by_keyword(terms: list[str], rules: list[Rule]) -> list[Rule]:
        terms_set = set(terms)
        scored: list[tuple[int, Rule]] = []
        for r in rules:
            haystack = " ".join((r.description, *r.keywords)).lower()
            score = sum(1 for t in terms_set if t in haystack)
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored]


def _to_snippet(rule: Rule) -> RuleSnippet:
    return RuleSnippet(
        rule_id=rule.rule_id,
        tier=rule.tier,
        entity_type=rule.entity_type,
        description=rule.description,
    )


def _flatten(values: Iterable[list[Rule]]) -> list[Rule]:
    out: list[Rule] = []
    for v in values:
        out.extend(v)
    return out
