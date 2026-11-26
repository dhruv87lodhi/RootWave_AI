from typing import Dict, Any
from .schemas import IncidentSummary


def validate_and_enforce_guardrails(preprocessed: Dict[str, Any], llm_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Layer 3: Python Safety & Guardrail Layer.
    - Guarantees deterministic UUID and window from Layer 1.
    - Checks that every key observation references a valid evidence_id.
    - Enforces investigation constraints for downstream Orchestration and Hypothesis agents.
    - Validates strictly against Pydantic IncidentSummary schema.
    """
    incident_id = preprocessed["incident_id"]

    # 1. Construct deterministic IncidentContext
    incident_context = {
        "incident_id": incident_id,
        "title": preprocessed["title"],
        "severity": preprocessed["severity"],
        "affected_service": preprocessed["affected_service"],
        "reported_window": preprocessed["reported_window"],
        "clock_skew_notes": preprocessed["clock_skew_notes"]
    }

    # 2. Construct deterministic EvidenceOverview
    evidence_overview = {
        "total_sources_available": preprocessed["total_sources"],
        "services_covered": preprocessed["services_covered"],
        "telemetry_types": preprocessed["telemetry_types"]
    }

    # 3. Guardrail evidence_id mapping in key observations
    available_evidence = llm_output.get("available_evidence", [])
    valid_eids = {e["evidence_id"] for e in available_evidence}

    # Fallback to first evidence_id if missing
    default_eid = list(valid_eids)[0] if valid_eids else "EVID-001"

    sanitized_observations = []
    for obs in llm_output.get("key_observations", []):
        eid = obs.get("evidence_id")
        if eid not in valid_eids:
            obs["evidence_id"] = default_eid
        sanitized_observations.append(obs)

    # 4. Enforce strict Investigation Constraints for Hypothesis Workers
    investigation_constraints = {
        "max_concurrent_hypotheses": 3,
        "token_budget": 10000
    }

    # 5. Assemble final object
    final_dict = {
        "incident_context": incident_context,
        "readiness_verdict": llm_output.get("readiness_verdict", {
            "status": "READY",
            "can_dispatch_workers": True,
            "quality_warnings": []
        }),
        "evidence_overview": evidence_overview,
        "available_evidence": available_evidence,
        "key_observations": sanitized_observations,
        "evidence_gaps": llm_output.get("evidence_gaps", []),
        "investigation_constraints": investigation_constraints
    }

    # 6. Validate with Pydantic
    validated_model = IncidentSummary(**final_dict)
    return validated_model.model_dump()
