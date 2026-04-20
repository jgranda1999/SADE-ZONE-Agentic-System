# SADE Agentic Orchestration System

A safety-critical, evidence-driven system for automatically determining whether a Drone | Pilot | Organization (DPO) trio may enter a controlled SADE Zone. The system uses real-time environmental conditions, historical reputation data, and formal evidence attestations to make deterministic, auditable admission decisions.

## Overview

The Safety-Aware Drone Ecosystem (SADE) admission system replaces manual authorization with a **deterministic, evidence-driven, auditable agentic workflow**. The system operates in two phases:

- **Phase 1 - Fast Path**: Gathers environment and reputation data, makes immediate decisions when safe
- **Phase 2 - Evidence Escalation**: Triggers SafeCert attestation workflow when additional evidence or mitigation is required

## Architecture

The system uses a multi-agent architecture with a single decision authority:

### Orchestrator Agent (Decision Authority)
- Receives entry requests
- Delegates to sub-agents for data retrieval
- Performs pair-wise analysis (Request × Environment, Request × Reputation, Environment × Reputation)
- Generates evidence requirements when needed
- Issues the **only** entry decision

### Sub-Agents (Advisory Only)

#### Environment Agent
- **Purpose**: Summarize external operating conditions from the request payload
- **Data**: Weather (wind, gusts, precipitation, visibility), manufacturer flight constraints (MFC), and related risk signals
- **Exposure**: `environment_agent` — orchestrator calls this sub-agent as a tool (see [`src/sade/main.py`](src/sade/main.py))

#### Reputation Agent
- **Purpose**: Interpret historical trust and reliability signals from provided records
- **Data**: Pilot, organization, and drone reputation; incident history (including unresolved incidents)
- **Exposure**: `reputation_agent` — orchestrator calls this sub-agent as a tool (see [`src/sade/main.py`](src/sade/main.py))

#### Claims Agent
- **Purpose**: Verify required actions against DPO claims and follow-up records
- **Data**: Checks satisfaction of required actions, resolves incident prefixes, tracks unresolved incidents; may emit `evidence_requirement_spec` when not satisfied
- **Exposure**: `claims_agent` — orchestrator calls this sub-agent as a tool (see [`src/sade/main.py`](src/sade/main.py))
- **Note**: Invoked when the orchestrator state machine requires claims verification (see [`src/sade/prompts/orchestrator_prompt.md`](src/sade/prompts/orchestrator_prompt.md))

#### SafeCert / attestation (future hook)

Entry JSON may include fields such as `safecert_pin` or `evidence_required` for re-evaluation flows. A dedicated SafeCert **Action Required** sub-agent (and companion attestation tools) is **not** wired into the current orchestrator graph in [`src/sade/main.py`](src/sade/main.py); the orchestrator plus claims path covers the MVP decision contract.

## Entry Request Model

The orchestrator consumes a **single merged JSON object** per evaluation. Typical fields include:

- **Identifiers**: `evaluation_id`, `evaluation_series_id` (UUIDs), `entry_request_kind`
- **Zone / pilot / UAV**: nested `zone`, `pilot`, `uav` (e.g. `sade_zone_id`, `pilot_id`, `organization_id`, `drone_id`)
- **Operation**: `payload`, `requested_entry_time`, `requested_exit_time`, `request_type` (derived from `entry_request_kind` when omitted)
- **Signals**: `weather_forecast`, `uav_model`
- **Records**: `reputation_records`, `attestation_claims`, `entry_request_history` (lists; may be empty)

Request geometry (regions, routes) lives inside the JSON as provided to the model; see [`src/sade/resources/entry-requests-files/entry-requests/`](src/sade/resources/entry-requests-files/entry-requests/) for worked CLI fixtures (API-oriented samples live under [`src/sade/resources/entry-requests-api/`](src/sade/resources/entry-requests-api/)).

## Decision Outputs

The orchestrator emits exactly one internal decision (`decision.type` in JSON):

- `APPROVED`: Entry allowed without constraints
- `APPROVED-CONSTRAINTS`: Entry allowed with enforceable operational limits
- `ACTION-REQUIRED`: Additional evidence or certification required (may include `evidence_requirement_spec`)
- `DENIED`: Fundamentally unsafe or policy-forbidden (includes `denial_code` where applicable)

