"""
Pydantic models for SADE agent outputs.

These models define the structured output types for all agents,
ensuring type safety and automatic validation.
"""

from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, Field


# ============================================================================
# Environment Agent Models
# ============================================================================
class ManufacturerFC(BaseModel):
    """Manufacturer flight constraints."""
    manufacturer: str
    model: str
    category: str
    mfc_payload_max_kg: float
    mfc_max_wind_kt: float

class SpatialConstraints(BaseModel):
    """Spatial constraints from environment data."""
    airspace_class: Optional[str] = None
    no_fly_zones: List[str] = Field(default_factory=list)
    restricted_areas: List[str] = Field(default_factory=list)


class RawConditions(BaseModel):
    """Raw environmental conditions."""
    wind: float
    wind_gust: float
    precipitation: Literal["none", "light", "moderate", "heavy"]
    visibility: Optional[float] = None
    light_conditions: Literal["daylight", "dusk", "dawn", "night"]
    spatial_constraints: SpatialConstraints


class RiskAssessment(BaseModel):
    """Risk assessment with factors."""
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    blocking_factors: List[str] = Field(default_factory=list)
    marginal_factors: List[str] = Field(default_factory=list)


class EnvironmentAgentOutput(BaseModel):
    """Output from Environment Agent (v2: includes recommendation, why for orchestrator)."""
    manufacturer_fc: ManufacturerFC
    raw_conditions: RawConditions
    risk_assessment: RiskAssessment
    constraint_suggestions_wind: List[str] = Field(default_factory=list)
    constraint_suggestions_payload: List[str] = Field(default_factory=list)
    recommendation_wind: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    recommendation_payload: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    recommendation_prose_wind: str = ""
    recommendation_prose_payload: str = ""
    why_prose_wind: str = ""
    why_prose_payload: str = ""
    why_wind: List[str] = Field(default_factory=list)
    why_payload: List[str] = Field(default_factory=list)


# ============================================================================
# Reputation Agent Models
# ============================================================================

class Incident(BaseModel):
    """Incident record."""
    incident_code: str = Field(..., description="Format: hhhh-sss")
    incident_category: str
    incident_subcategory: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    resolved: bool
    session_id: str
    date: str = Field(..., description="ISO8601 datetime string")


class IncidentAnalysis(BaseModel):
    """Analysis of incidents."""
    incidents: List[Incident] = Field(default_factory=list)
    unresolved_incidents_present: bool
    total_incidents: int
    recent_incidents_count: int


class ReputationRiskAssessment(BaseModel):
    """Risk assessment from reputation data."""
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    blocking_factors: List[str] = Field(default_factory=list)
    confidence_factors: List[str] = Field(default_factory=list)


class ReputationAgentOutput(BaseModel):
    """Output from Reputation Agent (orchestration fields; no reputation_summary to avoid bias)."""
    incident_analysis: IncidentAnalysis
    risk_assessment: ReputationRiskAssessment
    drp_sessions_count: int = 0
    demo_steady_max_kt: float = 0.0
    demo_gust_max_kt: float = 0.0
    incident_codes: List[str] = Field(default_factory=list)
    n_0100_0101: int = 0
    recommendation_prose: str = ""
    recommendation: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


# ============================================================================
# Action Required Agent Models (Evidence/Attestation)
# ============================================================================

class EvidenceSubject(BaseModel):
    """Subject of evidence requirement/attestation."""
    sade_zone_id: str
    pilot_id: str
    organization_id: str
    drone_id: str


class EvidenceRequirement(BaseModel):
    """Single evidence requirement."""
    expr: str
    keyword: str
    params: List[Union[str, Dict[str, str]]] = Field(
        default_factory=list,
        description="Can be empty list, list of strings, or list of {'key': 'value'} dicts"
    )


class EvidenceCategory(BaseModel):
    """Category of evidence requirements."""
    category: Literal["CERTIFICATION", "CAPABILITY", "ENVIRONMENT", "INTERFACE"]
    requirements: List[EvidenceRequirement]


class EvidenceRequirementPayload(BaseModel):
    """Complete evidence requirement payload."""
    type: Literal["EVIDENCE_REQUIREMENT"] = "EVIDENCE_REQUIREMENT"
    spec_version: str
    request_id: str
    subject: EvidenceSubject
    categories: List[EvidenceCategory]


