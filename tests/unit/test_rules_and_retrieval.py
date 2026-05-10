from __future__ import annotations

import pytest
from src.retrieval import HybridRetriever
from src.rules import InMemoryRuleStore, Rule, RuleException, RuleExceptionStore
from src.tenancy import CustomerContext, bind_customer, reset_customer


@pytest.fixture
def bound() -> object:
    ctx = CustomerContext(customer_id="cust-rs", country_code="DZ", plan="enterprise")
    token = bind_customer(ctx)
    yield token
    reset_customer(token)


@pytest.fixture
def store(bound: object) -> InMemoryRuleStore:
    return InMemoryRuleStore(
        seed=[
            Rule(
                rule_id="dz.nin",
                tier=1,
                entity_type="national_id",
                description="Algerian NIN",
                country_code="DZ",
                industry=None,
                customer_id=None,
            ),
            Rule(
                rule_id="banking.iban",
                tier=2,
                entity_type="bank_account",
                description="IBAN structure",
                country_code=None,
                industry="banking",
                customer_id=None,
                keywords=("iban", "swift"),
            ),
            Rule(
                rule_id="cust-rs.codename",
                tier=3,
                entity_type="custom",
                description="Project Atlas codename",
                country_code=None,
                industry=None,
                customer_id="cust-rs",
                keywords=("atlas",),
            ),
            # Tier 3 belonging to a different tenant — must not surface.
            Rule(
                rule_id="other.codename",
                tier=3,
                entity_type="custom",
                description="Other tenant secret",
                country_code=None,
                industry=None,
                customer_id="someone-else",
            ),
        ]
    )


async def test_list_for_customer_includes_tier1_country(store: InMemoryRuleStore) -> None:
    rules = await store.list_for_customer()
    rule_ids = {r.rule_id for r in rules}
    assert "dz.nin" in rule_ids


async def test_list_for_customer_excludes_other_tenant(store: InMemoryRuleStore) -> None:
    rules = await store.list_for_customer()
    rule_ids = {r.rule_id for r in rules}
    assert "other.codename" not in rule_ids


async def test_list_for_customer_filters_industries(store: InMemoryRuleStore) -> None:
    no_filter = await store.list_for_customer()
    with_filter = await store.list_for_customer(industries=["telecom"])
    assert any(r.rule_id == "banking.iban" for r in no_filter)
    assert not any(r.rule_id == "banking.iban" for r in with_filter)


async def test_upsert_tier3_rejects_other_tenant(store: InMemoryRuleStore) -> None:
    with pytest.raises(PermissionError):
        await store.upsert_tier3(
            Rule(
                rule_id="x",
                tier=3,
                entity_type="custom",
                description="x",
                country_code=None,
                industry=None,
                customer_id="not-me",
            )
        )


async def test_retrieval_finds_matching_tier3(store: InMemoryRuleStore) -> None:
    retriever = HybridRetriever(rule_store=store, top_k_tier3=3)
    snippets = await retriever.retrieve(text="The Atlas project is sensitive.")
    ids = [s.rule_id for s in snippets]
    assert "cust-rs.codename" in ids
    assert "dz.nin" in ids  # Tier 1 always included


async def test_exceptions_scoped_per_customer(bound: object) -> None:
    es = RuleExceptionStore()
    await es.add(
        RuleException(
            exception_id="exc-1",
            customer_id="cust-rs",
            rule_id="dz.nin",
            entity_type=None,
            text_match="123456789012345678",
        )
    )
    entries = await es.list_active()
    assert len(entries) == 1
    assert entries[0].rule_id == "dz.nin"
