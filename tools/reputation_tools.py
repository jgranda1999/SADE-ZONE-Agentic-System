"""
Reputation Agent Tools - Load from sade-mock-data/reputation_model.json.

Retrieves historical reputation and incident data for a DPO trio by filtering
sessions from the Reputation Model Profile (mock file). Used with entry requests
from main.py or entry_requests.json.
"""

import json
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone

from agents import function_tool
from models import (
    ReputationAgentOutput,
    Incident,
    IncidentAnalysis,
    ReputationRiskAssessment,
)

# Path to mock data (sade-mock-data next to tools/)
_MOCK_DATA_DIR = Path(__file__).resolve().parent.parent / "sade-mock-data"
_REPUTATION_MODEL_PATH = _MOCK_DATA_DIR / "reputation_model.json"


# Incident code mapping table
INCIDENT_CATEGORIES = {
    "0001": {
        "category": "Injury-Related Incidents",
        "subcategories": {
            "001": "Serious Injury",
            "010": "Loss of Consciousness"
        },
        "severity": "HIGH"
    },
    "0010": {
        "category": "Property Damage",
        "subcategories": {
            "001": "Damage > $500"
        },
        "severity": "MEDIUM"
    },
    "0011": {
        "category": "Mid-Air Collisions / Near-Misses",
        "subcategories": {
            "001": "Collision with Manned Aircraft",
            "010": "Near Mid-Air Collision (NMAC)"
        },
        "severity": "HIGH"
    },
    "0100": {
        "category": "Loss of Control / Malfunctions",
        "subcategories": {
            "001": "GPS or Navigation Failure",
            "010": "Flight Control Failure",
            "011": "Battery Failure / Fire",
            "100": "Communication Loss (C2 Link)",
            "101": "Flyaway (Uncontrolled Drone)"
        },
        "severity": "MEDIUM"
    },
    "0101": {
        "category": "Airspace Violations",
        "subcategories": {
            "001": "Unauthorized Entry into Controlled Airspace",
            "010": "Violation of Temporary Flight Restriction (TFR)",
            "011": "Overflight of People Without Waiver",
            "100": "Night Operations Without Proper Lighting"
        },
        "severity": "MEDIUM"
    },
    "0110": {
        "category": "Security & Law Enforcement Events",
        "subcategories": {
            "001": "Intercepted by Law Enforcement or Military",
            "010": "Suspected Cyberattack or GPS Jamming",
            "011": "Drone Used in Criminal Activity"
        },
        "severity": "HIGH"
    },
    "1111": {
        "category": "Incomplete Flight Log",
        "subcategories": {
            "001": "Drone did not exit zone"
        },
        "severity": "LOW"
    }
}


def parse_incident_code(incident_code: str) -> tuple[str, str, str, str]:
    """
    Parse incident code "hhhh-sss" into components.
    
    Returns:
        (high_level_code, sub_code, category, subcategory, severity)
    """
    parts = incident_code.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid incident code format: {incident_code}")
    
    high_level = parts[0]
    sub_code = parts[1]
    
    if high_level not in INCIDENT_CATEGORIES:
        return (high_level, sub_code, "Unknown", "Unknown", "LOW")
    
    cat_info = INCIDENT_CATEGORIES[high_level]
    category = cat_info["category"]
    subcategory = cat_info["subcategories"].get(sub_code, "Unknown")
    severity = cat_info["severity"]
    
    return (high_level, sub_code, category, subcategory, severity)


def _load_reputation_sessions(pilot_id: str, drone_id: str) -> List[Dict[str, Any]]:
    """
    Load sessions from sade-mock-data/reputation_model.json filtered by pilot_id and drone_id.
    Returns list of session dicts (time_in, time_out, incidents, record_type, wind_steady_kt, wind_gusts_kt, etc.).
    """
    if not _REPUTATION_MODEL_PATH.exists():
        return []
    raw = json.loads(_REPUTATION_MODEL_PATH.read_text())
    if not isinstance(raw, list):
        return []
    return [
        s for s in raw
        if s.get("pilot_id") == pilot_id and s.get("drone_id") == drone_id
    ]


