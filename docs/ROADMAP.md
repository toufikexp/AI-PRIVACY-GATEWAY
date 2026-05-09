# Delivery Roadmap

Each phase has explicit **verification criteria**. Per Anthropic best practices, no phase is "done" without verification. Code that hasn't been verified against tests does not ship.

## Phase 0 — Validation Experiment (Weeks 1–4)

**Goal:** Validate the central architectural claim before committing to full build.

The 95%+ accuracy claim is the product's core value proposition. If the three-detector ensemble doesn't reach this on real MENA data, the architecture changes — not the marketing.

### Tasks

- Set up minimal harness: structural validators (Algeria pack only), Detector B (mDeBERTa baseline, no fine-tuning yet), Detector C (Qwen2.5-7B with naive prompt, no RAG yet).
- Collect 50–100 real or realistic MENA enterprise text samples. If no design partner yet, use synthetic data generated to match real distribution (banking customer support transcripts, telecom case notes, government correspondence) plus public datasets.
- Build labeled reference corpus per `docs/ARCHITECTURE.md` §4.6 methodology: two annotators, Cohen's kappa ≥0.85 for Tier 1, disagreement adjudication.
- Measure: per-detector P/R/F1, ensemble P/R/F1, ensemble lift over best single detector.

### Verification

- [ ] Reference corpus exists, version-controlled, with annotation metadata
- [ ] All three detectors run on the corpus and produce structured output
- [ ] Metrics computed and reported: per-detector and ensemble
- [ ] Ensemble lift ≥3 F1 points over best single detector
- [ ] If ensemble F1 <90% → STOP. Identify weakest detector. Fix before proceeding.
- [ ] If ensemble F1 ≥90% → proceed to Phase 1. Document gap to 95% target as Phase 1 work.

### Decision gate

This is a **GO / NO-GO gate**. Do not proceed to Phase 1 if Phase 0 doesn't validate the core claim. The architecture's defensibility depends on this measurement existing.

---

## Phase 1 — Foundation (Weeks 5–8)

**Goal:** Operating skeleton with one country pack and the OpenAI-compatible API.

### Tasks

- Master plane: customer accounts, plan management API, license validation endpoint, online + offline (Sovereign) flows.
- Data plane proxy: FastAPI app, OpenAI-compatible `/v1/chat/completions` and `/v1/completions` endpoints, customer authentication via API key, request lifecycle management.
- PostgreSQL schema: `rules`, `rule_modules`, `rule_audit_log`, `rule_exceptions`, `customer_config`, `audit_log`.
- Algeria country pack (Tier 1): NIN, NIF, RIB, phone formats, geographic dictionary. Source: ISO standards + python-stdnum + national authority documentation.
- Audit log infrastructure: encrypted writer, hash chain construction.
- CI pipeline: pytest, mypy strict, ruff, container builds.

### Verification

- [ ] `pytest tests/unit/` passes with ≥80% coverage on new code
- [ ] `mypy --strict src/` clean
- [ ] `ruff check` clean
- [ ] Integration test: end-to-end request through the proxy with passthrough to OpenAI succeeds
- [ ] Algeria country pack: every entity type has at least 5 unit tests covering valid, invalid checksum, edge cases
- [ ] CI test `test_no_content_in_telemetry.py` passes (master plane isolation invariant)
- [ ] Audit log hash chain verification script exists and validates a fresh log

---

## Phase 2 — Three-Detector Ensemble (Weeks 9–14)

**Goal:** Full detection pipeline with all three detectors, retrieval, and merge.

### Tasks

- Detector A: structural validators with full Algeria coverage; libphonenumber + python-stdnum integration; context-window keyword adjustment.
- Detector B: fine-tune mDeBERTa-v3 on combined AR/FR/EN NER datasets (AraBench/AQMAR + WikiNeural + CoNLL/OntoNotes); ONNX int8 export; CPU inference service.
- Detector C: vLLM deployment script; Qwen2.5-7B AWQ-int4 model loading; RAG retrieval service (pgvector hybrid); structured-output prompt with JSON schema; tier-aware prefix caching.
- Merge engine: span coverage validation, overlap resolution, tier precedence, exception application, confidence aggregation, threshold filtering.
- Substitution engine: synthetic value dictionaries per entity type (must be culturally consistent), session map with component decomposition, AES-256-GCM encryption.
- Reverse substitution pipeline: post-response NER, contextual disambiguation, novel-entity logging.

### Verification

- [ ] Each detector has unit tests covering: success cases, malformed input, error paths
- [ ] Detection pipeline end-to-end test: input → all three detectors → merge → substitution → reverse substitution → output
- [ ] Hallucination protection test: feed Detector C an input designed to produce hallucinated spans; verify span validation drops them
- [ ] Reference corpus eval (Phase 0 corpus): ensemble P/R/F1 ≥ Phase 0 baseline + improvements claimed
- [ ] Latency profiling: P50 and P99 measured under representative load; documented; meets targets in `docs/ARCHITECTURE.md` §7.1
- [ ] Multi-tenant isolation test: two customers' requests processed concurrently; verify session maps don't cross
- [ ] Detector failure modes test: kill vLLM mid-request, verify strict mode returns 503 and audit-only mode continues with A+B
- [ ] Reverse substitution accuracy: ≥98% direct + component matches; novel-entity flagging works

---

## Phase 3 — Operations & Surface (Weeks 15–18)

