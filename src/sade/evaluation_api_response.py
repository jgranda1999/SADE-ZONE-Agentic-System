"""
Map internal orchestrator output (decision + visibility) to the external evaluation API JSON shape.

Internal decision types use hyphens (e.g. APPROVED-CONSTRAINTS); the API uses underscores
(APPROVED_CONSTRAINTS). ``decision.explanation`` is used as ``result.reason``.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

# Internal orchestrator Decision.type -> external API result.decision
_INTERNAL_TO_API_DECISION: Dict[str, str] = {
    "APPROVED": "APPROVED",
    "APPROVED-CONSTRAINTS": "APPROVED_CONSTRAINTS",
    "ACTION-REQUIRED": "ACTION_REQUIRED",
    "DENIED": "DENIED",
}

_DEFAULT_PROCESSING_FAILED_REASON = "Decision Maker could not process this evaluation."


def utc_now_z() -> str:
    """Current UTC time as ISO8601 with Z suffix (no sub-second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_number(s: str) -> Union[int, float]:
    v = float(s)
    if v.is_integer():
        return int(v)
    return v


def parse_constraint_to_api_item(constraint: str) -> Dict[str, Any]:
    """
    Parse a single internal constraint string into ``{"code": ..., "value": ...}``.

    Known patterns:
    - ``MAX_ALTITUDE(30m)`` -> MAX_ALTITUDE_M + numeric meters
    - ``SPEED_LIMIT(7m/s)`` -> SPEED_LIMIT_MPS + numeric m/s
    - ``PAYLOAD_MARGIN_CAUTION`` / ``DAYLIGHT_ONLY`` -> boolean true

    Anything else becomes ``{"code": "RAW", "value": "<original string>"}``.
    """
    s = (constraint or "").strip()
    if not s:
        return {"code": "RAW", "value": ""}

    m = re.match(r"^MAX_ALTITUDE\((\d+(?:\.\d+)?)m\)$", s, re.IGNORECASE)
    if m:
        return {"code": "MAX_ALTITUDE_M", "value": _coerce_number(m.group(1))}

    m = re.match(r"^SPEED_LIMIT\((\d+(?:\.\d+)?)m/s\)$", s, re.IGNORECASE)
    if m:
        return {"code": "SPEED_LIMIT_MPS", "value": _coerce_number(m.group(1))}

    if re.match(r"^PAYLOAD_MARGIN_CAUTION$", s):
        return {"code": "PAYLOAD_MARGIN_CAUTION", "value": True}

    if re.match(r"^DAYLIGHT_ONLY$", s, re.IGNORECASE):
        return {"code": "DAYLIGHT_ONLY", "value": True}

    return {"code": "RAW", "value": s}


def constraints_to_api_items(constraints: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Convert internal string constraints to API objects."""
    if not constraints:
        return []
    return [parse_constraint_to_api_item(c) for c in constraints]


def normalize_evidence_requirement_spec(
    spec: Optional[Dict[str, Any]],
    evaluation_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Return a copy of the evidence requirement spec with ``request_id`` set to ``evaluation_id``.

    Downstream expects the bare evaluation UUID; orchestrator output may use ``ACT-<uuid>``.
    """
    if spec is None:
        return None
    out = copy.deepcopy(spec)
    out["request_id"] = evaluation_id
    return out


def to_evaluation_api_payload(
    orchestrator_output: Dict[str, Any],
    evaluation_id: str,
    evaluation_series_id: str,
    *,
    completed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the successful evaluation API response body (includes ``result``).

    Args:
        orchestrator_output: Parsed orchestrator dict with ``decision`` (and typically ``visibility``).
        evaluation_id: Evaluation UUID string.
        evaluation_series_id: Evaluation series UUID string.
        completed_at: ISO8601 Z timestamp; if omitted, current UTC time is used.
    """
    decision = orchestrator_output.get("decision") or {}
    internal_type = decision.get("type")
    if internal_type not in _INTERNAL_TO_API_DECISION:
        raise ValueError(f"Unknown decision type: {internal_type!r}")

    api_decision = _INTERNAL_TO_API_DECISION[internal_type]
    reason = decision.get("explanation") or ""
    constraints = constraints_to_api_items(decision.get("constraints"))

    evidence_spec: Optional[Dict[str, Any]] = None
    raw_spec = decision.get("evidence_requirement_spec")
    if isinstance(raw_spec, dict):
        evidence_spec = normalize_evidence_requirement_spec(raw_spec, evaluation_id)
    elif raw_spec is not None:
        # Defensive: coerce via JSON round-trip if a non-dict sneaks in
        try:
            as_dict = json.loads(json.dumps(raw_spec, default=str))
            if isinstance(as_dict, dict):
                evidence_spec = normalize_evidence_requirement_spec(as_dict, evaluation_id)
        except (TypeError, ValueError):
            evidence_spec = None

    when = completed_at if completed_at is not None else utc_now_z()

    return {
        "evaluation_id": evaluation_id,
        "evaluation_series_id": evaluation_series_id,
        "completed_at": when,
        "result": {
            "decision": api_decision,
            "reason": reason,
            "constraints": constraints,
            "evidence_requirement_spec": evidence_spec,
        },
    }


def build_processing_failed_response(
    evaluation_id: str,
    evaluation_series_id: str,
    *,
    completed_at: Optional[str] = None,
    reason: str = _DEFAULT_PROCESSING_FAILED_REASON,
) -> Dict[str, Any]:
    """Catch-all response when orchestration or validation fails before a final decision exists."""
    when = completed_at if completed_at is not None else utc_now_z()
    return {
        "evaluation_id": evaluation_id,
        "evaluation_series_id": evaluation_series_id,
        "completed_at": when,
        "processing_failed": True,
        "reason": reason,
    }
