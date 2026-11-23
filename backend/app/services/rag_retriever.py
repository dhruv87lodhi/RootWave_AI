"""RAG retrieval service for hypothesis agents.

Performs semantic search over `evidence_chunks` using vector similarity
and metadata pre-filtering (representation_types, services, time_range).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EvidenceChunk
from app.services.embedding_service import cosine_similarity, generate_embedding


def _parse_dt(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        clean_val = val.strip()
        if clean_val.endswith("Z"):
            clean_val = clean_val[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(clean_val)
        except Exception:
            return None
    return None


def search_chunks(
    db: Session,
    incident_id: UUID,
    query: str,
    filters: Optional[Dict[str, Any]] = None,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """Retrieves the top-k most semantically relevant evidence chunks for an incident.

    Parameters
    ----------
    db : Session
        Active database session.
    incident_id : UUID
        The incident identifier to scope retrieval to.
    query : str
        Natural language investigation query or hypothesis statement.
    filters : Optional[dict], default=None
        Optional filters to restrict the search space:
        - "representation_types": list[str] (e.g. ["log_cluster", "metric_anomaly"])
        - "services": list[str] (e.g. ["order-service"])
        - "time_range": dict with "from" and/or "to" ISO timestamps
    top_k : int, default=10
        Maximum number of ranked chunks to return (clamped between 1 and 50).

    Returns
    -------
    list[dict]
        Ranked list of matching chunk results with similarity score and provenance.
    """
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    # Clamp top_k between 1 and 50
    effective_top_k = max(1, min(int(top_k), 50))

    # Generate query embedding
    query_vector = generate_embedding(clean_query)

    # 1. Base query scoped to incident
    stmt = select(EvidenceChunk).where(EvidenceChunk.incident_id == incident_id)

    # 2. Apply metadata filters
    if filters and isinstance(filters, dict):
        # Filter: representation_types
        rep_types = filters.get("representation_types")
        if rep_types and isinstance(rep_types, list):
            valid_types = [str(t).strip() for t in rep_types if str(t).strip()]
            if valid_types:
                stmt = stmt.where(EvidenceChunk.representation_type.in_(valid_types))

        # Filter: services
        services = filters.get("services")
        if services and isinstance(services, list):
            valid_services = [str(s).strip() for s in services if str(s).strip()]
            if valid_services:
                stmt = stmt.where(EvidenceChunk.service.in_(valid_services))

        # Filter: time_range
        time_filter = filters.get("time_range")
        if isinstance(time_filter, dict):
            from_dt = _parse_dt(time_filter.get("from") or time_filter.get("start"))
            to_dt = _parse_dt(time_filter.get("to") or time_filter.get("end"))

            if from_dt:
                # Chunk end must not be before from_dt
                stmt = stmt.where(
                    (EvidenceChunk.time_range_end.is_(None))
                    | (EvidenceChunk.time_range_end >= from_dt)
                )
            if to_dt:
                # Chunk start must not be after to_dt
                stmt = stmt.where(
                    (EvidenceChunk.time_range_start.is_(None))
                    | (EvidenceChunk.time_range_start <= to_dt)
                )

    candidates = list(db.execute(stmt).scalars().all())
    if not candidates:
        return []

    # 3. Score and rank candidates by vector cosine similarity
    scored_candidates = []
    for chunk in candidates:
        chunk_vector = chunk.embedding
        if chunk_vector is None:
            # Fallback score if embedding was not generated
            score = 0.5
        else:
            score = cosine_similarity(query_vector, chunk_vector)

        scored_candidates.append((score, chunk))

    # Sort descending by score
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # 4. Format top_k output records
    results: List[Dict[str, Any]] = []
    for score, chunk in scored_candidates[:effective_top_k]:
        time_range = None
        if chunk.time_range_start or chunk.time_range_end:
            time_range = {
                "from": (
                    chunk.time_range_start.isoformat()
                    if chunk.time_range_start
                    else None
                ),
                "to": (
                    chunk.time_range_end.isoformat()
                    if chunk.time_range_end
                    else None
                ),
            }

        results.append(
            {
                "chunk_id": str(chunk.chunk_id),
                "incident_id": str(chunk.incident_id),
                "content": chunk.content,
                "representation_type": chunk.representation_type,
                "evidence_type": chunk.evidence_type,
                "service": chunk.service,
                "time_range": time_range,
                "similarity_score": round(float(score), 4),
                "source_evidence_ids": chunk.source_evidence_ids or [],
                "metadata": chunk.metadata_ or {},
            }
        )

    return results

