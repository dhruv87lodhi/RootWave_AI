
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import UserDefinedType

from app.db.database import Base

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except ImportError:
    class Vector(UserDefinedType):
        """Fallback Vector type when pgvector package is not installed."""
        def __init__(self, dim: int = 1536):
            self.dim = dim

        def get_col_spec(self, **kw):
            return f"vector({self.dim})"

        def bind_processor(self, dialect):
            def process(value):
                if value is None:
                    return None
                if isinstance(value, list):
                    return f"[{','.join(str(x) for x in value)}]"
                return value
            return process

        def result_processor(self, dialect, coltype):
            def process(value):
                if value is None:
                    return None
                if isinstance(value, str):
                    return [float(x) for x in value.strip("[]").split(",") if x.strip()]
                return value
            return process


class Incident(Base):
    __tablename__ = "incidents"

    incident_id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    title = mapped_column(String(200), nullable=False)
    original_text = mapped_column(Text, nullable=False)
    affected_service = mapped_column(Text, nullable=True)
    severity = mapped_column(
        String(20), nullable=False, server_default=text("'UNKNOWN'")
    )
    status = mapped_column(
        String(20), nullable=False, server_default=text("'OPEN'")
    )
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('SEV1', 'SEV2', 'SEV3', 'SEV4', 'UNKNOWN')",
            name="incidents_severity_check",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'CLOSED')",
            name="incidents_status_check",
        ),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="incidents_time_check",
        ),
        Index("idx_incidents_status_created", "status", "created_at"),
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    evidence_id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    incident_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incidents.incident_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_type = mapped_column(String(50), nullable=False)
    source_type = mapped_column(String(30), nullable=False)
    source_name = mapped_column(Text, nullable=True)
    source_record_id = mapped_column(Text, nullable=True)
    event_time = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    excerpt = mapped_column(Text, nullable=True)
    original_ref = mapped_column(Text, nullable=True)
    content_hash = mapped_column(Text, nullable=True)
    reliability_score = mapped_column(Numeric(5, 4), nullable=True)
    payload = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(
            "reliability_score IS NULL OR reliability_score BETWEEN 0 AND 1",
            name="evidence_reliability_check",
        ),
        CheckConstraint(
            "source_type IN ('UPLOAD', 'INTEGRATION')",
            name="evidence_source_type_check",
        ),
        Index("idx_evidence_incident_event", "incident_id", "event_time"),
    )


class EvidenceInventory(Base):
    __tablename__ = "evidence_inventory"

    inventory_id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    incident_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incidents.incident_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    total_evidence_items = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_chunks = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    services_involved = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    time_range_start = mapped_column(DateTime(timezone=True), nullable=True)
    time_range_end = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_items_summary = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    chunks_summary = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    generated_at = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        CheckConstraint(
            "total_evidence_items >= 0", name="inventory_total_evidence_check"
        ),
        CheckConstraint(
            "total_chunks >= 0", name="inventory_total_chunks_check"
        ),
        Index("idx_inventory_incident", "incident_id"),
    )


class EvidenceChunk(Base):
    __tablename__ = "evidence_chunks"

    chunk_id = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    incident_id = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incidents.incident_id", ondelete="RESTRICT"),
        nullable=False,
    )
    content = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True)
    representation_type = mapped_column(String(30), nullable=False)
    evidence_type = mapped_column(String(50), nullable=False)
    service = mapped_column(Text, nullable=True)
    time_range_start = mapped_column(DateTime(timezone=True), nullable=True)
    time_range_end = mapped_column(DateTime(timezone=True), nullable=True)
    source_evidence_ids = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    metadata_ = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("idx_chunks_incident_type", "incident_id", "representation_type"),
        Index("idx_chunks_service", "incident_id", "service"),
    )