The HTTP ingest and [`src/sade/evaluation_api_response.py`](src/sade/evaluation_api_response.py) map these to the external evaluation API using **underscores** in `result.decision` (for example `APPROVED_CONSTRAINTS`, `ACTION_REQUIRED`). The human-readable narrative is carried in `decision.explanation` internally and exposed as `result.reason` in the API payload.

## Evidence Grammar

Evidence is expressed using a formal grammar with four fixed categories:

- **CERTIFICATION**: Regulatory certifications (e.g., PART_107, BVLOS)
- **CAPABILITY**: Operational capabilities (e.g., NIGHT_FLIGHT, PAYLOAD limits)
- **ENVIRONMENT**: Environmental mitigations (e.g., MAX_WIND_GUST)
- **INTERFACE**: System interface compatibility (e.g., SADE_ATC_API versions)

Evidence appears in two forms:
- **Evidence Requirement**: Requested when more proof is needed
- **Evidence Attestation**: Returned by SafeCert with satisfaction status

## Installation

### Prerequisites

- Python 3.10+ (codebase uses modern typing syntax, e.g. `X | Y`)
- pip

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd agentic-sade-dev
```

2. Install the package in editable mode (dependencies are declared in [`pyproject.toml`](pyproject.toml)):
```bash
pip install -r requirements.txt
```
This installs the local package as `-e .` (see [`requirements.txt`](requirements.txt)). For dev/test extras (e.g. `fakeredis`): `pip install -r requirements-dev.txt`.

Note: The project uses the **OpenAI Agents SDK** (`openai-agents`), which provides the `agents` module (`Agent`, `Runner`, `trace`). The HTTP layer uses **FastAPI**, **Uvicorn**, and **httpx**; **`redis`** is installed for optional `REDIS_URL` queue mode. No Redis server is required unless you enable that mode.

### API Configuration

The system uses OpenAI models via the Agents SDK. Agents are configured in [`src/sade/main.py`](src/sade/main.py) (currently `gpt-5.2` for the orchestrator and sub-agents). At minimum, set your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-...
```

You can change models and `ModelSettings` in `src/sade/main.py` following the OpenAI Agents SDK documentation.

### Retries and rate limits

Orchestrator runs use `_run_orchestrator_with_transport_retry` in `src/sade/main.py`: **only transient** transport failures (timeouts, connection errors, HTTP 429 / 5xx where applicable) are retried, with short exponential backoff (capped). Contract and validation errors are **not** retried. If you hit sustained rate limits, reduce concurrency, increase backoff in code, or use an account tier with higher limits.

## Usage

### Programmatic use

```python
import asyncio
from sade.main import process_entry_request

async def run():
    # Build a full merged request dict (UUIDs, zone/pilot/uav, weather, claims, reputation, …)
    request = { ... }  # see src/sade/resources/entry-requests-files/entry-requests/*.json
    out = await process_entry_request(request)
    print(out)  # { "decision": ..., "visibility": ... }

asyncio.run(run())
```

### CLI scenarios (`sade.main`)

The CLI driver does not run without a scenario flag. It loads a base entry request from [`src/sade/resources/entry-requests-files/entry-requests/`](src/sade/resources/entry-requests-files/entry-requests/) and merges attestation, reputation, and history fixtures from sibling folders under [`src/sade/resources/entry-requests-files/`](src/sade/resources/entry-requests-files/).

```bash
python -m sade.main accept
python -m sade.main accept_with_constraints
python -m sade.main action_required
python -m sade.main deny
python -m sade.main no_reputation_records
```

Equivalent: `python -m sade accept` (same flags; uses [`src/sade/__main__.py`](src/sade/__main__.py)).

Outputs (under the repository root via [`src/sade/paths.py`](src/sade/paths.py)):

- Human-readable trace: `results/integration/entry_result_<scenario>.txt`
- Evaluation-style JSON (same mapping as the API helper): `results/integration/entry_result_SADE_API_<scenario>.json`

The `results/integration/` directory is created automatically when missing.

### Async HTTP ingest (`decision-request` / `decision-result`)