def _retrieve_reputations_impl(
    pilot_id: str,
    org_id: str,
    drone_id: str,
    entry_time: str | None = None,
) -> ReputationAgentOutput:
    """
    Retrieve historical trust signals for a DPO trio from sade-mock-data/reputation_model.json.

    Sessions are filtered by pilot_id and drone_id. Entry_time is used as the
    reference date for "recent" incident count (last 30 days).

    Args:
        pilot_id: Pilot identifier (e.g. PILOT-12345)
        org_id: Organization identifier (not in JSON; kept for API shape)
        drone_id: Drone identifier (e.g. DRONE-XYZ-001), matches drone_id in JSON
        entry_time: Optional ISO8601 datetime for recent-incident window (default 2026-01-26)

    Returns:
        ReputationAgentOutput with reputation summary, incident analysis, and risk assessment
    """
    sessions = _load_reputation_sessions(pilot_id, drone_id)

    # Collect all incidents from sessions (one incident record per session per code)
    all_incidents = []
    incident_codes_seen: set[str] = set()

    for session in sessions:
        for incident_code in session.get("incidents", []):
            if incident_code not in incident_codes_seen:
                incident_codes_seen.add(incident_code)
                high_level, sub_code, category, subcategory, severity = parse_incident_code(incident_code)
                # Resolved if any session has record_type "010" (follow-up) for this incident
                resolved = any(
                    s.get("record_type") == "010" and incident_code in s.get("incidents", [])
                    for s in sessions
                )
                incident = Incident(
                    incident_code=incident_code,
                    incident_category=category,
                    incident_subcategory=subcategory,
                    severity=severity,
                    resolved=resolved,
                    session_id=session["session_id"],
                    date=session["time_in"],
                )
                all_incidents.append(incident)

    # Recent incidents: relative to entry_time or default reference
    if entry_time:
        try:
            ref_str = entry_time.replace("Z", "+00:00") if entry_time.endswith("Z") else entry_time
            reference_date = datetime.fromisoformat(ref_str)
        except (ValueError, TypeError):
            reference_date = datetime(2026, 1, 26, tzinfo=timezone.utc)
    else:
        reference_date = datetime(2026, 1, 26, tzinfo=timezone.utc)
    thirty_days_ago = reference_date - timedelta(days=30)
    
    recent_count = 0
    for inc in all_incidents:
        inc_date_str = inc.date.replace("Z", "+00:00") if inc.date.endswith("Z") else inc.date
        inc_date = datetime.fromisoformat(inc_date_str)
        if inc_date.tzinfo is None and reference_date.tzinfo is not None:
            inc_date = inc_date.replace(tzinfo=timezone.utc)
        if inc_date >= thirty_days_ago:
            recent_count += 1
    
    # Determine unresolved incidents
    unresolved_present = any(not inc.resolved for inc in all_incidents)
    
    incident_analysis = IncidentAnalysis(
        incidents=all_incidents,
        unresolved_incidents_present=unresolved_present,
        total_incidents=len(all_incidents),
        recent_incidents_count=recent_count
    )
    
    # Risk assessment based on incidents (no reputation_summary; decision from full model only)
    risk_level = "LOW"
    blocking_factors = []
    confidence_factors = []

    if unresolved_present:
        high_severity_unresolved = any(
            not inc.resolved and inc.severity == "HIGH"
            for inc in all_incidents
        )
        if high_severity_unresolved:
            risk_level = "HIGH"
            blocking_factors.append("unresolved_high_severity_incident")
        else:
            risk_level = "MEDIUM"
            blocking_factors.append("unresolved_incidents_present")

    if recent_count == 0:
        confidence_factors.append("no_recent_incidents")

    if all(inc.resolved for inc in all_incidents):
        confidence_factors.append("all_incidents_resolved")

    risk_assessment = ReputationRiskAssessment(
        risk_level=risk_level,
        blocking_factors=blocking_factors,
        confidence_factors=confidence_factors
    )

    # v2 orchestration fields from sessions (reputation_model.json)
    def _parse_wind(s: Any) -> float:
        if s is None:
            return 0.0
        try:
            return float(str(s).strip())
        except (ValueError, TypeError):
            return 0.0

    drp_sessions_count = len(sessions)
    demo_steady_max_kt = max(
        (_parse_wind(s.get("wind_steady_kt")) for s in sessions),
        default=0.0,
    )
    demo_gust_max_kt = max(
        (_parse_wind(s.get("wind_gusts_kt")) for s in sessions),
        default=0.0,
    )
    incident_codes = [
        code for s in sessions for code in s.get("incidents", [])
    ]
    n_0100_0101 = sum(
        1 for code in incident_codes
        if "-" in code and code.split("-")[0] in ("0100", "0101")
    )
    recommendation = risk_level
    why = [
        f"drp_sessions_count={drp_sessions_count}",
        f"demo_steady_max_kt={demo_steady_max_kt}",
        f"demo_gust_max_kt={demo_gust_max_kt}",
        f"n_0100_0101={n_0100_0101}",
        f"unresolved_incidents_present={unresolved_present}",
    ]
    if incident_codes:
        prefixes = list(dict.fromkeys(c.split("-")[0] for c in incident_codes if "-" in c))
        why.append(f"incident_prefixes_present={prefixes[:10]}")

    recommendation_prose = (
        f"Historical risk signal: {risk_level}. Sessions={drp_sessions_count}, "
        f"demo wind envelope steady={demo_steady_max_kt} kt gust={demo_gust_max_kt} kt; "
        f"n_0100_0101={n_0100_0101}, unresolved_incidents_present={unresolved_present}."
    )
    why_prose = "; ".join(why[:8])

    return ReputationAgentOutput(
        incident_analysis=incident_analysis,
        risk_assessment=risk_assessment,
        drp_sessions_count=drp_sessions_count,
        demo_steady_max_kt=demo_steady_max_kt,
        demo_gust_max_kt=demo_gust_max_kt,
        incident_codes=incident_codes,
        n_0100_0101=n_0100_0101,
        recommendation=recommendation,
        recommendation_prose=recommendation_prose,
        why_prose=why_prose,
        why=why[:8],
    )


@function_tool
def retrieve_reputations(input_json: str) -> ReputationAgentOutput:
    """
    Retrieve historical trust signals for a Drone|Pilot|Organization trio from
    sade-mock-data/reputation_model.json. Sessions are filtered by pilot_id and drone_id.

    Args:
        input_json: JSON string with pilot_id, org_id, drone_id, entry_time, request

    Returns:
        ReputationAgentOutput with reputation summary, incident analysis, and risk assessment
    """
    data = json.loads(input_json)
    return _retrieve_reputations_impl(
        pilot_id=data["pilot_id"],
        org_id=data["org_id"],
        drone_id=data["drone_id"],
        entry_time=data.get("entry_time"),
    )
