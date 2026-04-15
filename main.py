import asyncio
import json
import re
import sys, os
from pathlib import Path
from typing import Dict, Any, Iterable
from resources import *

from agents import Agent, Runner, trace, ModelSettings
from evaluation_api_response import to_evaluation_api_payload

try:
    from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
except Exception:  # pragma: no cover - keep runtime resilient if SDK shape changes
    APIConnectionError = APITimeoutError = RateLimitError = APIStatusError = tuple()  # type: ignore

# Single-run flow: orchestrator must not return until final decision (v2 prompt)
DEFAULT_MAX_TURNS = 25

# STATE 0/1 ACTION-REQUIRED only — orchestrator does not call claims_agent (see v5 orchestrator prompt).
_ACTION_REQUIRED_NO_CLAIMS_ACTIONS = frozenset(
    {"FIX_INVALID_ENTRY_REQUEST", "RETRY_SIGNAL_RETRIEVAL"}
)
_CLAIMS_SPEC_MISSING_TOKEN = "claims_agent.evidence_requirement_spec missing"
_CLAIMS_SPEC_CONFLICT_TOKEN = "claims_agent.evidence_requirement_spec conflicts with satisfied=true"
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _claims_call_required(decision: Dict[str, Any]) -> bool:
    """
    True if ACTION-REQUIRED implies visibility.claims_agent.called must be true.
    Exempt only STATE 0/1 early exits (fixed action list in prompt).
    """
    if decision.get("type") != "ACTION-REQUIRED":
        return False
    actions = decision.get("actions") or []
    if actions and all(a in _ACTION_REQUIRED_NO_CLAIMS_ACTIONS for a in actions):
        return False
    return True


def _extract_json_object(raw: str) -> Dict[str, Any]:
    """Extract first JSON object from raw text or fenced block."""
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1).strip())
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _claims_spec_present(claims: Dict[str, Any]) -> bool:
    """True when claims payload contains a non-empty evidence spec."""
    spec = claims.get("evidence_requirement_spec")
    return isinstance(spec, dict) and bool(spec.get("categories"))


def _iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    """Yield exception plus linked causes/contexts for robust classification."""
    seen = set()
    cur: BaseException | None = exc
    while cur and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = (cur.__cause__ or cur.__context__)


