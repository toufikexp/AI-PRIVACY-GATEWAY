---
name: rule-authority
description: Three-tier rule authority model. Use when working on rule storage, rule editing APIs, exception management, plan-tier enforcement, rule lifecycle, or country/industry pack ingestion. Covers the immutable Tier 1 / configurable Tier 2 / customer-owned Tier 3 model and the Tier 1 exception mechanism.
---

# Three-Tier Rule Authority

## The model

| Tier | Owner | Editable | Purpose |
|------|-------|----------|---------|
| Tier 1 — Country | Vendor | Immutable rules + customer-defined exceptions | Statutory PII per national law |
| Tier 2 — Industry | Vendor publishes; customer enables | Disable, retag tier, override substitution | Industry-specific patterns |
| Tier 3 — Customer | Customer | Full ownership | Proprietary entities, internal codenames |

Country rules (Tier 1) are ALWAYS enforced regardless of plan. Plan tier only affects what the customer can edit, not what the system enforces.

## Schema

```sql
-- Rules table — all three tiers live here
CREATE TABLE rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier SMALLINT NOT NULL CHECK (tier IN (1, 2, 3)),
    customer_id UUID NULL,  -- NULL for shared (Tier 1, Tier 2); set for Tier 3
    owning_module_id UUID NOT NULL REFERENCES rule_modules(id),
    entity_type VARCHAR(64) NOT NULL,
    rule_type VARCHAR(32) NOT NULL CHECK (rule_type IN ('pattern', 'dictionary', 'llm_hint')),
    config JSONB NOT NULL,  -- pattern, validators, context keywords, etc.
    embedding vector(768),  -- for retrieval
    rule_tsv tsvector,  -- for keyword retrieval
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'stale', 'flagged', 'deprecated')),
    created_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by VARCHAR(128) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT tier1_no_customer CHECK (tier != 1 OR customer_id IS NULL),
    CONSTRAINT tier3_has_customer CHECK (tier != 3 OR customer_id IS NOT NULL)
);

CREATE INDEX rules_customer_idx ON rules (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX rules_module_idx ON rules (owning_module_id);
CREATE INDEX rules_embedding_idx ON rules USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX rules_tsv_idx ON rules USING gin (rule_tsv);

-- Modules
CREATE TABLE rule_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(16) NOT NULL CHECK (type IN ('country', 'industry', 'customer')),
    name VARCHAR(128) NOT NULL,
    version VARCHAR(32) NOT NULL,
    parent_module_id UUID NULL REFERENCES rule_modules(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    applicable_countries TEXT[],  -- ISO 3166-1 codes
    applicable_industries TEXT[],
    UNIQUE (type, name, version)
);

-- Audit log for rule changes
CREATE TABLE rule_audit_log (
    id BIGSERIAL PRIMARY KEY,
    rule_id UUID NOT NULL,
    change_type VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    before_state JSONB,
    after_state JSONB,
    reason TEXT,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Customer exceptions to Tier 1 rules
CREATE TABLE rule_exceptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL,
    target_tier1_rule_id UUID NOT NULL REFERENCES rules(id),
    exception_type VARCHAR(32) NOT NULL CHECK (exception_type IN ('literal_value', 'pattern', 'contextual')),
    exception_config JSONB NOT NULL,
    justification TEXT NOT NULL CHECK (length(justification) >= 20),
    approved_by VARCHAR(128) NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expiry_date TIMESTAMPTZ NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'revoked')),
    created_by VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX rule_exceptions_customer_idx ON rule_exceptions (customer_id, status);

-- Customer config
CREATE TABLE customer_config (
    customer_id UUID PRIMARY KEY,
    enabled_modules UUID[],
    threshold_overrides JSONB DEFAULT '{}'::jsonb,
    sensitivity_routing JSONB DEFAULT '{}'::jsonb,
    failure_mode VARCHAR(16) NOT NULL DEFAULT 'strict' CHECK (failure_mode IN ('strict', 'audit_only', 'fallback'))
);
```

## Tier 1 — Immutability and exceptions

