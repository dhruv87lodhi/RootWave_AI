"""Evidence preparation and chunking orchestration service.

Extracts evidence from `evidence_items`, routes candidate items through
type-specific preprocessors (logs, metrics, jira, slack), preserves other
telemetry as direct pass-through chunks, generates embeddings, stores
results in `evidence_chunks`, and updates `evidence_inventory`.
"""

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import EvidenceChunk, EvidenceInventory, EvidenceItem
from app.services.preprocessors import (
    preprocess_jira,
    preprocess_logs,
    preprocess_metrics,
    preprocess_slack,
)


def _generate_mock_embedding(text: str, dim: int = 1536) -> List[float]:
    """Generates a deterministic float vector based on text content hash."""
    seed_hash = hashlib.sha256(text.encode("utf-8")).digest()
    values = []
    for i in range(dim):
        byte_val = seed_hash[i % len(seed_hash)]
        # Map byte 0-255 to roughly [-1.0, 1.0]
        values.append(round((byte_val / 127.5) - 1.0, 4))
    return values


def _parse_iso_datetime(val: Optional[str]) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        clean_val = val.strip()
        if clean_val.endswith("Z"):
            clean_val = clean_val[:-1] + "+00:00"
        return datetime.fromisoformat(clean_val)
    except Exception:
        return None


def _item_to_preprocessor_input(item: EvidenceItem) -> Dict[str, Any]:
    """Maps a DB EvidenceItem to the standard preprocessor input dictionary."""
    content = item.excerpt
    if not content and isinstance(item.payload, dict):
        content = item.payload.get("message") or item.payload.get("raw_content") or str(item.payload)
    if not content:
        content = ""

    service_name = item.source_name
    if not service_name and isinstance(item.payload, dict):
        service_name = item.payload.get("service") or item.payload.get("service_name")

    timestamp_str = item.event_time.isoformat() if item.event_time else None

    return {
        "evidence_id": str(item.evidence_id),
        "incident_id": str(item.incident_id),
        "evidence_type": item.evidence_type,
        "content": content,
        "timestamp": timestamp_str,
        "service_name": service_name,
        "metadata": item.payload if isinstance(item.payload, dict) else {},
    }