def _is_transient_transport_error(exc: BaseException) -> bool:
    """
    True only for transient API/transport failures.
    Contract/validation errors must never be retried.
    """
    for err in _iter_exception_chain(exc):
        if isinstance(err, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        if isinstance(err, APIStatusError) and getattr(err, "status_code", None) in _TRANSIENT_STATUS_CODES:
            return True
    msg = str(exc).lower()
    # Fallback for SDK-wrapped transport failures.
    return any(tok in msg for tok in ("timeout", "timed out", "connection reset", "rate limit", "http 429", "http 503"))


async def _run_orchestrator_with_transport_retry(
    formatted_request: str,
    max_turns: int,
    max_attempts: int = 3,
) -> Any:
    """Retry only transient API/transport failures with bounded backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            with trace("SADE Entry Request Processing"):
                return await Runner.run(
                    orchestrator_agent,
                    formatted_request,
                    max_turns=max_turns,
                )
        except Exception:
            exc = sys.exc_info()[1]
            if exc is None or not _is_transient_transport_error(exc) or attempt == max_attempts:
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 8))

from models import (
    EnvironmentAgentOutput,
    ReputationAgentOutput,
    ActionRequiredAgentOutput,
    ClaimsAgentOutput,
    OrchestratorOutput,
)
def load_prompt(prompt_file: str, prompts_dir: str = "prompts") -> str:
    """Load prompt text from a markdown file."""
    prompt_path = Path(__file__).parent / prompts_dir / prompt_file
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text()


# Load agent prompts (v3: orchestrator, env, rm, claims with visibility _prose; v1: action_required for SafeCert tool)
ORCHESTRATOR_PROMPT = load_prompt("orchestrator_prompt.md", prompts_dir="v5_prompts")
ENVIRONMENT_AGENT_PROMPT = load_prompt("env_agent_prompt.md", prompts_dir="v5_prompts")
REPUTATION_AGENT_PROMPT = load_prompt("rm_agent_prompt.md", prompts_dir="v5_prompts")
# ACTION_REQUIRED_AGENT_PROMPT = load_prompt("action_required_agent_prompt.md")
CLAIMS_AGENT_PROMPT = load_prompt("claims_agent_prompt.md", prompts_dir="v5_prompts")


# Sub-Agents (Advisory Only - Never Make Decisions)

environment_agent = Agent(
    name="environment_agent",
    # model_settings=ModelSettings(temperature=0.4),
    model="gpt-5.2",
    instructions=ENVIRONMENT_AGENT_PROMPT,
    output_type=EnvironmentAgentOutput,
    handoff_description=(
        "Analyzes entry-request weather and UAV model data and returns "
        "EnvironmentAgentOutput with wind/payload risk signals."
    ),
)

reputation_agent = Agent(
    name="reputation_agent",    
    model="gpt-5.2",
    # model_settings=ModelSettings(temperature=0.4),
    instructions=REPUTATION_AGENT_PROMPT,
    output_type=ReputationAgentOutput,
    handoff_description=(
        "Analyzes provided reputation_records and returns deterministic "
        "ReputationAgentOutput (demo_steady_max_kt, demo_gust_max_kt, demo_payload_max_kg, "
        "incident_codes, n_0100_0101, incident_analysis, risk_assessment, recommendation, why)."
    ),
)

claims_agent = Agent(
    name="claims_agent",
    # model_settings=ModelSettings(temperature=0.4),
    model="gpt-5.2",
    instructions=CLAIMS_AGENT_PROMPT,
    output_type=ClaimsAgentOutput,
    handoff_description=(
        "Verifies required_actions against attestation_claims and incident context "
        "(including PART_107_VERIFICATION when present). "
        "When satisfied is false, must return non-empty evidence_requirement_spec. "
        "Input includes wind_context and payload_context per ClaimsAgentInput."
    ),
)


# Orchestrator Agent (Sole Decision Authority)

orchestrator_agent = Agent(
    name="orchestrator_agent",
    model="gpt-5.2",
    instructions=ORCHESTRATOR_PROMPT,
    tools=[
        environment_agent.as_tool(
            tool_name="environment_agent",
            tool_description=(
                "Analyze environmental and MFC context from the provided minimal environment input. "
                "Input: JSON string with exactly the EnvironmentAgentInput subset: payload, uav, uav_model, weather_forecast. "
                "Do not pass the full entry request and do not include unrelated blocks (reputation_records, attestation_claims, entry_request_history, pilot, zone). "
                "Returns: EnvironmentAgentOutput (manufacturer_fc with manufacturer, model, category, mfc_payload_max_kg, mfc_max_wind_kt; raw_conditions; risk_assessment; constraint_suggestions_wind; constraint_suggestions_payload; recommendation_wind; recommendation_payload; recommendation_prose_wind; recommendation_prose_payload; why_prose_wind; why_prose_payload; why_wind; why_payload)."
            ),
        ),
        reputation_agent.as_tool(
            tool_name="reputation_agent",
            tool_description=(
                "Analyze provided historical reputation_records for the current DPO context. "
                "Input: JSON string with exactly the ReputationAgentInput subset: reputation_records. "
                "Do not pass the full entry request and do not include unrelated blocks (attestation_claims, weather_forecast, uav_model, pilot, uav, zone, entry_request_history). "
                "Returns: ReputationAgentOutput (validated Pydantic model) with incident_analysis, risk_assessment, demo_steady_max_kt, demo_gust_max_kt, demo_payload_max_kg, incident_codes, and n_0100_0101. "
                "If entry_request.reputation_records is missing or empty [], do NOT call this tool; the orchestrator prompt STATE 1 builds FIRST_TIME_DPO_REPUTATION_PROFILE from environment_agent MFC instead."
            ),
        ),
        claims_agent.as_tool(
            tool_name="claims_agent",
            tool_description=(
                "Verify required_actions against provided attestation_claims and incident context. "
                "Input: JSON string with exactly the ClaimsAgentInput subset: action_id, requested_entry_time, pilot, uav, required_actions, incident_codes, wind_context, payload_context, and attestation_claims. "
                "Do not pass the full entry request and do not include unrelated blocks (reputation_records, weather_forecast, uav_model, zone, entry_request_history). "
                "Returns: ClaimsAgentOutput with satisfied, resolved/unresolved incident prefixes, satisfied/unsatisfied actions, "
                "evidence_requirement_spec (required non-empty categories when satisfied=false), and why. "
                "Call when STATE 3 ends with ACTION-REQUIRED (rules 5–8 or PART_107 STATE 3b gate); orchestrator completes STATE 5 (including 5.3b Rule 7 preservation when applicable) before final JSON."
            ),
        ),
    ],
)


def format_entry_request(request: Dict[str, Any]) -> str:
    """
    Format an entry request dictionary into a structured text format
    for the Orchestrator Agent.
    """
    zone = request.get("zone") or {}
    pilot = request.get("pilot") or {}
    uav = request.get("uav") or {}
    request_type = request.get("request_type")
    if not request_type:
        request_type = "ZONE" if request.get("entry_request_kind") == "ENTRY" else "UNKNOWN"

    lines = [
        "ENTRY REQUEST",
        "=" * 50,
        f"Evaluation ID: {request.get('evaluation_id', 'MISSING')}",
        f"Evaluation Series ID: {request.get('evaluation_series_id', 'MISSING')}",
        f"Entry Request Kind: {request.get('entry_request_kind', 'MISSING')}",
        f"SADE Zone ID: {zone.get('sade_zone_id', 'MISSING')}",
        f"Pilot ID: {pilot.get('pilot_id', 'MISSING')}",
        f"Organization ID: {pilot.get('organization_id', 'MISSING')}",
        f"Drone ID: {uav.get('drone_id', 'MISSING')}",
        f"Payload (kg): {request.get('payload', 'MISSING')}",
        f"Requested Entry Time: {request.get('requested_entry_time', 'MISSING')}",
        f"Requested Exit Time: {request.get('requested_exit_time', 'MISSING')}",
        f"Request Type (derived): {request_type}",
    ]
    rep = request.get("reputation_records")
    rep_empty = rep is None or (isinstance(rep, list) and len(rep) == 0)
    lines.extend(
        [
            "",
            "Provided Data Blocks:",
            f"- uav_model: {'present' if isinstance(request.get('uav_model'), dict) else 'missing'}",
            f"- weather_forecast: {'present' if isinstance(request.get('weather_forecast'), dict) else 'missing'}",
            f"- reputation_records: {len(request.get('reputation_records', []))}",
            f"- attestation_claims: {len(request.get('attestation_claims', []))}",
            f"- entry_request_history: {len(request.get('entry_request_history', []))}",
        ]
    )
    if rep_empty:
        lines.append(
            "\nNote: reputation_records is empty — use orchestrator STATE 1 first-time DPO path "
            "(environment_agent only for signals; synthesize FIRST_TIME_DPO_REPUTATION_PROFILE; do not call reputation_agent)."
        )
    
    # Include safecert_pin if provided (for ACTION-REQUIRED re-evaluation)
    if 'safecert_pin' in request:
        lines.append(f"\nSafeCert PIN: {request['safecert_pin']}")
    
    # Include evidence_required if provided (for ACTION-REQUIRED re-evaluation)
    if 'evidence_required' in request:
        lines.append(f"\nEvidence Required:")
        lines.append(json.dumps(request['evidence_required'], indent=2))
    lines.append("\nENTRY REQUEST JSON (canonical):")
    lines.append(json.dumps(request, indent=2))
    
    return "\n".join(lines)

def parse_orchestrator_output(raw: str) -> Dict[str, Any]:
    """
    Parse the orchestrator's final output as JSON (v2/v3 contract: decision + visibility).

    Tries raw JSON first, then a single ```json ... ``` code block.
    Applies strict contract validation and fails on schema drift.
    Raises ValueError if no valid JSON object with "decision" is found.
    """
    parsed = _extract_json_object(raw)
    if isinstance(parsed, dict) and "decision" in parsed:
        OrchestratorOutput.model_validate(parsed)
        decision = parsed.get("decision", {})
        vis = parsed.get("visibility", {})
        claims = vis.get("claims_agent", {}) if isinstance(vis, dict) else {}
        if _claims_call_required(decision) and not claims.get("called", False):
            raise ValueError(
                "Invalid orchestrator output: ACTION-REQUIRED decision without claims_agent.called == true"
            )
        if _claims_spec_present(claims) and claims.get("satisfied") is True:
            raise ValueError(f"Invalid orchestrator output: {_CLAIMS_SPEC_CONFLICT_TOKEN}")
        if claims.get("called", False) and claims.get("satisfied") is False and not _claims_spec_present(claims):
            raise ValueError(f"Invalid orchestrator output: {_CLAIMS_SPEC_MISSING_TOKEN}")
        if _claims_spec_present(claims) and not isinstance(decision.get("evidence_requirement_spec"), dict):
            raise ValueError(
                "Invalid orchestrator output: claims provided evidence_requirement_spec but decision did not include it"
            )
        return parsed
    raise ValueError("Orchestrator output did not contain valid JSON with 'decision'")


async def process_entry_request(
    request: Dict[str, Any],
    max_turns: int = DEFAULT_MAX_TURNS,
) -> Dict[str, Any]:
    """
    Process an entry request through the SADE Orchestrator Agent (single run).

    The v2 orchestrator runs until it emits a final JSON (calling environment_agent;
    reputation_agent only when reputation_records is non-empty; claims_agent when
    STATE 3 ends ACTION-REQUIRED). Contract violations are fail-fast.
    Only transient OpenAI transport errors are retried.

    Args:
        request: Entry request dictionary
        max_turns: Max LLM/tool turns per run (default 25)

    Returns:
        Parsed output dict with "decision" and "visibility" (v2 contract).
        Raises ValueError if output could not be parsed as JSON.
    """
    formatted_request = format_entry_request(request)
    result = await _run_orchestrator_with_transport_retry(
        formatted_request=formatted_request,
        max_turns=max_turns,
    )
    raw = result.final_output.strip()
    return parse_orchestrator_output(raw)

# Main function
async def main():
    """
    Example entry request processing.
    """
    if "accept" in sys.argv:
        entry_request_file = "resources/entry-requests/accept_entry_request.json"
        attestation_claims_file = "resources/attestation-claims/accept_attestation_claims.json"
        reputation_records_file = "resources/reputation-records/accept_reputation_records.json"
        entry_request_history_file = "resources/entry-request-history/entry_request_history.json"
        test_name = "accept"
    elif "accept_with_constraints" in sys.argv:
        entry_request_file = "resources/entry-requests/accept_with_constraints_entry_request.json"
        attestation_claims_file = "resources/attestation-claims/accept_with_constraints_attestation_claims.json"
        reputation_records_file = "resources/reputation-records/accept_with_constraints_reputation_records.json"
        entry_request_history_file = "resources/entry-request-history/entry_request_history.json"
        test_name = "accept_with_constraints"
    elif "action_required" in sys.argv:
        entry_request_file = "resources/entry-requests/action_required_entry_request.json"
        attestation_claims_file = "resources/attestation-claims/action_required_attestation_claims.json"
        reputation_records_file = "resources/reputation-records/action_required_reputation_records.json"
        entry_request_history_file = "resources/entry-request-history/entry_request_history.json"
        test_name = "action_required"
    elif "deny" in sys.argv:
        entry_request_file = "resources/entry-requests/deny_entry_request.json"
        attestation_claims_file = "resources/attestation-claims/deny_attestation_claims.json"
        reputation_records_file = "resources/reputation-records/deny_reputation_records.json"
        entry_request_history_file = "resources/entry-request-history/entry_request_history.json"
        test_name = "deny"
    elif 'no_reputation_records' in sys.argv:
        entry_request_file = "resources/entry-requests/no_reputation_records_entry_request.json"
        attestation_claims_file = "resources/attestation-claims/no_reputation_records_attestation_claims.json"
        reputation_records_file = "resources/reputation-records/no_reputation_records_reputation_records.json"
        entry_request_history_file = "resources/entry-request-history/entry_request_history.json"
        test_name = "no_reputation_records"
    else:
        raise ValueError("Invalid entry request file")

    print("=" * 70)
    print(f"Processing {entry_request_file}...")
    print("=" * 70)
    print("=" * 70)

    # Example Entry Request (pilot_id/drone_id match sade-mock-data/reputation_model.json and user_input.json)
    with open(entry_request_file, "r") as f:
        request = json.load(f)
    # Adding other resources to the request
    with open(attestation_claims_file, "r") as f:
        request["attestation_claims"] = json.load(f)
    with open(reputation_records_file, "r") as f:
        request["reputation_records"] = json.load(f)
    with open(entry_request_history_file, "r") as f:
        request["entry_request_history"] = json.load(f)
    
    print("=" * 70)
    print("SADE Entry Request Processing")
    print("=" * 70)
    print("\nEntry Request:")
    print(json.dumps(request, indent=2))
    print("\n" + "=" * 70)
    print("Processing...")
    print("=" * 70 + "\n")
    
    try:
        output = await process_entry_request(request)
        
        # Convert output to evaluation API payload
        payload = to_evaluation_api_payload(
                        output,
                        request["evaluation_id"],
                        request["evaluation_series_id"],
                    )
        decision = output.get("decision", {})
        decision_type = decision.get("type", "UNKNOWN")
        sade_message = decision.get("sade_message", "")

        # Determine test number based on the loop index, if available
        # Since here we don't have direct access to loop index, let's assume
        # you want to name files like entry_result_1.txt, entry_result_2.txt, etc.
        # We'll try to infer test number based on the request or you may want to pass
        # loop index explicitly.
        #
        # To keep things robust, let's fallback to a per-request "sade_zone_id" as default.
        #
        # Recommended pattern: enumerate(example_request, start=1) in main()

        output_filename = f"results/integration/entry_result_{test_name}.txt"
        payload_filename = f"results/integration/entry_result_SADE_API_{test_name}.json"
        with open(payload_filename, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nSADE API Response Payload written to {payload_filename}")
        
        with open(output_filename, "w") as f:
            f.write("=" * 70 + "\n")
            f.write("FINAL DECISION\n")
            f.write("=" * 70 + "\n")
            f.write(f"Type: {decision_type}\n")
            f.write(f"SADE Message: {sade_message}\n")
            if decision.get("constraints"):
                f.write(f"Constraints: {decision['constraints']}\n")
            if decision.get("denial_code"):
                f.write(f"Denial Code: {decision['denial_code']}\n")
            # Always show explanation (orchestrator prompt requires it; fallback if legacy output)
            explanation = decision.get("explanation") or "(none)"
            f.write(f"Explanation: {explanation}\n")
            f.write("=" * 70 + "\n\n")
            f.write("Full output (decision + visibility):\n")
            f.write(json.dumps(output, indent=2))
            f.write("\n")
        print(f"\nOutput written to {output_filename}")

    except ValueError as e:
        print(f"\nParse error: {e}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C). Exiting.")
        sys.exit(0)
