# LLM Privacy Gateway

Privacy-preserving proxy between MENA enterprises and cloud LLM providers (OpenAI, Anthropic, Google). OpenAI-compatible API. Detects and substitutes sensitive entities before forwarding; reverses on response. Full architecture in `docs/ARCHITECTURE.md`.

## Stack

- **Language:** Python 3.11+ (FastAPI, async/await throughout)
- **Inference:** vLLM (separate process) hosting Qwen2.5-7B-Instruct AWQ-int4
- **NER:** mDeBERTa-v3-base ONNX int8 on CPU
- **Embeddings:** multilingual-e5-base ONNX on CPU
- **DB:** PostgreSQL 16 + pgvector (rules, audit, config)
- **Cache:** Redis (in-memory dict for single-node dev)
- **Dashboard:** FastAPI + Jinja2 + HTMX (same process as proxy, no separate frontend build)

## Architecture (one paragraph)

Three-plane: master cloud (commerce only, no customer data), country data plane (multi-tenant, processes traffic), optional company data plane (single-tenant). Within data plane: modular monolith for proxy + detectors A & B + retrieval + merge + substitution + dashboard, with vLLM as a separate process over local HTTP. Three-detector ensemble runs in parallel: structural validators (regex + checksum), multilingual NER, contextual LLM with RAG retrieval over a layered rule base. Three-tier rule authority: Tier 1 country/regulatory (immutable), Tier 2 industry (configurable), Tier 3 customer (full ownership). Synthetic substitution preserves LLM reasoning; reverse substitution uses component decomposition + post-response NER + contextual disambiguation.

## Code style

- Type hints required on all function signatures (`mypy --strict` passes)
- Async by default for I/O; never block the event loop
- `ruff` for linting and formatting (line length 100)
- Pydantic v2 for all request/response/config models
- Dependency injection via FastAPI `Depends`, no globals except for startup-initialized services
- Logging: structured JSON via `structlog`, never `print`
- Tests: `pytest`, async tests via `pytest-asyncio`, factories via `factory_boy`

## Workflow

- Run tests before claiming work is done: `pytest -x --ff`
- Type check before commit: `mypy src/`
- Format before commit: `ruff format src/ tests/`
- Lint before commit: `ruff check src/ tests/`
- Run a single test by node id during development; full suite is slow with vLLM startup
- vLLM is heavy to start; tests that don't need it MUST mock it via `tests/fixtures/llm_mock.py`

## Hard rules (do not violate)

1. **No customer content ever flows to the master plane.** Telemetry is structured numeric/categorical only. CI test `test_no_content_in_telemetry.py` enforces this; never bypass it.
2. **Original PII never persisted to disk in plaintext.** Session maps are memory-only, AES-256-GCM encrypted, purged on response.
3. **Every DB query carries `customer_id`.** Multi-tenant isolation is enforced at application layer; queries without `customer_id` raise `MissingTenantScopeError`.
4. **Tier 1 rules are immutable from customer-facing APIs.** Customer overrides go through the exception mechanism (`rule_exceptions` table), never by mutating Tier 1 rules.
5. **Audit log writes are blocking; if the audit DB is unhealthy, requests fail closed.** Zero data loss invariant — never silently drop audit records.
6. **vLLM detector outputs are span-validated against the original input** before being trusted. Hallucinated spans are dropped.

## Project layout

```
src/
  proxy/              # FastAPI gateway, OpenAI-compatible endpoints
  detectors/
    structural.py     # Detector A — regex + checksum
    ner.py            # Detector B — mDeBERTa ONNX
    contextual.py     # Detector C — vLLM client + RAG
  retrieval/          # pgvector hybrid retrieval
  merge/              # ensemble merge + span validation + exception application
  substitution/       # synthetic generation, session map, reverse substitution
  rules/              # rule storage, lifecycle, exception management
  audit/              # encrypted audit writer, hash-chain tamper-evidence
  dashboard/          # Jinja2 templates + HTMX endpoints
  master_client/      # data plane → master plane (license, telemetry)
  config/             # settings, plan flags
tests/
  unit/
  integration/
  fixtures/
docs/
  PRD.md              # what and why
  ARCHITECTURE.md     # condensed architecture reference
  ROADMAP.md          # phased delivery, verification criteria
.claude/
  skills/             # load-on-demand domain knowledge
```

## Where to look for context

- **What we're building / why:** `@docs/PRD.md`
- **How it works:** `@docs/ARCHITECTURE.md`
- **What to build next:** `@docs/ROADMAP.md`
- **Detection pipeline internals:** invoke `/detection-pipeline` skill
- **Rule tier model:** invoke `/rule-authority` skill
- **Multi-tenant isolation:** invoke `/multi-tenant-isolation` skill
- **Audit and security details:** invoke `/audit-and-security` skill

## Common gotchas

- vLLM startup takes 30–60s. Don't restart it in tight test loops; mock the client.
- pgvector HNSW index needs explicit `ANALYZE` after bulk rule loads or retrieval quality drops silently.
- Arabic text in DB requires `ENCODING = 'UTF8'` and `LC_COLLATE = 'C'` on the rules schema; `en_US` collation breaks Arabic equality.
- Session map purge runs on response delivery AND on 30-min idle timeout. Both paths must purge; tests exist for both.
- Plan flags poll the master plane every 5 minutes; in dev, set `MASTER_PLANE_MOCK=1` to skip and use `dev_plan_flags.json`.

## Verification

Before claiming a feature complete:
1. Unit tests pass: `pytest tests/unit/ -x`
2. Integration tests pass: `pytest tests/integration/ -x` (requires running PostgreSQL + Redis + mock vLLM)
3. `mypy src/` clean
4. `ruff check src/ tests/` clean
5. If touching detection: run `scripts/eval_corpus.py` and confirm no F1 regression on the reference corpus

Verification is non-negotiable. Code that hasn't been verified against tests does not ship.
