from typing import Any, Dict, List, TypedDict


class InvestigationState(TypedDict, total=False):
    incident_id: str
    incident: dict
    evidences: list[dict]
    chunks_count: int
    status: str
    errors: list[str]
    completed_at: str
