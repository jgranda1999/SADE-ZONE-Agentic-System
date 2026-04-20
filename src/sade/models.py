"""
Pydantic models for SADE agent outputs.

This file intentionally groups models in three layers:
1) Sub-agent input contracts (what orchestrator sends to each sub-agent)
2) Sub-agent output contracts (what each sub-agent returns)
3) Orchestrator output contract (final decision + visibility payload)

Keeping these contracts explicit in one place helps prompt text, tool descriptions,
and runtime validation stay aligned as schemas evolve.
"""

from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Environment Agent Models
# ============================================================================
# NOTE: Env input is intentionally minimal to avoid passing full entry requests.
class EnvInputUAV(BaseModel):
    """UAV identity block from entry request."""
    drone_id: str
    model_id: str
    owner_id: str


class EnvInputUAVModel(BaseModel):
    """UAV model capability block from entry request."""
    model_id: str
    name: str
    max_wind_tolerance_m_s: float = Field(
        ..., description="Manufacturer max wind tolerance for operations, meters per second."
    )
    max_temp_f: float
    min_temp_f: float
    max_payload_cap_kg: float


class EnvInputWeatherForecast(BaseModel):
    """Weather forecast block from entry request."""
    sade_zone_id: str
    window_start: str = Field(..., description="ISO8601 datetime string")
    window_end: str = Field(..., description="ISO8601 datetime string")
    max_wind_m_s: float = Field(..., description="Forecast steady wind, meters per second.")
    max_gust_m_s: float = Field(..., description="Forecast wind gusts, meters per second.")
    min_temp_f: float
    max_temp_f: float
    precipitation_summary: str
    visibility_min_m: float = Field(..., description="Minimum visibility, meters.")
    source: str
    confidence: float
    generated_at: str = Field(..., description="ISO8601 datetime string")


class EnvironmentAgentInput(BaseModel):
    """Minimal input contract passed from orchestrator to environment agent."""
    payload: str = Field(..., description="Stringified payload mass in kilograms.")
    uav: EnvInputUAV
    uav_model: EnvInputUAVModel
    weather_forecast: EnvInputWeatherForecast


# ============================================================================
# Reputation Agent Input Models
# ============================================================================
# NOTE: Reputation input is intentionally narrowed to historical records only.
class ReputationAgentInput(BaseModel):
    """Minimal input contract passed from orchestrator to reputation agent."""
    reputation_records: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Claims Agent Input Models
# ============================================================================
class ClaimsInputPilot(BaseModel):
    """Pilot identity block passed to claims agent."""
    pilot_id: str
    organization_id: str


class ClaimsInputWindContext(BaseModel):
    """Wind context passed from orchestrator state to claims agent."""
    wind_now_m_s: float
    gust_now_m_s: float
    demo_steady_max_m_s: float
    demo_gust_max_m_s: float


class ClaimsInputPayloadContext(BaseModel):
    """Payload context from orchestrator STATE 2 (requested mass + reputation + effective cap)."""

    payload_kg: Optional[float] = None
    demo_payload_max_kg: Optional[float] = None
    payload_cap_kg: Optional[float] = None


class ClaimsAgentInput(BaseModel):
    """Minimal input contract passed from orchestrator to claims agent (matches v5 orchestrator claims JSON)."""
    action_id: str
    requested_entry_time: str = Field(..., description="ISO8601 datetime string")
    pilot: ClaimsInputPilot
    uav: EnvInputUAV
    required_actions: List[str] = Field(default_factory=list)
    incident_codes: List[str] = Field(default_factory=list)
    wind_context: ClaimsInputWindContext
    payload_context: ClaimsInputPayloadContext
    attestation_claims: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Environment Agent Output Models
# ============================================================================
class ManufacturerFC(BaseModel):
    """Manufacturer flight constraints."""
    manufacturer: str
    model: str
    category: str
    mfc_payload_max_kg: float
    mfc_max_wind_m_s: float = Field(
        ..., description="Manufacturer max wind for operations, meters per second."
    )

class SpatialConstraints(BaseModel):
    """Spatial constraints from environment data."""
    airspace_class: Optional[str] = None
    no_fly_zones: List[str] = Field(default_factory=list)
    restricted_areas: List[str] = Field(default_factory=list)