Tier 1 rules cannot be modified by the customer. Period. Any API path that attempts a mutating operation on a Tier 1 rule by a customer-scoped principal MUST raise `ImmutableRuleViolationError`.

But customers DO have legitimate cases where a Tier 1 detection produces a false positive in their context. Example: a bank's internal product code happens to be 18 numeric digits and matches Algerian NIN format.

The mechanism: **exception entries**, not rule modification.

### Exception types

```python
@dataclass
class LiteralValueException:
    """Specific known-safe values that should never be flagged."""
    values: list[str]  # e.g., ["198507150045123456", "198507150045789012"]

@dataclass
class PatternException:
    """Pattern that distinguishes customer's non-sensitive use."""
    regex: str  # e.g., r"PRD-\d{2}-\d{18}"  (product code prefix)
    description: str

@dataclass
class ContextualException:
    """Tier 1 detections in specific contexts are suppressed."""
    context_keywords: list[str]  # e.g., ["product code", "inventory id", "SKU"]
    window_size: int = 50  # chars before/after the candidate detection
```

### Application in merge

```python
class ExceptionEngine:
    def apply(self, detections: List[Detection], customer_id: UUID) -> List[Detection]:
        active_exceptions = self._load_active_exceptions(customer_id)
        if not active_exceptions:
            return detections

        result = []
        for d in detections:
            if d.tier != 1:
                result.append(d)
                continue

            # Check if any active exception matches this Tier 1 detection
            matching_exception = self._find_matching_exception(d, active_exceptions)
            if matching_exception:
                # Don't add to result — exception suppresses the detection
                # But DO log to audit trail
                self._log_exception_application(d, matching_exception, customer_id)
                continue

            result.append(d)
        return result
```

### Audit trail for exceptions

Every Tier 1 detection that is suppressed by an exception MUST be logged with the exception ID. This produces a queryable record showing:
- Which Tier 1 detections are being suppressed for this customer
- How often each exception fires
- The justification text approved by the compliance officer
- The approval chain

Compliance officers and auditors can review this. Frequently-firing exceptions are flagged for re-validation.

### Plan-tier limits on exceptions

| Plan | Max active exceptions |
|------|----------------------|
| Starter | 10 |
| Professional | 100 |
| Enterprise | Unlimited |
| Sovereign | Unlimited |

The limit is enforced at exception creation time. If a customer attempts to create an exception when at limit, the API returns 402 Payment Required with a clear message about plan limits.

## Tier 2 — Industry packs

Vendor-curated. Customer can:
- **Enable / disable** the pack as a whole (e.g., banking pack)
- **Disable specific rules** within an enabled pack (e.g., disable SWIFT BIC detection if not relevant)
- **Retag tier severity** (e.g., raise a Tier 2 rule to Tier 1 priority for stricter handling)
- **Override substitution strategy** (e.g., use synthetic instead of mask for credit cards)

Customer cannot:
- Edit the underlying regex patterns or detection logic
- Change the rule's structural fingerprint (which would corrupt vendor's update path)

When the vendor releases a new version of a pack, the customer's overrides MUST persist. Don't reset customer customization on upgrade.

```python
class IndustryPackUpgrade:
    def upgrade(self, pack_name: str, new_version: str, customer_id: UUID) -> UpgradeResult:
        old_module = db.fetch_active_module(pack_name, customer_id)
        new_module = db.fetch_module(pack_name, new_version)

        # Customer's overrides on individual rules in old_module
        overrides = db.fetch_overrides(old_module.id, customer_id)

        # Apply overrides to corresponding rules in new_module by stable rule_key
        # (entity_type + rule_type, not rule_id which may change between versions)
        for rule in new_module.rules:
            override = overrides.get((rule.entity_type, rule.rule_type))
            if override:
                rule = self._apply_override(rule, override)

        # Activate new_module for this customer; deactivate old_module
        ...
```

## Tier 3 — Customer rules

Full customer ownership. Customer compliance team creates, edits, deletes, retunes any Tier 3 rule.

