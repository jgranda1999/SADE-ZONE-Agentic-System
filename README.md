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
- **Purpose**: Retrieve external operating conditions
- **Data**: Weather (wind, gusts, precipitation, visibility), light conditions, airspace/spatial constraints, manufacturer flight constraints (MFC)
- **Tools**: `retrieveEnvironment`, `retrieveMFC`

#### Reputation Agent
- **Purpose**: Retrieve historical trust and reliability signals
- **Data**: Pilot, organization, and drone reputation scores; incident history (including unresolved incidents)
- **Tool**: `retrieve_reputations`

#### Claims Agent
- **Purpose**: Verify required actions against DPO claims and follow-up records
- **Data**: Checks satisfaction of required actions, resolves incident prefixes, tracks unresolved incidents
- **Tool**: `retrieve_claims`
- **Note**: Called when ACTION-REQUIRED decision is issued to verify evidence satisfaction

#### Action Required Agent (SafeCert Interface)
- **Purpose**: Interface with SafeCert/Proving Grounds
- **Function**: Requests and retrieves formal evidence attestations
- **Tool**: `request_attestation`
- **Note**: Never makes admission decisions

## Entry Request Model

Each entry request includes:

- `sade_zone_id`: Target SADE zone identifier
- `pilot_id`: FAA pilot registration (e.g., "FA-01234567")
- `organization_id`: Organization identifier
- `drone_id`: Drone identifier
- `requested_entry_time`: ISO8601 datetime string
- `request_type`: One of `ZONE`, `REGION`, or `ROUTE`
- `request_payload`: Type-specific payload
  - **ZONE**: Full zone access
  - **REGION**: `polygon` (lat/lon coordinates), `ceiling`, `floor` (meters ASL)
  - **ROUTE**: Ordered `waypoints` (lat, lon, altitude ASL)

## Decision Outputs

The Orchestrator outputs exactly one of:

- `APPROVED`: Entry allowed without constraints
- `APPROVED-CONSTRAINTS,(...)`: Entry allowed with enforceable operational limits
- `ACTION-REQUIRED,(...)`: Additional evidence/certification required (includes evidence requirement JSON)
- `DENIED,(DENIAL_CODE, explanation)`: Fundamentally unsafe or policy-forbidden

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

- Python 3.8+
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

Note: The project uses the **OpenAI Agents SDK** (`openai-agents`), which provides the `agents` module (`Agent`, `Runner`, `trace`).

### API Configuration

The system uses OpenAI models via the Agents SDK. At minimum, you must configure your OpenAI API key:

```bash
export OPENAI_API_KEY=sk-...
```

You can further customize model selection and other settings following the OpenAI Agents SDK documentation.

### Rate Limiting

When processing multiple requests in batch, the system includes a configurable delay between requests to avoid hitting API rate limits. The default delay is set to 60 seconds in `main.py`. To adjust this delay, modify the `asyncio.sleep()` value in the `main()` function:

```python
# In main.py
await asyncio.sleep(60.0)  # Adjust delay as needed
```

**Note**: Rate limits vary by model and organization. If you encounter rate limit errors (HTTP 429), increase the delay between requests or consider using a model with higher rate limits.

## Usage

### Basic Example

```python
import asyncio
from main import process_entry_request

async def main():
    request = {
        "sade_zone_id": "ZONE-123",
        "pilot_id": "FA-01234567",
        "organization_id": "ORG-789",
        "drone_id": "DRONE-001",
        "requested_entry_time": "2026-01-26T14:00:00Z",
        "request_type": "REGION",
        "request_payload": {
            "polygon": [
                {"lat": 41.7000, "lon": -86.2400},
                {"lat": 41.7010, "lon": -86.2400},
                {"lat": 41.7010, "lon": -86.2390},
                {"lat": 41.7000, "lon": -86.2390},
            ],
            "ceiling": 300,  # meters ASL
            "floor": 100,    # meters ASL
        },
    }
    
    decision = await process_entry_request(request)
    print(decision)

if __name__ == "__main__":
    asyncio.run(main())
```

### Running the Example

```bash
python main.py
```

By default, the script reads `sade-mock-data/entry_requests.json`, processes the first entry request, and writes a result file into one of the scenario-specific subdirectories under `results/` (for example, `results/mfc-payload/mfc-payload-bad/entry_result_{ZONE_ID}.txt`). You can modify the loop in `main()` to process additional requests or change which scenario directory is used.

### GUI Visualizer

This repository includes a PyQt5 GUI for visualizing orchestrator decisions and sub-agent visibility.

- **Launch without a file** (empty state):
  ```bash
  python gui.py
  ```
- **Launch with a specific result file**:
  ```bash
  python gui.py results/mfc-payload/mfc-payload-bad/entry_result_ZONE-001.txt
  ```

The GUI also exposes presets for the bundled demo scenarios:

