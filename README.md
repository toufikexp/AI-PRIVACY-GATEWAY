# LLM Privacy Gateway

Privacy-preserving proxy between MENA enterprises and cloud LLM providers
(OpenAI, Anthropic, Google). Drop-in OpenAI-compatible API: detects and
substitutes sensitive entities before forwarding, reverses on response.
Original data never leaves the country boundary.

See [`docs/PRD.md`](docs/PRD.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
and [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full design.

## Status

Pre-Phase-2. The Phase 1 foundation is in place:

- FastAPI proxy with OpenAI-compatible `/v1/chat/completions`
- Multi-tenant scope (`CustomerContext`, `MissingTenantScopeError`)
- Detector A — Algeria Tier-1 structural validators (NIN, NIF, RIB, phone)
- AES-256-GCM session map with idle-purge sweep
- Tamper-evident audit writer (hash chain + HMAC + AES-GCM payload)
- Master-plane telemetry with whitelisted, content-free fields
- Unit tests (48), `mypy --strict` clean, `ruff` clean

Detectors B (mDeBERTa NER) and C (Qwen2.5 via vLLM), retrieval, merge,
substitution, dashboard, and the PostgreSQL schema land in Phase 2.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

pytest tests/unit -x          # unit tests
ruff check src tests          # lint
ruff format --check src tests # format check
mypy -p src                   # strict typecheck

# Run the proxy in dev (in-memory backends; no Postgres required)
uvicorn src.proxy.app:create_app --factory --reload --port 8080
```

Then:

```bash
curl -s http://localhost:8080/healthz
# Auth keys are registered programmatically via CustomerDirectory at MVP;
# see tests/unit/test_proxy_endpoint.py for the seed pattern.
```

## Project layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`CLAUDE.md`](CLAUDE.md) for development conventions and hard rules.
