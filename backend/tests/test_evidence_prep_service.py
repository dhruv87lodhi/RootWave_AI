from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid
import pytest

from app.db.models import EvidenceItem
from app.services.embedding_service import generate_embedding
from app.services.evidence_prep_service import (
    _create_passthrough_chunk,
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
    emb1 = generate_embedding("database timeout")
    emb2 = generate_embedding("database timeout")
    emb3 = generate_embedding("completely different text")

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
        excerpt="db_connections=98",
        payload={"metric_name": "db_connections", "value": 98.0},
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

    summary = prepare_evidence_for_incident(mock_db, inc_id)

    assert summary["incident_id"] == str(inc_id)
    assert summary["total_evidence_items"] == 3
    assert summary["total_chunks"] == 3
    assert mock_db.commit.called


def test_preprocess_jira_grouping():
    from app.services.preprocessors.jira import preprocess_jira

    evidence = [
        {
            "evidence_id": "ev-jira-1",
            "incident_id": "inc-1",
            "evidence_type": "ticket",
            "content": "HOTFIX-409 created in draft state",
            "timestamp": "2026-10-24T09:25:00+05:30",
            "metadata": {
                "issue_key": "HOTFIX-409",
                "status": "Created",
                "priority": "Highest",
            },
        },
        {
            "evidence_id": "ev-jira-2",
            "incident_id": "inc-1",
            "evidence_type": "ticket",
            "content": "Emergency config change to reduce DB idle timeout",
            "timestamp": "2026-10-24T09:28:10+05:30",
            "metadata": {
                "issue_key": "HOTFIX-409",
                "status": "Deployed",
                "peer_review_status": "SKIPPED",
            },
        },
    ]

    results = preprocess_jira(evidence)
    assert len(results) == 1
    hotfix_chunk = results[0]
    assert hotfix_chunk["representation_type"] == "jira_summary"
    assert hotfix_chunk["evidence_type"] == "ticket"
    assert len(hotfix_chunk["source_ids"]) == 2
    assert "ev-jira-1" in hotfix_chunk["source_ids"]
    assert "ev-jira-2" in hotfix_chunk["source_ids"]
    assert "HOTFIX-409" in hotfix_chunk["content"]


def test_preprocess_slack_cleaning_and_grouping():
    from app.services.preprocessors.slack import preprocess_slack

    evidence = [
        {
            "evidence_id": "ev-slack-1",
            "incident_id": "inc-01",
            "evidence_type": "slack_message",
            "content": "<@U1> seeing connection saturation on order DB :fire:",
            "timestamp": "2026-10-24T09:20:15+05:30",
            "service_name": "order-service",
            "metadata": {"channel": "#devops-oncall", "user": "alex"},
        },
        {
            "evidence_id": "ev-slack-2",
            "incident_id": "inc-01",
            "evidence_type": "slack_message",
            "content": "ok",  # Noise message - should be filtered
            "timestamp": "2026-10-24T09:21:00+05:30",
            "service_name": "order-service",
            "metadata": {"channel": "#devops-oncall", "user": "sarah"},
        },
        {
            "evidence_id": "ev-slack-3",
            "incident_id": "inc-01",
            "evidence_type": "slack_message",
            "content": "My kubectl context was pointing to prod-cluster by mistake!",
            "timestamp": "2026-10-24T09:33:20+05:30",
            "service_name": "order-service",
            "metadata": {"channel": "#devops-oncall", "user": "alex"},
        },
    ]

    results = preprocess_slack(evidence)

    assert len(results) == 1
    chunk = results[0]

    assert chunk["representation_type"] == "slack_summary"
    assert chunk["evidence_type"] == "slack_message"
    assert "ev-slack-1" in chunk["source_ids"]
    assert "ev-slack-3" in chunk["source_ids"]
    # Noise message was filtered out of source_ids
    assert "ev-slack-2" not in chunk["source_ids"]
    assert "ev-slack-2" in chunk["metadata"]["noise_filtered_ids"]

    # Content has structured categories (observation, finding)
    assert "Findings:" in chunk["content"]
    assert "Observations:" in chunk["content"]
    assert "prod-cluster by mistake" in chunk["content"]


def test_preprocess_metrics_anomaly_and_changepoints():
    from app.services.preprocessors.metrics import preprocess_metrics

    # Series: baseline normal values, then massive spike (like datadog 502 rate)
    evidence = [
        {
            "evidence_id": "ev-m-1",
            "incident_id": "inc-02",
            "evidence_type": "metric",
            "service_name": "payment-gateway",
            "timestamp": "2026-10-25T11:00:00+05:30",
            "metadata": {"metric_name": "http_502_rate", "value": 0.08, "unit": "percent"},
        },
        {
            "evidence_id": "ev-m-2",
            "incident_id": "inc-02",
            "evidence_type": "metric",
            "service_name": "payment-gateway",
            "timestamp": "2026-10-25T11:05:00+05:30",
            "metadata": {"metric_name": "http_502_rate", "value": 0.09, "unit": "percent"},
        },
        {
            "evidence_id": "ev-m-3",
            "incident_id": "inc-02",
            "evidence_type": "metric",
            "service_name": "payment-gateway",
            "timestamp": "2026-10-25T11:10:00+05:30",
            "metadata": {"metric_name": "http_502_rate", "value": 0.08, "unit": "percent"},
        },
        {
            "evidence_id": "ev-m-4",
            "incident_id": "inc-02",
            "evidence_type": "metric",
            "service_name": "payment-gateway",
            "timestamp": "2026-10-25T11:15:00+05:30",
            "metadata": {"metric_name": "http_502_rate", "value": 55.40, "unit": "percent"},  # Huge spike
        },
        {
            "evidence_id": "ev-m-5",
            "incident_id": "inc-02",
            "evidence_type": "metric",
            "service_name": "payment-gateway",
            "timestamp": "2026-10-25T11:20:00+05:30",
            "metadata": {"metric_name": "http_502_rate", "value": 82.10, "unit": "percent"},  # Sustained
        },
    ]

    results = preprocess_metrics(evidence)

    assert len(results) >= 1
    chunk = results[0]

    assert chunk["representation_type"] == "metric_anomaly"
    assert chunk["evidence_type"] == "metric"
    assert chunk["service_name"] == "payment-gateway"
    assert "ev-m-4" in chunk["source_ids"]
    assert "http_502_rate" in chunk["content"]
    assert chunk["time_range"] is not None


def test_preprocess_logs_clustering():
    from app.services.preprocessors.logs import preprocess_logs

    evidence = [
        {
            "evidence_id": "ev-log-1",
            "incident_id": "inc-03",
            "evidence_type": "application_log",
            "service_name": "checkout-service",
            "content": "Connection timeout connecting to database shard 1 after 3000ms",
            "timestamp": "2026-11-13T10:00:00Z",
        },
        {
            "evidence_id": "ev-log-2",
            "incident_id": "inc-03",
            "evidence_type": "application_log",
            "service_name": "checkout-service",
            "content": "Connection timeout connecting to database shard 2 after 5000ms",
            "timestamp": "2026-11-13T10:01:00Z",
        },
        {
            "evidence_id": "ev-log-3",
            "incident_id": "inc-03",
            "evidence_type": "application_log",
            "service_name": "checkout-service",
            "content": "User session expired for token abc-123",
            "timestamp": "2026-11-13T10:02:00Z",
        },
    ]

    results = preprocess_logs(evidence)

    # Expecting 2 clusters: the two database timeout logs and the session log
    assert len(results) == 2

    # Find the database timeout cluster
    db_cluster = next(c for c in results if "shard" in c["content"])
    assert db_cluster["representation_type"] == "log_cluster"
    assert db_cluster["evidence_type"] == "application_log"
    assert db_cluster["service_name"] == "checkout-service"
    assert set(db_cluster["source_ids"]) == {"ev-log-1", "ev-log-2"}
    assert db_cluster["metadata"]["cluster_count"] == 2
    assert db_cluster["time_range"]["start"] == "2026-11-13T10:00:00Z"
    assert db_cluster["time_range"]["end"] == "2026-11-13T10:01:00Z"