- `Weather · Wind Good` / `Medium` / `Bad`
- `MFC/Payload · Good` / `Medium` / `Bad`

## Decision Flow

The Orchestrator follows a mandatory state machine:

1. **Validate Request**: Check request format and required fields
2. **Retrieve Signals**: Call Environment and Reputation agents
3. **Pair-wise Analysis**: 
   - Request × Environment
   - Request × Reputation
   - Environment × Reputation
4. **Initial Decision**: Fast path decision (APPROVED, APPROVED-CONSTRAINTS, ACTION-REQUIRED, or DENIED)
5. **Claims Verification**: If ACTION-REQUIRED, verify required actions against DPO claims using Claims Agent
6. **Evidence Escalation**: If needed, request attestations from SafeCert via Action Required Agent
7. **Re-evaluation**: Evaluate attestation satisfaction and claims verification results
8. **Final Decision**: Emit final decision

The orchestrator runs in a single pass (no outer loop) and must emit a final decision within the maximum turn limit (default 25 turns, see `DEFAULT_MAX_TURNS` in `main.py`).

## Project Structure

```
agentic-sade-dev/
├── main.py                        # CLI entry point and orchestration logic
├── gui.py                         # PyQt5 GUI for visualizing decisions
├── models.py                      # Pydantic models for agent outputs and evidence grammar
├── tools/
│   ├── environment_tools.py       # Environment agent tools (weather, MFC, spatial constraints)
│   ├── reputation_tools.py        # Reputation agent tools
│   ├── claims_tools.py            # Claims agent tools
│   └── action_required_tools.py   # SafeCert interface tools (mock implementation)
├── v4_prompts/                    # Current agent prompts (orchestrator + sub‑agents)
│   ├── orchestrator_prompt.md
│   ├── env_agent_prompt.md
│   ├── rm_agent_prompt.md
│   └── claims_agent_prompt.md
├── v3_prompts/                    # Prior prompt iteration (kept for comparison)
├── v2_prompts/                    # Earlier prompt iteration (kept for comparison)
├── v1_prompts/                    # Legacy prompts (original design)
├── sade-mock-data/                # Mock data for testing
│   ├── entry_requests.json        # Example entry requests
│   ├── reputation_model.json      # Sample reputation model data
│   └── user_input.json            # Sample user input / claims data
├── results/                       # Output directory for decision results
│   ├── weather/
│   │   ├── wind-visibility-good/
│   │   ├── wind-visibility-medium/
│   │   └── wind-visibility-bad/
│   └── mfc-payload/
│       ├── mfc-payload-good/
│       ├── mfc-payload-medium/
│       └── mfc-payload-bad/
├── other/
│   └── project_overview.md        # Detailed architecture and policy documentation
├── to-dos.md                      # Development notes and follow-up tasks
├── requirements.txt               # Python dependencies (OpenAI Agents SDK, etc.)
└── README.md                      # This file
```

## Development Notes

### Safety-Critical Design Principles

- **Conservative**: When uncertain, require evidence
- **Evidence-driven**: Never assume unstated capabilities or certifications
- **Deterministic**: Follow the decision state machine exactly
- **Auditable**: Every decision must be defensible
- **Minimalism**: Request only the smallest set of evidence required

### Tool Communication Protocol

- Sub-agent tools accept **JSON string** inputs (not Python dicts)
- Tools return validated **Pydantic model** outputs (automatically validated)
- Field name mapping:
  - Entry Request `organization_id` → tool input `org_id`
  - Entry Request `requested_entry_time` → tool input `entry_time`

### Mock Implementations

The current tool implementations (`environment_tools.py`, `reputation_tools.py`, `claims_tools.py`, `action_required_tools.py`) are **mock implementations** for testing. In production, these would:

- Call actual weather APIs and airspace databases (Environment Agent)
- Query the Reputation Model Profile endpoint (Reputation Agent)
- Query DPO claims and follow-up records (Claims Agent)
- Interface with the SafeCert API (Action Required Agent)

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

The system processes example requests from `sade-mock-data/entry_requests.json`. To test different scenarios:

1. Modify the entry requests in `sade-mock-data/entry_requests.json`
2. Or modify the `main()` function to process specific requests
3. Results are written to `results/entry_result_{ZONE_ID}.txt`

**Note**: When processing multiple requests, the system includes a 60-second delay between requests to avoid API rate limits. Adjust this delay in `main.py` if needed based on your API rate limits.

## Contributing

This is a safety-critical system. All changes must:
- Maintain deterministic behavior
- Preserve auditability
- Follow the evidence grammar specification
- Not bypass safety checks

## License

[Specify license]

## References

- See `other/project_overview.md` for detailed architecture documentation
- See `v4_prompts/orchestrator_prompt.md` for the current Orchestrator decision logic
- See `models.py` for complete data model specifications
- See `v4_prompts/` directory for all current agent prompts (and `v1_prompts/`–`v3_prompts/` for historical versions)
