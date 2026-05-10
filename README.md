# LLM Privacy Gateway

Privacy-preserving proxy between MENA enterprises and cloud LLM providers
(OpenAI, Anthropic, Google). Drop-in OpenAI-compatible API: detects and
substitutes sensitive entities before forwarding, reverses on response.
Original data never leaves the country boundary.

See [`docs/PRD.md`](docs/PRD.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

## What's in the box

- **OpenAI-compatible** `POST /v1/chat/completions` proxy (FastAPI)
- **Three-detector ensemble** running concurrently:
  - Detector A — structural validators (Algeria pack today: NIN/NIF/RIB/phone)
  - Detector B — multilingual NER (mDeBERTa ONNX backend; pure-Python stub for dev)
  - Detector C — contextual LLM (vLLM HTTP backend; pure-Python stub for dev)
- **pgvector hybrid retrieval** over a layered, three-tier rule base
- **Merge engine** with span validation, tier precedence, customer exceptions, threshold filtering
- **Synthetic substitution** (not placeholders) with component decomposition for robust reverse
- **AES-256-GCM session map** purged on response or 30-min idle
- **Tamper-evident audit log** (hash chain + HMAC + AES-GCM payloads); Postgres or in-memory backend
- **Dashboard** (Jinja2 + HTMX): activity, rules, exceptions, audit
- **Master-plane client** with content-free telemetry whitelist
- **Docker compose** stack (proxy + Postgres + Redis); single binary via `docker build`

## Configuration

All configuration is environment-driven via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
Copy [`.env.example`](.env.example) to `.env` for a development run; every
setting has the prefix `GATEWAY_`.

Backend choice for heavy components is a toggle:

| Setting | Values | Notes |
|---|---|---|
| `GATEWAY_NER_BACKEND` | `stub` (default) / `onnx` | `onnx` requires `pip install -e '.[ner]'` and the model paths |
| `GATEWAY_VLLM_BACKEND` | `stub` (default) / `http` | `http` requires `GATEWAY_VLLM_URL` |
| `GATEWAY_AUDIT_STORE_BACKEND` | `memory` (default) / `postgres` | `postgres` requires `GATEWAY_POSTGRES_DSN` |
| `GATEWAY_RULE_STORE_BACKEND` | `memory` (default) / `postgres` | (Postgres backend ships in `src/rules/postgres_backend.py`) |
| `GATEWAY_MASTER_PLANE_MOCK` | `true` (default) / `false` | `false` requires `GATEWAY_MASTER_PLANE_URL` |
| `GATEWAY_DEFAULT_FAILURE_MODE` | `strict` / `audit_only` / `fallback` | per ARCHITECTURE §6.2 |

Crypto keys (`GATEWAY_SESSION_MAP_KEY`, `GATEWAY_AUDIT_ENCRYPTION_KEY`,
`GATEWAY_AUDIT_HMAC_KEY`) are 32-byte hex strings. The all-zero defaults
are a deliberately useless placeholder so a missing key in production
surfaces immediately. Real deployments load keys from a sealed file,
HashiCorp Vault, or HSM (PKCS#11) per the audit-and-security skill.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env

# Generate real keys for dev
python -c "import os;print('GATEWAY_SESSION_MAP_KEY='+os.urandom(32).hex())" >> .env
python -c "import os;print('GATEWAY_AUDIT_ENCRYPTION_KEY='+os.urandom(32).hex())" >> .env
python -c "import os;print('GATEWAY_AUDIT_HMAC_KEY='+os.urandom(32).hex())" >> .env

uvicorn src.proxy.app:create_app --factory --reload --port 8080
```

Or via Docker:

```bash
docker compose up --build
```

## Test

```bash
pytest tests/unit -x       # 74 tests
ruff check src tests
ruff format --check src tests
mypy -p src
```

Continuous integration runs the same gates on every PR — see
`.github/workflows/ci.yml`.

## Try it

```bash
# Health
curl -s localhost:8080/healthz

# Chat (you must register an API key first; see tests/unit/test_proxy_endpoint.py
# for the seed pattern, or wire the customer directory to Postgres in production).
curl -s localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-test-1" -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Mon RIB est 00400123456789012375"}]}'

# Dashboard (same Bearer token)
curl -s localhost:8080/dashboard/ -H "Authorization: Bearer sk-test-1"
```

## Layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design and
[`CLAUDE.md`](CLAUDE.md) for development conventions and hard rules.