The [`src/sade/api.py`](src/sade/api.py) FastAPI app accepts a **full** entry-request JSON (same merged shape as the CLI examples: include `evaluation_id`, `evaluation_series_id`, zone/pilot/UAV/weather, `attestation_claims`, `reputation_records`, etc.). Optional field **`decision_result_url`** (http/https): if present, the completed evaluation is `POST`ed there for that request only, and the field is removed before orchestration so it is not shown to the LLM. If omitted, `DECISION_RESULT_URL` is used when set.

**Execution model**

- **No `REDIS_URL`**: After accepting a request, the API schedules [`run_evaluation_job`](src/sade/evaluation_job.py) in-process (`asyncio.create_task`). Idempotency for duplicate `evaluation_id` values is tracked **in memory** in that process only.
- **`REDIS_URL` set**: The API enqueues work to a **Redis Stream** ([`src/sade/queue_redis.py`](src/sade/queue_redis.py)) and returns **202** immediately. A separate process must run the worker module, which consumes the stream and calls `run_evaluation_job`. Idempotency keys live in Redis, so duplicate submits are deduplicated **across** API replicas that share the same Redis.

Evaluation work (orchestration, optional persistence under `results/api-integration/`, callback retries) is implemented once in [`src/sade/evaluation_job.py`](src/sade/evaluation_job.py).

**Redis (optional, recommended for production-style setups)**

Start a local Redis (see [`docker-compose.yml`](docker-compose.yml)):

```bash
docker compose up -d
export REDIS_URL=redis://127.0.0.1:6379/0
```

Then run the API and worker in separate terminals:

```bash
# Terminal A — ingest only
uvicorn sade.api:app --host 0.0.0.0 --port 8000

# Terminal B — consumer (required when REDIS_URL is set)
python -m sade.decision_worker
```

**Run locally (in-process queue, no Redis):**

```bash
pip install -r requirements.txt
uvicorn sade.api:app --host 0.0.0.0 --port 8000
```

**Environment variables:**

| Variable | Purpose |
|----------|---------|
| `REDIS_URL` | If set (e.g. `redis://127.0.0.1:6379/0`), enqueue decision work to Redis Streams and require `python -m sade.decision_worker`. If unset, evaluations run in the API process as background tasks. |
| `SADE_STREAM_KEY` | Redis stream name (default `sade:decisions`). |
| `SADE_CONSUMER_GROUP` | Redis consumer group for `XREADGROUP` (default `sade-workers`). |
| `SADE_IDEMPOTENCY_PREFIX` | Prefix for Redis keys that gate idempotent enqueue (default `sade:ingest`). |
| `SADE_IDEMPOTENCY_TTL_SEC` | TTL for idempotency keys in seconds (default 30 days; minimum 60 when overridden). |
| `SADE_STREAM_BLOCK_MS` | Worker blocking read timeout in ms (default 5000; minimum 1000 when overridden). |
| `SADE_CONSUMER_NAME` | Worker consumer name; default includes hostname and PID. |
| `DECISION_RESULT_URL` | Full URL for outbound `POST` of the completed evaluation payload (`to_evaluation_api_payload` or `build_processing_failed_response`). If unset, the result is only logged at INFO (useful for local runs without a receiver). Failed callbacks are retried with exponential backoff (up to 5 attempts) for transient HTTP statuses (429, 5xx) and transport errors. |
| `SADE_PERSIST_RESULTS` | If `0` / `false` / `no`, skip writing orchestrator JSON under `results/api-integration/`. Default: write `entry_result_{evaluation_id}.json` (`decision` + `visibility`, same contract as the CLI body). Nothing is written if processing fails before a final orchestrator JSON exists. |
| `SADE_INGEST_API_KEY` | Single allowed API key. If set (non-empty), `POST /decision-request` requires `X-API-Key: <key>` or `Authorization: Bearer <key>`. |
| `SADE_INGEST_API_KEYS` | Comma-separated list of allowed keys (overrides `SADE_INGEST_API_KEY` when non-empty). |
| `SADE_INGEST_REVOKED_KEYS` | Comma-separated keys that must receive **403** if presented (even if they would otherwise match an allow list). |

**`POST /decision-request`**