Validation at authoring time (Section 5.6.1 of `docs/ARCHITECTURE.md`):
- Pattern overlap analysis: does the new rule subsume or get subsumed by existing rules?
- Substitution conflict detection: do two rules apply different strategies to overlapping types?
- Tier consistency: a Tier 3 rule cannot suppress a Tier 1 detection (use exception mechanism instead).
- Test corpus pre-check: estimate trigger frequency on customer's recent (anonymized) audit log.

```python
@router.post("/rules", response_model=RuleResponse)
async def create_tier3_rule(
    rule_data: TenancyScopedRuleCreate,
    current_user: User = Depends(require_role("compliance_officer")),
    customer_id: UUID = Depends(get_customer_id),
) -> RuleResponse:
    # Plan-tier limit check
    plan = await plan_service.get_plan(customer_id)
    current_count = await rules_repo.count_tier3_rules(customer_id)
    if plan.max_tier3_rules is not None and current_count >= plan.max_tier3_rules:
        raise HTTPException(402, "Tier 3 rule limit reached for current plan")

    # Validation
    validation = await rule_validator.validate(rule_data, customer_id)
    if validation.has_conflicts:
        return RuleResponse(status="needs_resolution", conflicts=validation.conflicts)

    # Create
    rule = await rules_repo.create_tier3(rule_data, customer_id, current_user.id)

    # Generate embedding asynchronously (don't block API response)
    background_tasks.add_task(embedding_service.embed_rule, rule.id)

    return RuleResponse(status="created", rule=rule)
```

## Plan-flag enforcement

Plan flags gate dashboard UI elements AND back-end API endpoints. Both layers MUST enforce; UI-only enforcement is bypassable.

```python
def require_plan_capability(capability: str):
    """FastAPI dependency that checks the customer's plan supports a capability."""
    async def dependency(customer_id: UUID = Depends(get_customer_id)) -> None:
        plan = await plan_service.get_plan(customer_id)
        if not plan.has_capability(capability):
            raise HTTPException(402, f"Capability '{capability}' not in current plan")
    return dependency

@router.post(
    "/rule-modules/{module_id}/disable",
    dependencies=[Depends(require_plan_capability("disable_tier2_rules"))]
)
async def disable_tier2_rule(...):
    ...
```

## Hard rules

1. **Tier 1 rules are immutable from customer-facing code paths.** Vendor-only updates via signed master plane releases.
2. **Tier 1 false-positive suppression goes through the exception mechanism, not rule modification.**
3. **Every rule mutation logs to `rule_audit_log` with before/after JSONB.** No exceptions.
4. **Plan flag checks happen at API layer, not just UI.** UI-only checks are bypassable.
5. **Customer's overrides on Tier 2 rules persist through pack upgrades.** Reset breaks customer trust.
6. **Exception entries require non-trivial justification text.** Database constraint: `length(justification) >= 20`.
7. **`customer_id` filter required on every Tier 3 query.** No exceptions.

## Testing patterns

```python
# Test that Tier 1 mutation by customer principal is rejected
async def test_customer_cannot_mutate_tier1_rule(client, customer_user, tier1_rule):
    response = await client.patch(
        f"/rules/{tier1_rule.id}",
        json={"config": {"pattern": "modified"}},
        headers=customer_user.auth_headers,
    )
    assert response.status_code == 403

# Test that exception suppresses Tier 1 detection
async def test_exception_suppresses_tier1_detection(detection_pipeline, customer):
    await create_exception(customer, target_rule="ALGERIAN_NIN", literal_values=["198507150045123456"])
    detections = await detection_pipeline.detect("My ID is 198507150045123456", customer.id)
    assert all(d.entity_type != "ALGERIAN_NIN" for d in detections)
    # Verify exception use was audited
    audit_entries = await audit_repo.fetch_recent(customer.id)
    assert any(e.event_type == "tier1_exception_applied" for e in audit_entries)

# Test plan limit enforcement
async def test_starter_plan_blocks_excess_tier3_rules(client, starter_customer):
    # Create up to limit
    for i in range(25):
        response = await client.post("/rules", json={...}, headers=starter_customer.auth_headers)
        assert response.status_code == 200
    # 26th should fail
    response = await client.post("/rules", json={...}, headers=starter_customer.auth_headers)
    assert response.status_code == 402
```
