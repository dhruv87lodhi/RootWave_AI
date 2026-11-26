from typing import List, Optional
from pydantic import BaseModel, Field


class IncidentContext(BaseModel):
    incident_id: str
    title: str
    severity: str
    affected_service: str
    reported_window: str
    clock_skew_notes: str


class ReadinessVerdict(BaseModel):
    status: str = Field(..., description="READY, PARTIALLY_READY, or BLOCKED")
    can_dispatch_workers: bool
    quality_warnings: List[str] = Field(default_factory=list)


class EvidenceOverview(BaseModel):
    total_sources_available: int
    services_covered: List[str]
    telemetry_types: List[str]


class AvailableEvidence(BaseModel):
    evidence_id: str
    source_name: str
    type: str
    service: str
    summary: str


class KeyObservation(BaseModel):
    timestamp: str
    observation: str
    evidence_id: str


class InvestigationConstraints(BaseModel):
    max_concurrent_hypotheses: int = 3
    token_budget: int = 10000


class IncidentSummary(BaseModel):
    incident_context: IncidentContext
    readiness_verdict: ReadinessVerdict
    evidence_overview: EvidenceOverview
    available_evidence: List[AvailableEvidence]
    key_observations: List[KeyObservation]
    evidence_gaps: List[str] = Field(default_factory=list)
    investigation_constraints: InvestigationConstraints