**Goal:** Production-ready deployment with dashboard, audit, and operational tooling.

### Tasks

- Dashboard: live activity view, detection statistics, rule management UI (Tier 3 + exception authoring), audit log viewer, customer configuration. Jinja2 + HTMX, no separate React build.
- Rule authoring sandbox: paste sample text, see what current pattern matches, before activating.
- Result cache (Redis), embedding cache, hot reload of rule changes.
- Failure modes implementation: strict, audit-only, fallback configurable per customer per environment.
- Banking industry pack (Tier 2): cards (Luhn), SWIFT BIC, IBAN structures, transaction reference patterns, loan IDs.
- Active-passive HA configuration via keepalived (production deployments).
- Observability: Prometheus metrics, structured JSON logging via structlog, OpenTelemetry traces.
- Multi-tenant isolation hardening: per-customer cache namespaces, RLS policies on audit table, automated CI test for cross-tenant queries.
- Audit tamper-evidence: hash chain external anchoring (master plane for standard, RFC 3161 timestamping for Sovereign).
- Key management: sealed file (Starter/Pro), Vault adapter (Enterprise), HSM via PKCS#11 (Sovereign).

### Verification

- [ ] Dashboard E2E test: log in, view detections, create Tier 3 rule, create Tier 1 exception, verify changes apply within 60s
- [ ] Rule authoring sandbox test: invalid regex rejected with clear error; valid regex shows live match results
- [ ] Failure mode tests: each mode (strict/audit-only/fallback) has integration test for each failure scenario
- [ ] Banking pack: every entity type has ≥5 unit tests; reference corpus eval passes for banking-specific samples
- [ ] HA failover test: kill active node mid-request, verify keepalived failover < 10s, no audit log loss
- [ ] Cross-tenant isolation test: attempt direct DB query without `customer_id`, verify exception raised
- [ ] Audit chain verification: insert 1000 records, run verifier, verify chain valid; modify one record, verify detection
- [ ] HSM integration test (using SoftHSM in CI): encryption and decryption operations succeed; key never extracted
- [ ] Load test: sustained 30 RPS on single L4 GPU for 30 minutes; P99 latency stays within target
- [ ] Backpressure test: send 100 RPS to a 50-RPS-capable instance; verify Starter tier 429s before Enterprise tier latency degrades

---

## Phase 4 — Design Partner Deployment (Weeks 19–22)

**Goal:** First real customer in production. Validation of architecture against real traffic.

### Tasks

- Design partner deployment in their environment (in-country DC, customer DC, or sovereign cloud as appropriate).
- Customer onboarding workshop: rule authoring training, exception governance, dashboard walkthrough.
- Initial customer-specific Tier 3 rules and exceptions (vendor-led for first 90 days, per PRD).
- Compliance report templates: Algeria 18-07, basic UAE PDPL, basic Saudi PDPL.
- Telemetry pipeline to master plane (validated, monitored, content-free).
- Runbooks: deployment, upgrade, rule pack updates, exception governance, DR/restore, incident response.
- Validation checkpoint: measure ensemble accuracy on partner's real traffic; tune thresholds; document gaps.

### Verification

- [ ] Partner deployment passes their security review
- [ ] First 100 real production requests succeed end-to-end
- [ ] Partner-specific reference corpus eval: ≥95% precision, ≥95% recall (or document specific gaps and remediation plan)
- [ ] Partner compliance officer can author Tier 3 rules and exceptions without vendor help (after onboarding workshop)
- [ ] At least one compliance report generated and reviewed by partner's compliance team
- [ ] Telemetry data flowing to master plane; manual audit of last 24h confirms zero customer content leakage
- [ ] Incident runbook tested via tabletop exercise

---

## Post-MVP — Expansion (Months 6–12)

Items deferred from MVP. Each is independently scoped; pick based on customer demand:

- Additional country packs: UAE, Saudi Arabia, Morocco, Tunisia, Egypt
- Additional industry packs: Telecom, Healthcare, Government, Insurance
- Multi-provider routing: Anthropic Claude, Google Gemini, Mistral, locally-hosted Ollama/vLLM
- **Streaming response support** with chunked-buffer reverse substitution (see `docs/ARCHITECTURE.md` §8.4)
- Multi-turn conversation session management
- Cost governance: dashboards, departmental budgets, waste detection
- Advanced policy engine: topic blocking, output guardrails, approval workflows
- Document/PDF processing pipeline

## Year 2+ — Platform Maturity

- Cross-customer detection improvement (anonymized) feeding industry packs
- K-anonymity and re-identification risk scoring
- Self-serve onboarding for mid-market
- Image and multimodal sanitization
- API marketplace for community-contributed rules
- SOC 2 Type II, regional certifications
- Automated rule/exception suggestion from observed patterns

## Hard rules for every phase

1. **Phase verification gates are not optional.** A phase is not "done" until all checkboxes pass. No skipping ahead.
2. **Performance regressions are blockers.** Every PR runs the latency profile; >10% regression on any P50/P99 target blocks merge.
3. **Accuracy regressions are blockers.** Every PR that touches detection runs the reference corpus eval; >1 F1 point regression on any tier blocks merge.
4. **Multi-tenant isolation tests run on every PR.** Cross-tenant leakage in any test is a critical incident.
5. **No customer content in any test fixture committed to git.** Use synthetic data only. Real data lives in encrypted dev environments only.