def _create_passthrough_chunk(item_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Converts non-preprocessed evidence directly into a searchable chunk."""
    evidence_type = item_dict.get("evidence_type", "generic")
    if "deploy" in evidence_type.lower():
        representation_type = "deployment_event"
    else:
        representation_type = "unprocessed_evidence"

    return {
        "incident_id": item_dict["incident_id"],
        "evidence_type": evidence_type,
        "representation_type": representation_type,
        "content": item_dict.get("content", ""),
        "source_ids": [item_dict["evidence_id"]],
        "timestamp": item_dict.get("timestamp"),
        "time_range": None,
        "service_name": item_dict.get("service_name"),
        "metadata": item_dict.get("metadata", {}),
    }


def prepare_evidence_for_incident(
    db: Session,
    incident_id: UUID,
    generate_embeddings: bool = True,
) -> Dict[str, Any]:
    """Preprocesses all evidence items for an incident and creates chunks + inventory.

    1. Loads evidence_items from the database.
    2. Routes items by evidence_type:
       - logs -> preprocess_logs
       - metrics -> preprocess_metrics
       - ticket/jira -> preprocess_jira
       - slack_message/slack -> preprocess_slack
       - others -> direct pass-through
    3. Generates embeddings.
    4. Persists chunks to `evidence_chunks`.
    5. Computes and upserts `evidence_inventory`.
    """
    evidence_items = repositories.list_evidence_for_incident(db, incident_id)
    if not evidence_items:
        return {
            "incident_id": str(incident_id),
            "total_evidence_items": 0,
            "total_chunks": 0,
            "chunks": [],
        }

    # Group into input dictionaries
    logs_input: List[Dict[str, Any]] = []
    metrics_input: List[Dict[str, Any]] = []
    jira_input: List[Dict[str, Any]] = []
    slack_input: List[Dict[str, Any]] = []
    other_input: List[Dict[str, Any]] = []

    for item in evidence_items:
        item_dict = _item_to_preprocessor_input(item)
        etype = (item.evidence_type or "").lower()

        if etype in ("application_log", "logs", "log"):
            logs_input.append(item_dict)
        elif etype in ("metric", "metrics"):
            metrics_input.append(item_dict)
        elif etype in ("ticket", "jira"):
            jira_input.append(item_dict)
        elif etype in ("slack_message", "slack"):
            slack_input.append(item_dict)
        else:
            other_input.append(item_dict)

    # Execute preprocessors and direct passthroughs
    processed_records: List[Dict[str, Any]] = []
    if logs_input:
        processed_records.extend(preprocess_logs(logs_input))
    if metrics_input:
        processed_records.extend(preprocess_metrics(metrics_input))
    if jira_input:
        processed_records.extend(preprocess_jira(jira_input))
    if slack_input:
        processed_records.extend(preprocess_slack(slack_input))
    for other_item in other_input:
        processed_records.append(_create_passthrough_chunk(other_item))

    # Clean existing chunks for this incident to support idempotent reruns
    db.query(EvidenceChunk).filter(EvidenceChunk.incident_id == incident_id).delete(synchronize_session=False)

    saved_chunks: List[EvidenceChunk] = []
    now_utc = datetime.now(timezone.utc)

    for rec in processed_records:
        content = rec.get("content", "")
        embedding = _generate_mock_embedding(content) if generate_embeddings else None

        # Parse timestamps
        tr = rec.get("time_range")
        if isinstance(tr, dict):
            tr_start = _parse_iso_datetime(tr.get("start"))
            tr_end = _parse_iso_datetime(tr.get("end"))
        else:
            ts = _parse_iso_datetime(rec.get("timestamp"))
            tr_start = ts
            tr_end = ts

        chunk = EvidenceChunk(
            incident_id=incident_id,
            content=content,
            embedding=embedding,
            representation_type=rec.get("representation_type", "unprocessed_evidence"),
            evidence_type=rec.get("evidence_type", "generic"),
            service=rec.get("service_name"),
            time_range_start=tr_start,
            time_range_end=tr_end,
            source_evidence_ids=rec.get("source_ids", []),
            metadata_=rec.get("metadata", {}),
            created_at=now_utc,
        )
        db.add(chunk)
        saved_chunks.append(chunk)

    db.flush()

    # Calculate inventory summary
    raw_summary: Dict[str, int] = {}
    for item in evidence_items:
        raw_summary[item.evidence_type] = raw_summary.get(item.evidence_type, 0) + 1

    chunks_summary: Dict[str, Dict[str, Any]] = {}
    services_set = set()

    for ch in saved_chunks:
        rtype = ch.representation_type
        if rtype not in chunks_summary:
            chunks_summary[rtype] = {"count": 0, "services": []}
        chunks_summary[rtype]["count"] += 1

        if ch.service:
            services_set.add(ch.service)
            if ch.service not in chunks_summary[rtype]["services"]:
                chunks_summary[rtype]["services"].append(ch.service)

    # Time range from evidence_items
    event_times = [it.event_time for it in evidence_items if it.event_time]
    min_time = min(event_times) if event_times else None
    max_time = max(event_times) if event_times else None

    # Upsert inventory
    inventory = (
        db.query(EvidenceInventory)
        .filter(EvidenceInventory.incident_id == incident_id)
        .first()
    )
    if not inventory:
        inventory = EvidenceInventory(
            incident_id=incident_id,
            total_evidence_items=len(evidence_items),
            total_chunks=len(saved_chunks),
            services_involved=sorted(list(services_set)),
            time_range_start=min_time,
            time_range_end=max_time,
            evidence_items_summary=raw_summary,
            chunks_summary=chunks_summary,
            generated_at=now_utc,
        )
        db.add(inventory)
    else:
        inventory.total_evidence_items = len(evidence_items)
        inventory.total_chunks = len(saved_chunks)
        inventory.services_involved = sorted(list(services_set))
        inventory.time_range_start = min_time
        inventory.time_range_end = max_time
        inventory.evidence_items_summary = raw_summary
        inventory.chunks_summary = chunks_summary
        inventory.generated_at = now_utc

    db.commit()

    return {
        "incident_id": str(incident_id),
        "total_evidence_items": len(evidence_items),
        "total_chunks": len(saved_chunks),
        "services_involved": sorted(list(services_set)),
        "chunks_summary": chunks_summary,
        "evidence_items_summary": raw_summary,
    }

