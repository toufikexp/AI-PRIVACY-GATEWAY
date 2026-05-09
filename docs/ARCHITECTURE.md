# Architecture Reference

This is the condensed architecture for ongoing reference. Full design rationale lives in the source design document (`LLM_Privacy_Gateway_Solution_Design_v2.docx`). Deep technical details on specific subsystems are in `.claude/skills/` and load on demand.

## High-level

```
┌─────────────────────┐         ┌──────────────────────┐
│  Master Cloud Plane │         │  Cloud LLM Provider  │
│  (any cloud)        │         │  (OpenAI / Anthropic)│
│                     │         │                      │
│  - Customer accts   │         └──────────▲───────────┘
│  - Plans / billing  │                    │ sanitized
│  - License service  │                    │ requests
│  - SW distribution  │                    │ only
│  - Telemetry agg    │                    │
└──────────┬──────────┘                    │
           │ pull-only                     │
           │ (signed updates,              │
           │  license validation,          │
           │  metadata-only telemetry)     │
           │                               │
           ▼                               │
┌──────────────────────────────────────────┴───────┐
│         Country Data Plane (in-country)          │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │  FastAPI Proxy + Detectors A/B + Retrieval │  │
│  │  + Merge + Substitution + Audit + Dashboard│  │
│  │  (single Python process, modular monolith) │  │
│  └────────────────────────────────────────────┘  │
│                       │                          │
│                       │ local HTTP               │
│                       ▼                          │
│  ┌──────────────────────────────────────────┐    │
│  │  vLLM Inference Server (separate process)│    │
│  │  Detector C — Qwen2.5-7B-Instruct AWQ-int4│   │
│  └──────────────────────────────────────────┘    │
│                                                  │
│  ┌──────────────┐  ┌─────────┐                   │
│  │ PostgreSQL   │  │  Redis  │                   │
│  │ + pgvector   │  │  cache  │                   │
│  │ (rules,audit)│  └─────────┘                   │
│  └──────────────┘                                │
└──────────────────────────────────────────────────┘
                         ▲
                         │ customer applications
                         │ (OpenAI-compatible API)
```

Optional: **Company Data Plane** — single-customer dedicated deployment for Sovereign and high-tier Enterprise customers. Same components, single tenant, may be air-gapped from master plane under offline license.

## The three planes

| Plane | Lives where | Holds customer content? |
|-------|-------------|--------------------------|
| Master Cloud | Any cloud (vendor-managed) | Never |
| Country Data | In-country DC, multi-tenant | Yes — all customers in that country |
| Company Data | Customer's own DC, single-tenant | Yes — single customer only |

Strict separation: master plane handles commerce only. Country plane handles all customer traffic and data. Master ↔ data plane comms are pull-only with mTLS; data plane → master is metadata-only telemetry.

## The three detectors

All three run **concurrently, not sequentially**. Total detection latency is bounded by the slowest detector.

| Detector | Tech | Latency | Catches |
|----------|------|---------|---------|
| A — Structural | Regex + checksum, libphonenumber, python-stdnum | 5–10ms | NIN, phones, IBAN, cards, tax IDs (deterministic formats) |
| B — NER | mDeBERTa-v3 ONNX int8 (CPU) | 40–80ms | Person names, organizations, locations, dates (unstructured PII) |
| C — Contextual LLM | Qwen2.5-7B AWQ-int4 via vLLM (GPU) + RAG | 150–250ms | Customer-specific entities, contextual sensitivity, novel patterns |

Worst-case detection latency: ~340ms P99. Steady state with prefix + result caches: 80–150ms P50.

Deep dive: invoke the `/detection-pipeline` skill.

## The three rule tiers

| Tier | Owner | Editable | Source |
|------|-------|----------|--------|
| Tier 1 — Country | Vendor | Immutable rules; customers can add **exceptions** (Section 5.4) | National regulators, ISO, libphonenumber, python-stdnum |
| Tier 2 — Industry | Vendor publishes; customer enables | Partial: disable, retag tier, override substitution | PCI DSS, SWIFT, ITU-T, ICD codes, GLEIF, etc. |
| Tier 3 — Customer | Customer compliance team | Full ownership | Customer-defined |

Plan tier gates editing capabilities. Country rules are ALWAYS enforced regardless of plan; plan only affects what the customer can edit.

Deep dive: invoke the `/rule-authority` skill.

## Request flow (12 steps)

1. **Ingress.** POST `/v1/chat/completions`, customer's API key authenticates.
2. **Plan & policy load.** Cached plan flags, threshold overrides, enabled rule modules.
3. **Cache check.** Hash input; on hit return cached detections, skip to step 9.
4. **Embed + detector kickoff** (concurrent). Embedding generation, Detector A, Detector B all start.
5. **Retrieval.** Hybrid pgvector + keyword + tier filter, top-K rules.
6. **Detector C.** vLLM inference with cached prefix (Tier 1 + Tier 2) + retrieved Tier 3 + input + JSON schema.
7. **Merge & validate.** Combine A/B/C; verify spans; apply customer Tier 1 exceptions; resolve overlaps by tier precedence.
8. **Substitution.** Generate synthetic replacements; build session map with component decomposition; replace in input.
9. **Forward** sanitized request to upstream provider with customer's stored upstream API key.
10. **Response handling.** Run Detector B post-response NER; apply multi-strategy reverse substitution.
11. **Audit & telemetry.** Encrypted audit record; metric updates; aggregate-only telemetry to master.
12. **Cache & return** de-sanitized response.

