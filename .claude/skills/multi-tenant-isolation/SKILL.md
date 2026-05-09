---
name: multi-tenant-isolation
description: Multi-tenant isolation model for the country data plane. Use when working on database queries, cache implementations, session management, or any code that touches data shared across customers. This is the primary security boundary that protects against cross-customer data leakage. Cross-tenant leakage is a fatal product failure.
---

# Multi-Tenant Isolation Model

## Why this matters

A country data plane serves multiple customers simultaneously. A bank, a telecom, and a government agency may share the same VM. Their data MUST NEVER cross.

This is enforced at multiple layers:
1. **Per-request statelessness** — no session state shared across requests
2. **`customer_id` propagation** — every DB query, cache key, log line carries customer_id
3. **Cache partitioning** — keys namespaced by customer_id
4. **vLLM inference isolation** — only safe shared state is in the prefix cache
5. **Audit log isolation** — query scoping enforced at app and DB layer

## The customer_id propagation rule

**Every database query touching customer-scoped data MUST include `customer_id` in the WHERE clause.** Code that doesn't follows this is a defect.

Enforcement: a custom database access layer that enforces this at the application level.

```python
class TenantScopedRepository:
    """Base class for repositories that handle tenant-scoped data.

    Every method MUST take customer_id as the first parameter (after self).
    Queries that don't filter by customer_id raise MissingTenantScopeError.
    """

    def __init__(self, db: Database):
        self.db = db

    async def fetch_one(self, customer_id: UUID, query: str, *args) -> Record | None:
        if not self._query_has_customer_filter(query):
            raise MissingTenantScopeError(
                f"Query missing customer_id filter: {query[:100]}..."
            )
        return await self.db.fetch_one(query, customer_id, *args)

    @staticmethod
    def _query_has_customer_filter(query: str) -> bool:
        # Heuristic: query must reference customer_id in a WHERE clause
        # Production version uses sqlglot to AST-parse
        normalized = query.lower()
        return "customer_id" in normalized and ("where" in normalized or "set" in normalized)
```

This is a guardrail, not a substitute for thinking. Real isolation comes from disciplined query writing. The check catches mistakes.

### CI test

```python
# tests/integration/test_tenant_isolation.py
async def test_no_query_paths_skip_customer_id():
    """Audit all repo methods to ensure customer_id is required."""
    for repo_class in [RulesRepository, ExceptionsRepository, AuditRepository, ...]:
        for method in inspect.getmembers(repo_class, predicate=inspect.isfunction):
            if method[0].startswith("_"):
                continue
            sig = inspect.signature(method[1])
            assert "customer_id" in sig.parameters, \
                f"{repo_class.__name__}.{method[0]} missing customer_id parameter"
```

## Per-request statelessness

Session maps (real ↔ synthetic substitutions) MUST live in process memory only for the duration of one request, then be purged. They MUST NOT persist across requests, MUST NOT be written to disk, MUST NOT be shared across customers.

```python
class SessionMap:
    """In-memory bidirectional substitution map for a single request.

    AES-256-GCM encrypted. Purged after response delivery.
    """

    def __init__(self, customer_id: UUID, request_id: UUID):
        self.customer_id = customer_id
        self.request_id = request_id
        self._key = secrets.token_bytes(32)  # ephemeral, never persisted
        self._cipher = AESGCM(self._key)
        self._real_to_synthetic: dict[bytes, bytes] = {}  # encrypted
        self._synthetic_to_real: dict[bytes, bytes] = {}
        self._created_at = time.monotonic()

    def add(self, real: str, synthetic: str, components: list[Component]) -> None:
        # Register full form
        self._add_pair(real, synthetic)
        # Register components (e.g., "Karim" → "Mohamed", "M. Hadji" → "M. Benali")
        for comp in components:
            self._add_pair(comp.real_form, comp.synthetic_form)

    def reverse(self, synthetic_text: str) -> str:
        # ... apply reverse substitution
        ...

    def purge(self) -> None:
        """MUST be called when request ends. Wipes memory."""
        # Overwrite key bytes (best effort; Python doesn't guarantee)
        for i in range(len(self._key)):
            self._key = b"\x00" * len(self._key)
        self._real_to_synthetic.clear()
        self._synthetic_to_real.clear()
```

The proxy MUST call `purge()` on every request, including error paths. Use `try/finally` or context managers, never bare cleanup.

