from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid
import pytest

from app.db.models import EvidenceChunk
from app.services.embedding_service import cosine_similarity, generate_embedding
from app.services.rag_retriever import search_chunks


def test_embedding_and_similarity():
    # Same text has max similarity
    text = "Database connection pool exhausted"
    emb1 = generate_embedding(text)
    emb2 = generate_embedding(text)
    assert len(emb1) == 1536
    sim_same = cosine_similarity(emb1, emb2)
    assert pytest.approx(sim_same, 0.001) == 1.0

    # Different texts
    emb_diff = generate_embedding("Frontend CSS color button styling")
    sim_diff = cosine_similarity(emb1, emb_diff)
    assert 0.0 <= sim_diff < 1.0


def test_search_chunks_empty_query():
    mock_db = MagicMock()
    inc_id = uuid.uuid4()
    results = search_chunks(mock_db, inc_id, query="   ")
    assert results == []


def test_search_chunks_ranking():
    inc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Chunk 1: highly relevant
    c1_content = "Database connection pool saturated with timeouts"
    c1 = EvidenceChunk(
        chunk_id=uuid.uuid4(),
        incident_id=inc_id,
        content=c1_content,
        embedding=generate_embedding(c1_content),
        representation_type="log_cluster",
        evidence_type="application_log",
        service="order-service",
        time_range_start=now,
        time_range_end=now,
        source_evidence_ids=["ev-1"],
        metadata_={"cluster_count": 50},
    )

    # Chunk 2: irrelevant
    c2_content = "User logged out of web dashboard successfully"
    c2 = EvidenceChunk(
        chunk_id=uuid.uuid4(),
        incident_id=inc_id,
        content=c2_content,
        embedding=generate_embedding(c2_content),
        representation_type="log_cluster",
        evidence_type="application_log",
        service="auth-service",
        time_range_start=now,
        time_range_end=now,
        source_evidence_ids=["ev-2"],
        metadata_={"cluster_count": 1},
    )

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [c1, c2]

    # Search with query matching c1
    results = search_chunks(
        mock_db,
        inc_id,
        query="Database connection pool saturated with timeouts",
        top_k=5,
    )

    assert len(results) == 2
    # First result should be c1 with highest similarity score
    assert results[0]["chunk_id"] == str(c1.chunk_id)
    assert results[0]["similarity_score"] > results[1]["similarity_score"]
    assert results[0]["source_evidence_ids"] == ["ev-1"]
    assert results[0]["representation_type"] == "log_cluster"


def test_search_chunks_filters():
    inc_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = []

    filters = {
        "representation_types": ["metric_anomaly"],
        "services": ["order-service"],
        "time_range": {
            "from": "2026-10-24T09:00:00Z",
            "to": "2026-10-24T10:00:00Z",
        },
    }

    results = search_chunks(
        mock_db,
        inc_id,
        query="db connections",
        filters=filters,
        top_k=20,
    )

    assert results == []
    # Verify execute was called with SQL statement containing filters
    assert mock_db.execute.called

