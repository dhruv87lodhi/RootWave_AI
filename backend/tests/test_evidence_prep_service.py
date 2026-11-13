from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid
import pytest

from app.db.models import EvidenceItem
from app.services.evidence_prep_service import (
    _create_passthrough_chunk,
    _generate_mock_embedding,
    _item_to_preprocessor_input,
    prepare_evidence_for_incident,
)


def test_item_to_preprocessor_input():
    ev_id = uuid.uuid4()
    inc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    item = EvidenceItem(
        evidence_id=ev_id,
        incident_id=inc_id,
        evidence_type="application_log",
        source_name="order-service",
        event_time=now,
        excerpt="Database connection timed out",
        payload={"level": "ERROR", "cluster": "k8s-prod"},
    )

    result = _item_to_preprocessor_input(item)
    assert result["evidence_id"] == str(ev_id)
    assert result["incident_id"] == str(inc_id)
    assert result["evidence_type"] == "application_log"
    assert result["content"] == "Database connection timed out"
    assert result["service_name"] == "order-service"
    assert result["timestamp"] == now.isoformat()
    assert result["metadata"]["level"] == "ERROR"


def test_create_passthrough_chunk():
    item_dict = {
        "evidence_id": "EV-101",
        "incident_id": "INC-001",
        "evidence_type": "deployment_log",
        "content": "Deployed v2.1.0",
        "timestamp": "2026-11-08T10:12:00Z",
        "service_name": "api-service",
        "metadata": {"commit": "abc1234"},
    }

    chunk = _create_passthrough_chunk(item_dict)
    assert chunk["incident_id"] == "INC-001"
    assert chunk["evidence_type"] == "deployment_log"
    assert chunk["representation_type"] == "deployment_event"
    assert chunk["content"] == "Deployed v2.1.0"
    assert chunk["source_ids"] == ["EV-101"]


def test_generate_mock_embedding():
    emb1 = _generate_mock_embedding("database timeout")
    emb2 = _generate_mock_embedding("database timeout")
    emb3 = _generate_mock_embedding("completely different text")

    assert len(emb1) == 1536
    assert emb1 == emb2  # Deterministic
    assert emb1 != emb3


def test_prepare_evidence_for_incident_mock_db():
    inc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    item1 = EvidenceItem(
        evidence_id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="application_log",
        source_name="order-service",
        event_time=now,
        excerpt="Connection timeout",
        payload={},
    )
    item2 = EvidenceItem(
        evidence_id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="metric",
        source_name="order-service",
        event_time=now,
        excerpt="metric=98",
        payload={},
    )
    item3 = EvidenceItem(
        evidence_id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="deployment_log",
        source_name="k8s",
        event_time=now,
        excerpt="Deployed new version",
        payload={},
    )

    mock_db = MagicMock()
    # Mock repositories.list_evidence_for_incident via query
    mock_db.execute.return_value.scalars.return_value.all.return_value = [item1, item2, item3]
    mock_db.query.return_value.filter.return_value.first.return_value = None

    summary = prepare_evidence_for_incident(mock_db, inc_id)

    assert summary["incident_id"] == str(inc_id)
    assert summary["total_evidence_items"] == 3
    assert summary["total_chunks"] == 3
    assert "log_cluster" in summary["chunks_summary"]
    assert "metric_anomaly" in summary["chunks_summary"]
    assert "deployment_event" in summary["chunks_summary"]
    assert mock_db.commit.called