```python
@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    customer_id: UUID = Depends(get_customer_id),
):
    request_id = uuid4()
    session_map = SessionMap(customer_id, request_id)
    try:
        sanitized = await pipeline.sanitize(request, session_map, customer_id)
        upstream_response = await upstream_provider.forward(sanitized)
        final = await pipeline.de_sanitize(upstream_response, session_map)
        return final
    finally:
        session_map.purge()
```

### Idle timeout backstop

If a request is somehow abandoned (e.g., proxy crashes mid-response), there's a 30-minute idle timeout that purges any session maps still resident. Implementation:

```python
class SessionMapRegistry:
    """Tracks live session maps; purges them on idle timeout."""

    def __init__(self, idle_timeout_seconds: int = 1800):
        self._maps: WeakValueDictionary[UUID, SessionMap] = WeakValueDictionary()
        self._idle_timeout = idle_timeout_seconds
        # Background task: periodically iterate live maps, purge if idle > timeout
```

In tests, both purge paths must be exercised. Don't skip the timeout test because "it shouldn't happen in normal operation."

## Cache partitioning

Result cache, embedding cache, plan flag cache. ALL keys namespaced by `customer_id`.

```python
class TenantPartitionedCache:
    def __init__(self, redis: Redis):
        self.redis = redis

    @staticmethod
    def _make_key(customer_id: UUID, namespace: str, key: str) -> str:
        # Hash customer_id to avoid leaking it in Redis MONITOR
        cid_hash = hashlib.sha256(customer_id.bytes).hexdigest()[:16]
        return f"{cid_hash}:{namespace}:{key}"

    async def get(self, customer_id: UUID, namespace: str, key: str) -> bytes | None:
        return await self.redis.get(self._make_key(customer_id, namespace, key))

    async def set(self, customer_id: UUID, namespace: str, key: str, value: bytes, ttl: int) -> None:
        await self.redis.set(self._make_key(customer_id, namespace, key), value, ex=ttl)
```

Per-customer cache size limits prevent one customer from monopolizing memory:

```python
async def set_with_limit(self, customer_id: UUID, namespace: str, key: str, value: bytes, ttl: int):
    # Track per-customer cache size
    size_key = self._make_key(customer_id, "_size", namespace)
    current_size = int(await self.redis.get(size_key) or 0)
    if current_size + len(value) > MAX_PER_CUSTOMER_CACHE_BYTES:
        await self._evict_oldest(customer_id, namespace, len(value))
    await self.set(customer_id, namespace, key, value, ttl)
    await self.redis.incrby(size_key, len(value))
```

## vLLM inference isolation

vLLM's prefix cache is shared across all requests within an instance. This is safe ONLY because the cached prefix contains:
- System instructions (vendor-defined, identical for all customers)
- Tier 1 country pack rules for the customer's country (vendor-defined, identical for all customers in same country)
- Subset of Tier 2 industry pack rules the customer has enabled (vendor-defined; identical content for any customer with same pack subset)

These are vendor-curated regulatory and industry rules. They are NOT customer-specific data.

What is per-request and NEVER shared:
- Customer's retrieved Tier 3 rules (their proprietary patterns)
- The actual input text
- The structured-output schema and any per-request directives

```python
def _build_prompt(customer_id: UUID, retrieved_rules: List[Rule], input_text: str) -> Prompt:
    # CACHED PREFIX (safe to share):
    prefix = f"""
    {SYSTEM_INSTRUCTIONS}

    ## Country Rules ({get_country(customer_id)})
    {format_rules(get_tier1_for_country(get_country(customer_id)))}

    ## Industry Rules ({get_industries(customer_id)})
    {format_rules(get_tier2_for_industries(get_industries(customer_id)))}
    """

    # DYNAMIC SUFFIX (per-request, customer-specific):
    suffix = f"""
    ## Customer-Specific Rules
    {format_rules(retrieved_rules)}

    ## Input
    {input_text}
    """

    return Prompt(prefix=prefix, suffix=suffix)
```

The cache key for the prefix is a hash of (country_id, set_of_industry_pack_versions). Customers with same country and same industry pack subset share the same cached prefix entry. This is safe because the content is identical.

DO NOT include customer-specific information (customer_id, customer name, custom rules) in the prefix. That would violate isolation AND defeat the cache.

## Audit log isolation

Single audit_log table with `customer_id` column. Two layers of enforcement:

### Application layer

Every audit query goes through the audit repository, which requires `customer_id`:

```python
class AuditRepository(TenantScopedRepository):
    async def query_by_customer(
        self,
        customer_id: UUID,
        start: datetime,
        end: datetime,
        filters: dict,
    ) -> list[AuditEntry]:
        # customer_id is enforced as parameter
        sql = """
        SELECT * FROM audit_log
        WHERE customer_id = $1 AND timestamp BETWEEN $2 AND $3
        """
        # ... apply filters
        return await self.db.fetch_all(sql, customer_id, start, end)
```

Dashboard endpoints derive `customer_id` from the authenticated user's customer affiliation. A user from Customer A cannot pass Customer B's customer_id; the auth layer overrides any client-supplied value.

### Database layer (defense in depth)

PostgreSQL row-level security:

```sql
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_customer_isolation ON audit_log
    FOR ALL
    USING (customer_id = current_setting('app.current_customer_id')::uuid);
```

Application sets `app.current_customer_id` per-connection. Even a SQL injection that constructs a custom query is constrained by the policy.

```python
@asynccontextmanager
async def tenant_scoped_db(customer_id: UUID):
    async with db.connection() as conn:
        await conn.execute(f"SET LOCAL app.current_customer_id = '{customer_id}'")
        yield conn
```

### Compliance officer cross-customer access

There's no such thing. A compliance officer at Customer A cannot view Customer B's audit logs by any means. Vendor support engineers debugging a customer issue use a separate read-only dashboard that requires explicit customer authorization (signed access grant from the customer) and is itself audited.

## Verification

### Per-PR CI tests

```python
# tests/integration/test_cross_tenant_isolation.py

async def test_customer_a_cannot_see_customer_b_rules(client, customer_a, customer_b):
    # Customer B creates a Tier 3 rule
    rule = await create_rule(customer_b)

    # Customer A tries to fetch it
    response = await client.get(f"/rules/{rule.id}", headers=customer_a.auth_headers)
    assert response.status_code in (403, 404)  # either is acceptable; 404 leaks no info

async def test_concurrent_requests_dont_share_session_maps(detection_pipeline, customer_a, customer_b):
    # Run requests from different customers concurrently
    results = await asyncio.gather(
        detection_pipeline.process(customer_a, "Mohamed Benali"),
        detection_pipeline.process(customer_b, "Ahmed Salem"),
    )
    # Verify each customer's session map only contains their own substitution
    # (this requires test hooks into the session map registry)

async def test_redis_cache_keys_namespaced(redis_client, customer_a, customer_b):
    await result_cache.set(customer_a.id, "namespace", "key", b"value_a", ttl=60)
    await result_cache.set(customer_b.id, "namespace", "key", b"value_b", ttl=60)

    # Same logical key, different customers → different stored values
    assert await result_cache.get(customer_a.id, "namespace", "key") == b"value_a"
    assert await result_cache.get(customer_b.id, "namespace", "key") == b"value_b"

async def test_direct_db_query_without_customer_id_raises(audit_repo):
    with pytest.raises(MissingTenantScopeError):
        await audit_repo.fetch_one("SELECT * FROM audit_log WHERE timestamp > NOW() - INTERVAL '1 day'")
```

### Annual penetration testing

Production deployments require third-party pentest before any Enterprise or Sovereign customer onboarding. Pentest report covers:
- Cross-tenant DB query attempts
- Cache namespace bypass attempts
- Session map memory inspection
- Audit log access by unauthorized principals
- Auth token manipulation to claim other customer's identity

### Incident response

Any defect that allows cross-customer data exposure is treated as a critical security incident:
- Within 4 hours: incident commander assigned, scope of leakage determined
- Within 24 hours: technical fix deployed, all affected customers identified
- Within 72 hours: notification to all affected customers per regulatory requirements (most MENA jurisdictions and GDPR mandate this)
- Within 7 days: post-mortem published internally; root cause and additional preventive controls deployed

## Hard rules

1. **Never write a query without `customer_id` filter on tenant-scoped data.** If the query is for vendor admin (cross-customer), it goes through a separate admin-scoped repository with explicit audit logging.
2. **Never persist session maps to disk.** Memory only.
3. **Never put customer-specific data in the vLLM cached prefix.** Only vendor-curated, identical-across-tenants content.
4. **Always purge session maps in `finally`.** Including error paths.
5. **Never derive `customer_id` from request body.** Derive from authenticated principal only.
6. **Never log customer-specific entity values without encryption.** Logs go through structured logger that auto-encrypts sensitive fields.
7. **Cross-tenant test failures block PRs.** No exceptions, no overrides.