class RequirementMeta(BaseModel):
    """Metadata for a requirement in an attestation."""
    status: Literal["SATISFIED", "PARTIAL", "NOT_SATISFIED", "UNKNOWN"]
    # Additional fields may be present but are opaque
    # Using Dict[str, Any] to allow extra fields
    class Config:
        extra = "allow"


class AttestedRequirement(BaseModel):
    """A requirement in an attestation."""
    expr: str
    keyword: str
    params: List[Union[str, Dict[str, str]]] = Field(
        default_factory=list,
        description="Can be empty list, list of strings, or list of {'key': 'value'} dicts"
    )
    meta: RequirementMeta


class AttestationCategory(BaseModel):
    """Category in an attestation."""
    category: str
    requirements: List[AttestedRequirement]


class Signature(BaseModel):
    """Digital signature (opaque)."""
    signer: str
    signature_type: str
    signature_ref: str


class EvidenceRef(BaseModel):
    """Evidence reference (opaque)."""
    evidence_id: str
    kind: str
    ref: str


class EvidenceAttestation(BaseModel):
    """Complete evidence attestation payload."""
    type: Literal["EVIDENCE_ATTESTATION"] = "EVIDENCE_ATTESTATION"
    spec_version: str
    attestation_id: str
    in_response_to: str
    subject: EvidenceSubject
    categories: List[AttestationCategory]
    signatures: List[Signature] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)


class ActionRequiredAgentOutput(BaseModel):
    """Output from Action Required Agent."""
    satisfied: bool
    attestation: Optional[EvidenceAttestation] = None
    error: Optional[str] = None


# ============================================================================
# Claims Agent Models (v2 — required_actions verification)
# ============================================================================

class ClaimsAgentOutput(BaseModel):
    """Output from Claims Agent. Verifies required_actions against DPO claims/follow-ups."""
    satisfied: bool
    resolved_incident_prefixes: List[str] = Field(default_factory=list)
    unresolved_incident_prefixes: List[str] = Field(default_factory=list)
    satisfied_actions: List[str] = Field(default_factory=list)
    unsatisfied_actions: List[str] = Field(default_factory=list)
    recommendation_prose: str = ""
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


# ============================================================================
# Orchestrator Output (decision + visibility contract)
# ============================================================================
# Visibility uses the same Pydantic contracts as sub-agent outputs:
# environment_agent -> EnvironmentAgentOutput, reputation_agent -> ReputationAgentOutput,
# claims_agent -> wrapper with called + ClaimsAgentOutput (when called).

class EntryRequestVisibility(BaseModel):
    """Visibility copy of the entry request (must match input field names)."""
    sade_zone_id: str
    pilot_id: str
    organization_id: str
    drone_id: str
    payload: str
    requested_entry_time: str
    request_type: str


class ClaimsAgentVisibility(BaseModel):
    """Claims agent in visibility: called flag + full ClaimsAgentOutput when called."""
    called: bool = False
    satisfied: bool = False
    resolved_incident_prefixes: List[str] = Field(default_factory=list)
    unresolved_incident_prefixes: List[str] = Field(default_factory=list)
    satisfied_actions: List[str] = Field(default_factory=list)
    unsatisfied_actions: List[str] = Field(default_factory=list)
    recommendation_prose: str = ""
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


class Visibility(BaseModel):
    """Full visibility: entry_request copy + full sub-agent output contracts."""
    entry_request: EntryRequestVisibility
    environment_agent: EnvironmentAgentOutput
    reputation_agent: ReputationAgentOutput
    claims_agent: ClaimsAgentVisibility
    rule_trace: List[str] = Field(default_factory=list)


class Decision(BaseModel):
    """Decision object in orchestrator output."""
    type: Literal["APPROVED", "APPROVED-CONSTRAINTS", "ACTION-REQUIRED", "DENIED"]
    sade_message: str
    constraints: List[str] = Field(default_factory=list)
    action_id: Optional[str] = None
    actions: List[str] = Field(default_factory=list)
    denial_code: Optional[str] = None
    explanation: str = ""


class OrchestratorOutput(BaseModel):
    """Orchestrator final output (decision + visibility). Matches v3 prompt OUTPUT CONTRACT."""
    decision: Decision
    visibility: Visibility
