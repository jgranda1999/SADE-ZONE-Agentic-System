"""
Claims Agent Tools - v2 orchestration.

Retrieves and verifies DPO claims/follow-ups against required_actions
using data from sade-mock-data/ (user_input.json for incident status).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from agents import function_tool

from models import ClaimsAgentOutput

# Path to mock data (sade-mock-data next to agentic-sade-dev/tools)
_MOCK_DATA_DIR = Path(__file__).resolve().parent.parent / "sade-mock-data"
_USER_INPUT_PATH = _MOCK_DATA_DIR / "user_input.json"

# High / medium severity incident prefix families (from v2 prompts)
HIGH_SEVERITY_PREFIXES = {"0001", "0011", "0110"}
PREFIX_0100_0101 = {"0100", "0101"}


def _load_user_claims(drone_id: str) -> List[Dict[str, Any]]:
    """Load DPO claims/follow-up records from sade-mock-data/user_input.json, filtered by drone."""
    if not _USER_INPUT_PATH.exists():
        return []
    raw = json.loads(_USER_INPUT_PATH.read_text())
    if not isinstance(raw, list):
        return []
    # Filter by drone (field is "drone_id" in the JSON)
    records = [r for r in raw if r.get("drone_id") == drone_id]
    # Sort by date (MM/DD/YYYY)
    def parse_date(r: Dict[str, Any]) -> datetime:
        s = r.get("date", "")
        try:
            return datetime.strptime(s, "%m/%d/%Y")
        except (ValueError, TypeError):
            return datetime.min

    records.sort(key=parse_date)
    return records


def _align_incident_resolution(
    incident_codes: List[str],
    user_records: List[Dict[str, Any]],
) -> tuple[List[str], List[str]]:
    """
    Align incident_codes (hhhh-sss) with user_input records by order (chronological).
    Returns (resolved_prefixes, unresolved_prefixes) where prefix = hhhh.
    A prefix is resolved if at least one aligned record has status "Resolved".
    """
    resolved_set: set[str] = set()
    unresolved_list: List[str] = []

    for i, code in enumerate(incident_codes):
        if "-" not in code:
            continue
        prefix = code.split("-")[0]
        if i < len(user_records) and user_records[i].get("status") == "Resolved":
            resolved_set.add(prefix)
        else:
            unresolved_list.append(prefix)

    resolved_prefixes = list(dict.fromkeys(resolved_set))
    unresolved_prefixes = [p for p in dict.fromkeys(unresolved_list) if p not in resolved_set]
    return resolved_prefixes, unresolved_prefixes


def _retrieve_claims_impl(
    action_id: str,
    pilot_id: str,
    org_id: str,
    drone_id: str,
    entry_time: str,
    required_actions: List[str],
    incident_codes: List[str],
    wind_context: Dict[str, Any],
) -> ClaimsAgentOutput:
    """
    Verify required_actions using sade-mock-data/user_input.json and wind_context.
    """
    user_records = _load_user_claims(drone_id)
    resolved_prefixes, unresolved_prefixes = _align_incident_resolution(incident_codes, user_records)
    all_prefixes = list(dict.fromkeys(
        code.split("-")[0] for code in incident_codes if "-" in code
    ))

    satisfied_actions: List[str] = []
    unsatisfied_actions: List[str] = []
    why_list: List[str] = []

    for action in required_actions:
        if action == "RESOLVE_HIGH_SEVERITY_INCIDENTS":
            high_resolved = [p for p in resolved_prefixes if p in HIGH_SEVERITY_PREFIXES]
            high_any = [p for p in all_prefixes if p in HIGH_SEVERITY_PREFIXES]
            if high_any and not high_resolved:
                unsatisfied_actions.append(action)
                why_list.append("high-severity incident(s) lack verified follow-up in user_input")
            elif high_any:
                satisfied_actions.append(action)
                why_list.append(f"verified follow-up for high-severity prefix(es) {high_resolved} (from sade-mock-data)")
            else:
                satisfied_actions.append(action)
                why_list.append("no high-severity incidents in incident_codes")
        elif action == "SUBMIT_REQUIRED_FOLLOWUP_REPORTS":
            if len(user_records) >= len(incident_codes):
                # All incidents have a record; satisfaction depends on status (handled by resolved/unresolved)
                missing = [p for p in all_prefixes if p in unresolved_prefixes]
                if missing:
                    unsatisfied_actions.append(action)
                    why_list.append(f"follow-up not resolved for prefix(es) {missing} (sade-mock-data status)")
                else:
                    satisfied_actions.append(action)
                    why_list.append(f"follow-up reports found for {len(incident_codes)} incident(s) in user_input")
            else:
                unsatisfied_actions.append(action)
                why_list.append(f"user_input has {len(user_records)} records for {len(incident_codes)} incident(s); missing follow-ups")
        elif action in (
            "RESOLVE_0100_0101_INCIDENTS_AND_MITIGATE_WIND_RISK",
            "RESOLVE_PATTERN_OF_0100_0101",
        ):
            p0100_0101 = [p for p in all_prefixes if p in PREFIX_0100_0101]
            resolved_0100_0101 = [p for p in resolved_prefixes if p in PREFIX_0100_0101]
            if p0100_0101 and set(resolved_0100_0101) != set(p0100_0101):
                unsatisfied_actions.append(action)
                why_list.append(f"0100/0101 incidents not all resolved in user_input: resolved {resolved_0100_0101}")
            elif p0100_0101:
                satisfied_actions.append(action)
                why_list.append(f"0100/0101 incidents resolved or mitigated: {resolved_0100_0101} (sade-mock-data)")
            else:
                satisfied_actions.append(action)
                why_list.append("no 0100/0101 incidents in incident_codes")
        elif action == "PROVE_WIND_CAPABILITY":
            wind_now = wind_context.get("wind_now_kt")
            gust_now = wind_context.get("gust_now_kt")
            demo_steady = wind_context.get("demo_steady_max_kt")
            demo_gust = wind_context.get("demo_gust_max_kt")
            if wind_now is not None and gust_now is not None and demo_steady is not None and demo_gust is not None:
                if wind_now <= demo_steady and gust_now <= demo_gust:
                    satisfied_actions.append(action)
                    why_list.append("wind within demonstrated envelope (wind_context)")
                else:
                    unsatisfied_actions.append(action)
                    why_list.append("wind exceeds demonstrated envelope; no proof record")
            else:
                unsatisfied_actions.append(action)
                why_list.append("wind_context missing; cannot verify wind capability")
        else:
            satisfied_actions.append(action)
            why_list.append(f"action '{action}' checked (mock)")

    satisfied = len(unsatisfied_actions) == 0
    recommendation_prose = (
        "All required actions satisfied." if satisfied
        else f"Unsatisfied: {unsatisfied_actions}; resolved prefixes: {resolved_prefixes}."
    )
    why_prose = "; ".join(why_list[:10])

    return ClaimsAgentOutput(
        satisfied=satisfied,
        resolved_incident_prefixes=resolved_prefixes,
        unresolved_incident_prefixes=unresolved_prefixes,
        satisfied_actions=satisfied_actions,
        unsatisfied_actions=unsatisfied_actions,
        recommendation_prose=recommendation_prose,
        why_prose=why_prose,
        why=why_list[:10],
    )


@function_tool
def retrieve_claims(input_json: str) -> ClaimsAgentOutput:
    """
    Retrieve and verify DPO claims/follow-ups against required_actions.

    Args:
        input_json: JSON string with action_id, pilot_id, org_id, drone_id,
                    entry_time, required_actions, incident_codes, wind_context

    Returns:
        ClaimsAgentOutput with satisfied, resolved/unresolved incident prefixes,
        satisfied/unsatisfied_actions, and why.
    """
    data = json.loads(input_json)
    return _retrieve_claims_impl(
        action_id=data.get("action_id", ""),
        pilot_id=data["pilot_id"],
        org_id=data["org_id"],
        drone_id=data["drone_id"],
        entry_time=data["entry_time"],
        required_actions=data.get("required_actions", []),
        incident_codes=data.get("incident_codes", []),
        wind_context=data.get("wind_context", {}),
    )