- **202 Accepted** — First time this `evaluation_id` is queued; body: `{"status":"ACCEPTED","evaluation_id","evaluation_series_id"}`.
- **200 OK** — Idempotent retry with the same `evaluation_id` and `evaluation_series_id` (no second orchestration run).
- **400** — Malformed JSON, invalid body shape (FastAPI validation), bad UUIDs, bad `decision_result_url`, or `evaluation_id` reused with a different `evaluation_series_id`.
- **401** — API key enforcement is enabled (`SADE_INGEST_API_KEY` / `SADE_INGEST_API_KEYS`) but the request is missing or invalid credentials.
- **403** — The caller presented a key listed in `SADE_INGEST_REVOKED_KEYS`.
- **404** — No handler for the request path (wrong URL or method-only routes elsewhere).

When no API keys are configured, authentication is **not** enforced by the app (use network controls or a gateway for production).

**Idempotency**: Without `REDIS_URL`, deduplication is **in-memory and single-process** only (multiple Uvicorn workers or containers do not share state). With `REDIS_URL`, the same `evaluation_id` maps to a Redis key before `XADD`, so duplicate submits return **200** without a new stream message and work is not duplicated across API instances that share that Redis.

**Example:**

```bash
curl -sS -X POST "http://127.0.0.1:8000/decision-request" \
  -H "Content-Type: application/json" \
  -d @src/sade/resources/entry-requests-files/entry-requests/accept_entry_request.json
```

**Python helper** ([`scripts/send_decision_request.py`](scripts/send_decision_request.py), defaults to API-oriented JSON under `src/sade/resources/entry-requests-api/` and `http://127.0.0.1:8000/decision-request`):

- **Default:** starts a local callback HTTP server, sets `decision_result_url` on the payload, POSTs to ingest, then **waits** until the API POSTs the finished evaluation back (same machine; the API must reach the callback URL—typically `uvicorn` on `127.0.0.1` while the script listens on `127.0.0.1` with an ephemeral port).
- **`--accept-only`:** print the 202/200 acceptance response only (no listener). Use `--repeat` with this to hit idempotency (202 then 200).

Environment variable **`DECISION_REQUEST_URL`** overrides the ingest URL (same as `--url`).

```bash
# Terminal A
uvicorn sade.api:app --host 0.0.0.0 --port 8000

# Terminal B
python scripts/send_decision_request.py
python scripts/send_decision_request.py --wait-timeout 7200
python scripts/send_decision_request.py --accept-only
python scripts/send_decision_request.py --accept-only --repeat
python scripts/send_decision_request.py --file src/sade/resources/entry-requests-api/accept_entry_request.json
```

Small **HTTP smoke scripts** (one status each) live under [`scripts/ingest_examples/`](scripts/ingest_examples/): `ingest_202_accepted.py`, `ingest_200_idempotent.py`, `ingest_400_bad_request.py`, `ingest_401_unauthorized.py`, `ingest_403_forbidden.py`, `ingest_404_not_found.py`. They print status code and body; use `DECISION_REQUEST_URL` and `SADE_INGEST_API_KEY` when the API requires them.

## Decision Flow

The Orchestrator follows a mandatory state machine:

1. **Validate Request**: Check request format and required fields
2. **Retrieve Signals**: Call Environment and Reputation agents
3. **Pair-wise Analysis**: 
   - Request × Environment
   - Request × Reputation
   - Environment × Reputation
4. **Initial Decision**: Fast path decision (APPROVED, APPROVED-CONSTRAINTS, ACTION-REQUIRED, or DENIED)
5. **Claims verification** (when required by the orchestrator state machine): verify required actions against DPO claims using the Claims Agent
6. **Final decision**: Emit final JSON (`decision` + `visibility`) in one orchestrator run

The orchestrator runs in a single pass (no outer loop) and must emit a final decision within the maximum turn limit (default 25 turns, see `DEFAULT_MAX_TURNS` in `src/sade/main.py`).

## Project Structure