class RawConditions(BaseModel):
    """Raw environmental conditions."""
    wind: float = Field(..., description="Steady wind, meters per second.")
    wind_gust: float = Field(..., description="Wind gusts, meters per second.")
    precipitation: Literal["none", "light", "moderate", "heavy"]
    visibility: Optional[float] = Field(None, description="Visibility, meters (from forecast).")
    light_conditions: Literal["daylight", "dusk", "dawn", "night"]
    spatial_constraints: SpatialConstraints


class RiskAssessment(BaseModel):
    """Risk assessment with factors."""
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    blocking_factors: List[str] = Field(default_factory=list)
    marginal_factors: List[str] = Field(default_factory=list)


class EnvironmentAgentOutput(BaseModel):
    """Output from Environment Agent (risk signals + evidence fields for orchestrator)."""
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
# NOTE: These are outputs consumed by orchestrator state machine rules.

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
    """Output from Reputation Agent (deterministic historical risk signals)."""
    incident_analysis: IncidentAnalysis
    risk_assessment: ReputationRiskAssessment
    drp_sessions_count: int = 0
    demo_steady_max_m_s: float = 0.0
    demo_gust_max_m_s: float = 0.0
    demo_payload_max_kg: float = 0.0
    incident_codes: List[str] = Field(default_factory=list)
    n_0100_0101: int = 0
    recommendation_prose: str = ""
    recommendation: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


# ============================================================================
# Action Required Agent Models (Evidence/Attestation)
# ============================================================================
# NOTE: Kept for shared schema compatibility even if action-required flow is
# currently handled through claims/evidence requirement contracts.

class EvidenceSubject(BaseModel):
    """Subject of evidence requirement/attestation."""
    sade_zone_id: str
    pilot_id: str
    organization_id: str
    drone_id: str


class EvidenceRequirementParamObject(BaseModel):
    """Structured param object for ``EvidenceRequirement.params`` (strict JSON Schema)."""
    model_config = ConfigDict(extra="ignore")

    prefix: Optional[str] = None
    incident_code: Optional[str] = None
    incident_codes: Optional[List[str]] = None
    key: Optional[str] = None
    value: Optional[str] = None


class EvidenceRequirement(BaseModel):
    """Single evidence requirement."""
    requirement_id: str
    expr: str
    keyword: str
    applicable_scopes: List[Literal["PILOT", "UAV"]] = Field(default_factory=list)
    params: List[Union[str, EvidenceRequirementParamObject]] = Field(
        default_factory=list,
        description="Empty list, string tokens, or small structured objects (prefix/incident_code/etc.).",
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
    params: List[Union[str, EvidenceRequirementParamObject]] = Field(
        default_factory=list,
        description="Empty list, string tokens, or structured param objects.",
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
    evidence_requirement_spec: Optional[EvidenceRequirementPayload] = None
    recommendation_prose: str = ""
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


# ============================================================================
# Orchestrator Output (decision + visibility contract)
# ============================================================================
# Visibility embeds full sub-agent outputs so downstream systems can audit
# exactly which evidence/signals were used for the final decision.


class ClaimsAgentVisibility(BaseModel):
    """Claims agent in visibility: called flag + full ClaimsAgentOutput when called."""
    called: bool = False
    satisfied: bool = False
    resolved_incident_prefixes: List[str] = Field(default_factory=list)
    unresolved_incident_prefixes: List[str] = Field(default_factory=list)
    satisfied_actions: List[str] = Field(default_factory=list)
    unsatisfied_actions: List[str] = Field(default_factory=list)
    evidence_requirement_spec: Optional[EvidenceRequirementPayload] = None
    recommendation_prose: str = ""
    why_prose: str = ""
    why: List[str] = Field(default_factory=list)


class Visibility(BaseModel):
    """Visibility payload returned by orchestrator."""
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
    evidence_requirement_spec: Optional[EvidenceRequirementPayload] = None
    denial_code: Optional[str] = None
    explanation: str = ""


class OrchestratorOutput(BaseModel):
    """Orchestrator final output (decision + visibility). Matches v3 prompt OUTPUT CONTRACT."""
    decision: Decision
    visibility: Visibility
