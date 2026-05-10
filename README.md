# LLM Privacy Gateway

Privacy-preserving proxy between MENA enterprises and cloud LLM providers
(OpenAI, Anthropic, Google). Drop-in OpenAI-compatible API: detects and
substitutes sensitive entities before forwarding, reverses on response.
Original data never leaves the country boundary.

Architecture: **federated** — a country **data plane** processes traffic
in-country, a **master cloud plane** handles SaaS commerce (customer
accounts, plan flags, license issuance, content-free telemetry).

See [`docs/PRD.md`](docs/PRD.md) and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

---

## What's in the box

### Country data plane (`src/proxy/`, `src/detectors/`, `src/audit/`, ...)

- **OpenAI-compatible API** at `POST /v1/chat/completions`
- **Three-detector ensemble** running concurrently:
  - **Detector A** — structural Algeria pack: NIN, NIF, NIS, NSS, RIB, RIP, IBAN-DZ, Luhn cards, passport, driving licence, vehicle plate, CHIFA, phone, email, IP
  - **Detector B** — multilingual NER. Pluggable: `stub` (dev) / `onnx` / `transformers` (HuggingFace, default `Davlan/distilbert-base-multilingual-cased-ner-hrl`)
  - **Detector C** — vLLM contextual LLM with RAG + tier-aware prefix caching. Pluggable: `stub` / `http`
- **pgvector hybrid retrieval** over the three-tier rule base
- **Merge engine** with span validation, exception suppression, confidence aggregation, tier precedence
- **Synthetic substitution** with component decomposition (AR/FR/EN honorifics) for robust reverse substitution
- **AES-256-GCM session map**, idle-purge sweep
- **Tamper-evident audit** (hash chain + HMAC + AES-GCM payload), Postgres or in-memory
- **Postgres-backed customer auth** with bcrypt-hashed keys + AES-GCM encryption of upstream provider keys
- **Plan-tier enforcement** (`src/plans.py`)
- **Crypto key resolution** from env or HashiCorp Vault KV v2
- **Prometheus `/metrics`** + optional OpenTelemetry tracing
- **License gate** at startup (`GATEWAY_LICENSE_REQUIRED=true` fails closed)
- **Dashboard** (Jinja2 + HTMX) at `/dashboard/`: activity, rules, exceptions, audit

### Master cloud plane (`src/master_plane/`)

- Customer onboarding and plan management
- RSA-signed offline license issuance (Sovereign tier)
- Plan-flag polling endpoint for data planes
- Content-free telemetry intake
- Admin CLI: `python -m src.master_plane.admin {keygen|create-customer|issue-license|init-db}`

### Operations

- Two-plane `docker-compose.yml` (data + master + two Postgres + Redis)
- `Dockerfile` (data plane) and `Dockerfile.master` (master plane)
- `scripts/run_vllm.sh` — self-host Detector C on a GPU host
- `scripts/eval_corpus.py` + `scripts/build_synthetic_corpus.py` — reference-corpus eval harness
- `deploy/keepalived.conf.example` — active-passive HA
- GitHub Actions CI: ruff + ruff format + mypy strict + 150 unit tests

---

## Configuration

All settings flow through [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
Copy [`.env.example`](.env.example) to `.env`. Every variable is prefixed
`GATEWAY_` (data plane) or `MASTER_` (master plane).

Key backend toggles:

| Setting | Values | Notes |
|---|---|---|
| `GATEWAY_NER_BACKEND` | `stub` (default) / `transformers` / `onnx` | `transformers`/`onnx` need `pip install -e '.[ner]'` |
| `GATEWAY_VLLM_BACKEND` | `stub` (default) / `http` | `http` needs `GATEWAY_VLLM_URL` |
| `GATEWAY_KEY_STORE_BACKEND` | `env` (default) / `vault` | `vault` needs `GATEWAY_VAULT_ADDR` + `GATEWAY_VAULT_TOKEN` |
| `GATEWAY_AUDIT_STORE_BACKEND` | `memory` (default) / `postgres` | `postgres` needs `GATEWAY_POSTGRES_DSN` |
| `GATEWAY_RULE_STORE_BACKEND` | `memory` / `postgres` | same DSN |
| `GATEWAY_CUSTOMER_STORE_BACKEND` | `memory` / `postgres` | same DSN |
| `GATEWAY_MASTER_PLANE_MOCK` | `true` (default) / `false` | `false` needs `GATEWAY_MASTER_PLANE_URL` |
| `GATEWAY_LICENSE_REQUIRED` | `false` (default) / `true` | `true` fails startup without a valid signed token |
| `GATEWAY_DEFAULT_FAILURE_MODE` | `strict` / `audit_only` / `fallback` | per ARCHITECTURE §6.2 |

---

## Run the full stack

```bash
docker compose up --build
```

Services come up:
- `master`   on http://localhost:9090
- `proxy`    on http://localhost:8080
- `postgres` (data plane) on 5432
- `master-postgres` on 5433
- `redis` on 6379

Onboard a customer via the master plane admin API:

```bash
# Generate license keys (one time)
python -m src.master_plane.admin keygen --out ./keys/master

# Create a customer
python -m src.master_plane.admin create-customer \
  --master-url http://localhost:9090 \
  --id cust-acme --company "Acme Bank" --country DZ --plan enterprise

# Issue a license (used by the data plane on startup if GATEWAY_LICENSE_REQUIRED=true)
python -m src.master_plane.admin issue-license \
  --master-url http://localhost:9090 \
  --id cust-acme --days 365
```

Provision a customer API key on the data plane (bcrypt-hashed in Postgres,
upstream LLM key encrypted at rest):

```bash
docker compose exec proxy python -m src.proxy.customer_admin create \
  --customer cust-acme \
  --country DZ \
  --plan enterprise \
  --upstream-key sk-proj-YOUR-OPENAI-KEY
# Prints the gateway API key once. Store it; it is not retrievable later.
```

Then call the gateway:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-XXX-from-customer-admin-create" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Mon RIB est 00400123456789012375"}]}'
```

---

## Run for development (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

pytest tests/unit -x       # 150 tests
ruff check src tests scripts
ruff format --check src tests scripts
mypy -p src

uvicorn src.proxy.app:create_app   --factory --reload --port 8080  # data plane
uvicorn src.master_plane.app:create_app --factory --reload --port 9090  # master plane
```

---

## Self-host Detector C (vLLM + GPU)

```bash
./scripts/run_vllm.sh   # spins up vLLM serving Qwen2.5-7B-AWQ
# then on the proxy:
export GATEWAY_VLLM_BACKEND=http
export GATEWAY_VLLM_URL=http://localhost:8000/v1
```

Without a GPU, leave `GATEWAY_VLLM_BACKEND=stub`. Detector A + B remain
fully active.

---

## Layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system design and
[`CLAUDE.md`](CLAUDE.md) for development conventions and hard rules.