## Reverse substitution

The challenge: LLMs rephrase, abbreviate, change gender/plurality, drop honorifics. Naive reverse mapping fails.

Three coordinated mechanisms:

1. **Component decomposition at substitution time.** Register full form, first/last name alone, with honorifics in multiple languages.
2. **Post-response NER validation.** Run Detector B on the response; classify entities as direct match, component match, or novel (LLM-generated).
3. **Contextual disambiguation.** Component matches reverse only if context supports it (no other plausible referent, no semantic conflict).

Acknowledged failure mode: LLM-generated novel attributes about substituted entities propagate to real entities post-reverse. Mitigation is audit flagging, not architectural prevention.

## Substitution ≠ placeholders

Synthetic values ("Karim Hadji" replacing "Mohamed Benali"), not placeholder tags (`[[PERSON_1]]`). Synthetic preserves LLM reasoning quality (cultural context, gender inference, social register). Placeholders degrade output significantly, especially for Arabic and French.

## Multi-tenant isolation

Country data plane serves multiple customers simultaneously. Cross-customer leakage is a fatal product failure.

- Per-request statelessness; session maps memory-only, AES-256-GCM encrypted, purged on response or 30-min idle.
- `customer_id` propagated through every DB query, cache key, log line, audit record. Application-layer enforcement; queries without `customer_id` raise an exception.
- Cache partitioning by `customer_id` namespace.
- vLLM prefix cache shared (Tier 1 + Tier 2 are same per country, regulatory and vendor-curated). Per-request retrieved Tier 3 + input never shared.
- Audit log queries scoped by requesting user's customer affiliation.

Deep dive: invoke the `/multi-tenant-isolation` skill.

## Audit and tamper-evidence

- Audit records: structured fields, sensitive fields encrypted with AES-256-GCM. Key storage tier-dependent (sealed file → Vault → HSM for Sovereign).
- Hash chain: each record contains content hash + previous record's hash + HMAC signature with separate tamper-evidence key.
- External anchoring: chain head hash periodically anchored to master plane (standard tiers) or RFC 3161 timestamping authority (Sovereign).
- Verification: recompute chain end-to-end, validate HMAC signatures, compare to anchored hashes.

Deep dive: invoke the `/audit-and-security` skill.

## Failure modes (configurable per customer)

| Mode | Detector failure | Upstream LLM down | Master plane unreachable |
|------|------------------|-------------------|--------------------------|
| Strict (default) | Return 503 | Return 502 | 24h grace, then degraded 7d, then stop |
| Audit-only | Forward unsanitized + alert | Same as strict | Same |
| Fallback | Route to local model | Same | Same |

Sovereign tier: master plane is never required at runtime. Operates fully offline under signed license valid for contract period. Audit log writes always block; if audit DB is unhealthy, requests fail closed.

## Performance targets

| Operation | P50 | P99 |
|-----------|-----|-----|
| Detection (cache hit) | <10ms | <25ms |
| Detection (cold path, all detectors) | 180ms | 340ms |
| Total proxy overhead | 200ms | 400ms |
| Sustained throughput single L4 GPU | 25 RPS | Peak 50 RPS for ≤60s |
| Sustained throughput single A10 GPU | 50 RPS | Peak 80 RPS for ≤60s |

Tier-based queue prioritization under saturation: Sovereign → Enterprise → Professional → Starter.

## Tech choices and why

- **PostgreSQL + pgvector** — single mature DB engine, vector search built in, no separate vector DB to operate.
- **vLLM** — best open-source inference server in 2026; continuous batching, prefix caching, OpenAI-compatible API.
- **Qwen2.5-7B AWQ-int4** — best Arabic capability among open 7B models; fits on single L4/A10.
- **mDeBERTa-v3 ONNX int8** — best accuracy-per-size for AR/FR/EN; CPU-feasible.
- **FastAPI + Jinja2 + HTMX for dashboard** — eliminates separate frontend build pipeline; minimal complexity for solo dev.
- **Modular monolith** — in-process function calls between modules; only vLLM is split out (different scaling profile, different GPU dependencies).

## What is NOT in the architecture

- No Kubernetes for MVP. Docker Compose or systemd is enough for design partners.
- No microservices beyond vLLM. Don't break the monolith further until there's measured contention.
- No separate frontend SPA. HTMX is enough.
- No message queue. Synchronous request/response.
- No service mesh. mTLS to master plane is the only inter-service auth.
