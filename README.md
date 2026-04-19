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
- **Exposure**: `environment_agent` — orchestrator calls this sub-agent as a tool (see `main.py`)

#### Reputation Agent
- **Purpose**: Interpret historical trust and reliability signals from provided records
- **Data**: Pilot, organization, and drone reputation; incident history (including unresolved incidents)
- **Exposure**: `reputation_agent` — orchestrator calls this sub-agent as a tool (see `main.py`)

#### Claims Agent
- **Purpose**: Verify required actions against DPO claims and follow-up records
- **Data**: Checks satisfaction of required actions, resolves incident prefixes, tracks unresolved incidents; may emit `evidence_requirement_spec` when not satisfied
- **Exposure**: `claims_agent` — orchestrator calls this sub-agent as a tool (see `main.py`)
- **Note**: Invoked when the orchestrator state machine requires claims verification (see `prompts/orchestrator_prompt.md`)

#### SafeCert / attestation (future hook)

Entry JSON may include fields such as `safecert_pin` or `evidence_required` for re-evaluation flows. A dedicated SafeCert **Action Required** sub-agent (and companion attestation tools) is **not** wired into the current `main.py` graph; the orchestrator plus claims path covers the MVP decision contract.

## Entry Request Model

The orchestrator consumes a **single merged JSON object** per evaluation. Typical fields include:

- **Identifiers**: `evaluation_id`, `evaluation_series_id` (UUIDs), `entry_request_kind`
- **Zone / pilot / UAV**: nested `zone`, `pilot`, `uav` (e.g. `sade_zone_id`, `pilot_id`, `organization_id`, `drone_id`)
- **Operation**: `payload`, `requested_entry_time`, `requested_exit_time`, `request_type` (derived from `entry_request_kind` when omitted)
- **Signals**: `weather_forecast`, `uav_model`
- **Records**: `reputation_records`, `attestation_claims`, `entry_request_history` (lists; may be empty)

Request geometry (regions, routes) lives inside the JSON as provided to the model; see [`resources/entry-requests/`](resources/entry-requests/) for worked examples.

## Decision Outputs

The orchestrator emits exactly one internal decision (`decision.type` in JSON):

- `APPROVED`: Entry allowed without constraints
- `APPROVED-CONSTRAINTS`: Entry allowed with enforceable operational limits
- `ACTION-REQUIRED`: Additional evidence or certification required (may include `evidence_requirement_spec`)
- `DENIED`: Fundamentally unsafe or policy-forbidden (includes `denial_code` where applicable)

The HTTP ingest and [`evaluation_api_response.py`](evaluation_api_response.py) map these to the external evaluation API using **underscores** in `result.decision` (for example `APPROVED_CONSTRAINTS`, `ACTION_REQUIRED`). The human-readable narrative is carried in `decision.explanation` internally and exposed as `result.reason` in the API payload.

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

2. Install dependencies:
```bash
pip install -r requirements.txt
```

Note: The project uses the **OpenAI Agents SDK** (`openai-agents`), which provides the `agents` module (`Agent`, `Runner`, `trace`). The HTTP layer uses **FastAPI**, **Uvicorn**, and **httpx** (see `requirements.txt`).

### API Configuration

The system uses OpenAI models via the Agents SDK. Agents are configured in [`main.py`](main.py) (currently `gpt-5.2` for the orchestrator and sub-agents). At minimum, set your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-...
```

You can change models and `ModelSettings` in `main.py` following the OpenAI Agents SDK documentation.

### Retries and rate limits

Orchestrator runs use `_run_orchestrator_with_transport_retry` in `main.py`: **only transient** transport failures (timeouts, connection errors, HTTP 429 / 5xx where applicable) are retried, with short exponential backoff (capped). Contract and validation errors are **not** retried. If you hit sustained rate limits, reduce concurrency, increase backoff in code, or use an account tier with higher limits.

## Usage

### Programmatic use

```python
import asyncio
from main import process_entry_request

async def run():
    # Build a full merged request dict (UUIDs, zone/pilot/uav, weather, claims, reputation, …)
    request = { ... }  # see resources/entry-requests/*.json
    out = await process_entry_request(request)
    print(out)  # { "decision": ..., "visibility": ... }

asyncio.run(run())
```

### CLI scenarios (`main.py`)

`main.py` does not run without a scenario flag. It loads a base entry request from [`resources/entry-requests/`](resources/entry-requests/) and merges attestation, reputation, and history fixtures from [`resources/attestation-claims/`](resources/attestation-claims/), [`resources/reputation-records/`](resources/reputation-records/), and [`resources/entry-request-history/`](resources/entry-request-history/).

```bash
python main.py accept
python main.py accept_with_constraints
python main.py action_required
python main.py deny
python main.py no_reputation_records
```

Outputs:

- Human-readable trace: `results/integration/entry_result_<scenario>.txt`
- Evaluation-style JSON (same mapping as the API helper): `results/integration/entry_result_SADE_API_<scenario>.json`

Create `results/integration/` if it does not exist, or adjust paths in `main()` if you prefer another directory.

### Async HTTP ingest (`decision-request` / `decision-result`)

The [`api.py`](api.py) FastAPI app accepts a **full** entry-request JSON (same merged shape as the CLI examples: include `evaluation_id`, `evaluation_series_id`, zone/pilot/UAV/weather, `attestation_claims`, `reputation_records`, etc.). Optional field **`decision_result_url`** (http/https): if present, the completed evaluation is `POST`ed there for that request only, and the field is removed before orchestration so it is not shown to the LLM. If omitted, `DECISION_RESULT_URL` is used when set.

**Run locally:**

```bash
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8000
```

**Environment variables:**

| Variable | Purpose |
|----------|---------|
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

**Idempotency** is in-memory and **single-process** only (multiple Uvicorn workers or containers do not share deduplication state).

**Example:**

```bash
curl -sS -X POST "http://127.0.0.1:8000/decision-request" \
  -H "Content-Type: application/json" \
  -d @resources/entry-requests/accept_entry_request.json