```
agentic-sade-dev/
├── pyproject.toml                 # Package metadata, dependencies, setuptools package-data
├── src/sade/                      # Installable Python package (`pip install -e .`)
│   ├── __init__.py
│   ├── __main__.py                # `python -m sade` → CLI scenario driver
│   ├── paths.py                   # repo_root() for results/ at project root
│   ├── main.py                    # Orchestrator + sub-agents; CLI scenario driver
│   ├── api.py                     # FastAPI ingest (POST /decision-request)
│   ├── evaluation_job.py          # run_evaluation_job: orchestration, persist, callbacks
│   ├── queue_redis.py             # Redis Streams enqueue + worker loop
│   ├── decision_worker.py         # Standalone consumer (run as `python -m sade.decision_worker`)
│   ├── evaluation_api_response.py
│   ├── models.py
│   ├── prompts/                   # Agent prompts (package data)
│   └── resources/                 # Fixture JSON (package data): entry-requests-files/, entry-requests-api/
├── docker/                        # Reserved for Dockerfiles / Compose (phase 2)
├── docker-compose.yml             # Local Redis (append-only) for the queue
├── scripts/
│   ├── send_decision_request.py
│   └── ingest_examples/
├── tests/
│   └── test_evaluation_api_response.py
├── results/                       # Generated outputs (gitignored)
│   ├── integration/               # CLI: entry_result_<scenario>.*
│   └── api-integration/           # API persistence when enabled
├── requirements.txt               # `-e .` editable install
├── requirements-dev.txt           # `-e .[dev]`
└── README.md
```

## Development Notes

### Safety-Critical Design Principles

- **Conservative**: When uncertain, require evidence
- **Evidence-driven**: Never assume unstated capabilities or certifications
- **Deterministic**: Follow the decision state machine exactly
- **Auditable**: Every decision must be defensible
- **Minimalism**: Request only the smallest set of evidence required

### Tool Communication Protocol

- The orchestrator passes each sub-agent a **JSON string** whose object shape matches the input contracts in [`src/sade/models.py`](src/sade/models.py) (`EnvironmentAgentInput`, `ReputationAgentInput`, `ClaimsAgentInput`, and nested types).
- Sub-agents return **Pydantic-validated** structured outputs (`EnvironmentAgentOutput`, `ReputationAgentOutput`, `ClaimsAgentOutput`).

### Mock versus production

In this repository, **environment**, **reputation**, and **claims** are implemented as **LLM sub-agents** with structured outputs (`src/sade/models.py`); the orchestrator passes JSON slices of the entry request per `src/sade/prompts/*.md`. A production deployment would typically replace or augment these with deterministic services (weather, reputation store, claims DB) and/or attach real tools to the SDK agents, while keeping the same external contracts.

### Constraints

Constraints are enforceable operational limits such as:
- `SPEED_LIMIT(7m/s)`
- `MAX_ALTITUDE(300m)`
- Reduced region polygons
- Modified route waypoints

Constraints must be:
- Justified by environment or geometry
- NOT replace missing certifications or mitigations

## Testing

- **Optional dev deps** (`fakeredis`, etc.): `pip install -r requirements-dev.txt`
- **Unit tests**: `python -m unittest discover -s tests` (requires `pip install -e .` or `pip install -r requirements.txt`). Some tests reference golden files under `results/integration/`; generate or refresh those with `python -m sade.main <scenario>` when contracts change.
- **CLI integration**: Run `python -m sade.main accept` (and other scenarios) and inspect `results/integration/`.
- **API integration (in-process)**: Run `uvicorn sade.api:app` without `REDIS_URL`, then `python scripts/send_decision_request.py` (or `curl` as above) and inspect the callback JSON and `results/api-integration/` when persistence is enabled.
- **API + Redis**: Set `REDIS_URL`, run `docker compose up -d`, start `uvicorn sade.api:app` and `python -m sade.decision_worker`, then exercise ingest as above.

## Contributing

This is a safety-critical system. All changes must:
- Maintain deterministic behavior
- Preserve auditability
- Follow the evidence grammar specification
- Not bypass safety checks

## License

[Specify license]

## References

- See [`src/sade/prompts/orchestrator_prompt.md`](src/sade/prompts/orchestrator_prompt.md) for orchestrator decision logic
- See [`src/sade/models.py`](src/sade/models.py) for data model specifications
- See [`src/sade/evaluation_api_response.py`](src/sade/evaluation_api_response.py) for the external evaluation API mapping