```

**Python helper** ([`scripts/send_decision_request.py`](scripts/send_decision_request.py), defaults to `resources/entry-requests/action_required_entry_request.json` and `http://127.0.0.1:8000/decision-request`):

- **Default:** starts a local callback HTTP server, sets `decision_result_url` on the payload, POSTs to ingest, then **waits** until the API POSTs the finished evaluation back (same machine; the API must reach the callback URL—typically `uvicorn` on `127.0.0.1` while the script listens on `127.0.0.1` with an ephemeral port).
- **`--accept-only`:** print the 202/200 acceptance response only (no listener). Use `--repeat` with this to hit idempotency (202 then 200).

Environment variable **`DECISION_REQUEST_URL`** overrides the ingest URL (same as `--url`).

```bash
# Terminal A
uvicorn api:app --host 0.0.0.0 --port 8000

# Terminal B
python scripts/send_decision_request.py
python scripts/send_decision_request.py --wait-timeout 7200
python scripts/send_decision_request.py --accept-only
python scripts/send_decision_request.py --accept-only --repeat
python scripts/send_decision_request.py --file resources/entry-requests/accept_entry_request.json
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

The orchestrator runs in a single pass (no outer loop) and must emit a final decision within the maximum turn limit (default 25 turns, see `DEFAULT_MAX_TURNS` in `main.py`).

## Project Structure

```
agentic-sade-dev/
├── main.py                        # Orchestrator + sub-agents; CLI scenario driver
├── api.py                         # FastAPI ingest (POST /decision-request); background eval; callback + persist
├── evaluation_api_response.py     # Maps orchestrator JSON ↔ evaluation API payload
├── models.py                      # Pydantic models for agent outputs and evidence grammar
├── prompts/                       # Current agent prompts (loaded by main.py)
│   ├── orchestrator_prompt.md
│   ├── env_agent_prompt.md
│   ├── rm_agent_prompt.md
│   └── claims_agent_prompt.md
├── resources/                     # Fixture JSON for CLI scenarios and API examples
│   ├── entry-requests/
│   ├── attestation-claims/
│   ├── reputation-records/
│   └── entry-request-history/
├── scripts/
│   ├── send_decision_request.py   # Local ingest + optional callback listener
│   └── ingest_examples/           # Tiny scripts per HTTP status (202, 200, 400, 401, 403, 404)
├── tests/
│   └── test_evaluation_api_response.py
├── results/                       # Generated outputs (gitignored as needed)
│   ├── integration/             # main.py CLI: entry_result_<scenario>.txt / SADE_API JSON
│   └── api-integration/          # api.py: entry_result_<evaluation_id>.json when enabled
├── requirements.txt
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

- The orchestrator passes each sub-agent a **JSON string** whose object shape matches the input contracts in [`models.py`](models.py) (`EnvironmentAgentInput`, `ReputationAgentInput`, `ClaimsAgentInput`, and nested types).
- Sub-agents return **Pydantic-validated** structured outputs (`EnvironmentAgentOutput`, `ReputationAgentOutput`, `ClaimsAgentOutput`).

### Mock versus production

In this repository, **environment**, **reputation**, and **claims** are implemented as **LLM sub-agents** with structured outputs (`models.py`); the orchestrator passes JSON slices of the entry request per `prompts/*.md`. A production deployment would typically replace or augment these with deterministic services (weather, reputation store, claims DB) and/or attach real tools to the SDK agents, while keeping the same external contracts.

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

- **Unit tests**: `tests/test_evaluation_api_response.py` (payload mapping and helpers). Some tests reference golden files under `results/integration/`; generate or refresh those with `python main.py <scenario>` when contracts change.
- **CLI integration**: Run `python main.py accept` (and other scenarios) and inspect `results/integration/`.
- **API integration**: Run `uvicorn api:app`, then `python scripts/send_decision_request.py` (or `curl` as above) and inspect the callback JSON and `results/api-integration/` when persistence is enabled.

## Contributing

This is a safety-critical system. All changes must:
- Maintain deterministic behavior
- Preserve auditability
- Follow the evidence grammar specification
- Not bypass safety checks

## License

[Specify license]

## References

- See [`prompts/orchestrator_prompt.md`](prompts/orchestrator_prompt.md) for orchestrator decision logic
- See [`models.py`](models.py) for data model specifications
- See [`evaluation_api_response.py`](evaluation_api_response.py) for the external evaluation API mapping
