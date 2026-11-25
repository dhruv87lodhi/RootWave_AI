# Data Contracts — Sprint 2 (Orchestration)
## AI-Powered Root Cause Investigation Platform

---

## 1. Purpose

This document defines the data contracts that **Team 2 (Orchestration)** depends on to build the Orchestrator agents.

It covers:
- The **`evidence_chunks` table** — the prepared, searchable evidence chunks that hypothesis agents retrieve via the RAG retrieval service.

> **Note:** Internal orchestration contracts (agent task assignments, agent results, final investigation output) are owned and defined by Team 2.

---

## 2. Representation Types

The following `representation_type` values are defined for **Sprint 2**. Team 1 populates chunks using these values and Team 2 filters on them when querying the RAG service.

> **Note:** This list is fixed for Sprint 2. It is intentionally designed to be extensible — new representation types can be added in future sprints without breaking existing contracts. Neither team should hardcode logic that breaks when an unknown type appears.

| Value | Source Evidence Type | Description |
| :--- | :--- | :--- |
| `log_cluster` | `application_log` | A compact cluster summary representing many similar log lines. |
| `metric_anomaly` | `metric` | A detected anomaly or changepoint in a metric time series with the anomalous period summarised. |
| `slack_summary` | `slack_message` | A concise summary of a relevant Slack incident thread preserving timeline, symptoms, and decisions. |
| `jira_summary` | `ticket` | A summary or direct chunk of a Jira ticket relevant to the incident. |
| `deployment_event` | `deployment` | A key deployment or configuration change event preserved with timeline context. |

---

## 3. Database Schema

### 3.1 Table: `evidence_chunks`

Stores the prepared, embedding-indexed evidence chunks produced by the Evidence Preparation pipeline. Team 2 accesses this table exclusively through the **RAG retrieval service** — no direct table access.

| Column Name | PostgreSQL Type | Nullable | Default / Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `chunk_id` | `UUID` | **No** | Primary Key (`gen_random_uuid()`) | Unique identifier for this chunk. |
| `incident_id` | `UUID` | **No** | FK → `incidents.incident_id` `ON DELETE RESTRICT` | Parent incident. |
| `content` | `TEXT` | **No** | None | Human-readable, retrieval-optimised text content of the chunk. This is what agents read and reason over. |
| `embedding` | `vector(1536)` | Yes | `NULL` | pgvector embedding used for semantic similarity search. |
| `representation_type` | `VARCHAR(30)` | **No** | Check: values from Section 2 | Category of preprocessed representation. |
| `evidence_type` | `VARCHAR(50)` | **No** | None | Original evidence classification (e.g., `application_log`, `metric`). Aligns with `evidence_items.evidence_type`. |
| `service` | `TEXT` | Yes | `NULL` | Service this chunk relates to. `NULL` for cross-service evidence (e.g., Slack, Jira). |
| `time_range_start` | `TIMESTAMPTZ` | Yes | `NULL` | Start of the time window this chunk covers. |
| `time_range_end` | `TIMESTAMPTZ` | Yes | `NULL` | End of the time window this chunk covers. |
| `source_evidence_ids` | `JSONB` | **No** | Default: `'[]'::jsonb` | List of original `evidence_items.evidence_id` UUIDs this chunk was derived from. For discrete sources (Jira, Slack, Deployment), contains all source IDs. For high-volume sources (log clusters), contains representative sample IDs (e.g. first, peak, last occurrence). |
| `metadata` | `JSONB` | **No** | `'{}'::jsonb` | Flexible extra fields that vary by `representation_type` (e.g., cluster size, anomaly score, ticket ID). |
| `created_at` | `TIMESTAMPTZ` | **No** | `NOW()` | When this chunk was created. |

* **Indexes**:
  * `idx_chunks_incident_type` on `(incident_id, representation_type)`.
  * `idx_chunks_service` on `(incident_id, service)`.
  * Vector index on `embedding` for approximate nearest-neighbour search (Team 1 to configure).

Source evidence references are stored directly on each chunk via `source_evidence_ids`. This enables zero-join, high-performance vector retrieval while preserving complete audit traceability back to the raw `evidence_items` table.

---

## 4. Evidence Preprocessing Contract

To prepare raw evidence items for vector search and avoid token bloat, evidence is processed through type-specific preprocessors before chunk creation.

### 4.1 Preprocessor Scope

* **Preprocessed Types (4 categories)**:
  * `application_log` $\rightarrow$ `preprocess_logs` (log pattern clustering / Drain3)
  * `metric` $\rightarrow$ `preprocess_metrics` (anomaly & changepoint detection)
  * `ticket` (Jira) $\rightarrow$ `preprocess_jira` (ticket summarization)
  * `slack_message` $\rightarrow$ `preprocess_slack` (thread summarization)
* **Non-Preprocessed Types**:
  * Other evidence types (e.g. `deployment_log`, `generic`) pass through directly into `evidence_chunks` without loss, tagged with their respective representation type (e.g., `deployment_event` or `unprocessed_evidence`).

---

### 4.2 Preprocessor Function Signature

All preprocessor modules in `app/services/preprocessors/` adhere to a uniform batch signature:

```python
def preprocess_evidence(evidence: list[dict]) -> list[dict]:
    ...
```

#### Input Dictionary Schema (per item)

```json
{
  "evidence_id": "c1f8a847-a8d2-4b2a-89bc-9937bc38d9b1",
  "incident_id": "8f2a1b9c-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "evidence_type": "application_log",
  "content": "Database connection timeout for api-service",
  "timestamp": "2026-11-08T10:12:00Z",
  "service_name": "api-service",
  "metadata": {}
}
```

* `evidence_id` (`str`): UUID of the source record in `evidence_items`.
* `incident_id` (`str`): UUID of the parent incident.
* `evidence_type` (`str`): Telemetry classification (`application_log`, `metric`, `ticket`, `slack_message`).
* `content` (`str`): Normalized text or description extracted from `excerpt` or `payload`.
* `timestamp` (`str | None`): Event timestamp in ISO-8601 UTC string format.
* `service_name` (`str | None`): Affected service or origin name.
* `metadata` (`dict`): Source-specific payload dictionary.

#### Output Dictionary Schema (per preprocessed record)

```json
{
  "incident_id": "8f2a1b9c-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
  "evidence_type": "application_log",
  "representation_type": "log_cluster",
  "content": "Multiple database connection timeouts occurred in api-service.",
  "source_ids": ["c1f8a847-a8d2-4b2a-89bc-9937bc38d9b1", "..."],
  "timestamp": null,
  "time_range": {
    "start": "2026-11-08T10:12:00Z",
    "end": "2026-11-08T10:18:00Z"
  },
  "service_name": "api-service",
  "metadata": {
    "cluster_count": 3
  }
}
```

* `incident_id` (`str`): Incident ID.
* `evidence_type` (`str`): Source evidence type.
* `representation_type` (`str`): Target representation (`log_cluster`, `metric_anomaly`, `jira_summary`, `slack_summary`).
* `content` (`str`): Preprocessed summary text ready for RAG embedding and LLM reasoning.
* `source_ids` (`list[str]`): List of original `evidence_id`s represented by this record (preserves complete provenance).
* `timestamp` (`str | None`): Single point-in-time timestamp (if applicable).
* `time_range` (`dict | None`): Range object with `start` and `end` ISO timestamps (if spanning an interval).
* `service_name` (`str | None`): Related service.
* `metadata` (`dict`): Preprocessing statistics (e.g. `cluster_size`, `peak_value`, `thread_count`).

---

## 5. Pipeline Architecture & Implementation Status

The Sprint 2 evidence preparation layer is structured into modular components:

```text
backend/app/
├── db/
│   ├── models.py                   # Implemented: EvidenceChunk model
│   └── repositories.py             # Implemented: list_chunks_for_incident
└── services/
    ├── embedding_service.py        # Implemented: 1536-dim embeddings & cosine similarity (OpenAI/Gemini with deterministic fallback)
    ├── preprocessors/              # Implemented: Preprocessors per evidence type
    │   ├── logs.py                 # preprocess_logs (Drain3 clustering)
    │   ├── metrics.py              # preprocess_metrics (Anomaly detection)
    │   ├── jira.py                 # preprocess_jira (Ticket summarization)
    │   └── slack.py                # preprocess_slack (Thread summarization)
    ├── evidence_prep_service.py    # Implemented: Coordinator pipeline (chunking)
    └── rag_retriever.py            # Implemented: search_chunks semantic retrieval service
```

### Pipeline Execution Flow (`prepare_evidence_for_incident`):
1. **Load Raw Evidence**: Retrieves raw items for the given incident from `evidence_items`.
2. **Convert & Route**: Transforms rows to input schemas, routing 4 types to preprocessors and non-preprocessed types to pass-through chunks.
3. **Embeddings**: Generates 1536-dimensional normalized unit vector embeddings (`generate_embedding`).
4. **Persist Chunks**: Bulk-writes chunks into `evidence_chunks` with `source_evidence_ids` for zero-join retrieval.

---

## 6. RAG Retrieval Service & Embeddings

Team 1 provides the callable **RAG retrieval service** (`app/services/rag_retriever.py`) that Team 2's hypothesis agents import and call directly in LangGraph:

```python
from app.services.rag_retriever import search_chunks

results = search_chunks(
    db=db_session,
    incident_id=incident_id,
    query="database connection pool exhausted in order-service",
    filters={"representation_types": ["log_cluster", "metric_anomaly"]},
    top_k=5,
)
```

### 6.1 Embedding Strategy (`embedding_service.py`)

* **Vector Dimension**: 1536 dimensions (matching industry-standard models like OpenAI `text-embedding-3-small`).
* **Normalized Unit Vectors**: Vectors are L2-normalized so that cosine similarity is computed directly via dot product, mapped into a clean $0.0$ to $1.0$ score range.
* **Pluggable & Zero-Key Fallback**:
  * In production: connects to live cloud embedding models (e.g., OpenAI or Gemini).
  * In local/test environments: uses a deterministic SHA-256 seed-based projection that requires no API keys, incurs zero network latency, and executes in milliseconds while guaranteeing identical semantic representations across runs.

### 6.2 Pre-Filtering & Semantic Ranking

The service executes a 2-stage retrieval pipeline:
1. **Relational Metadata Pre-Filtering**: Scans candidate chunks in SQL matching `incident_id`, filtering by `representation_types`, `services`, and `time_range` overlap before scoring.
2. **Similarity Scoring & Ranking**: Computes cosine similarity between query vector and candidate chunk vectors (using pgvector `<=>` operator in PostgreSQL or unit vector cosine similarity in fallback mode), returning top-k ranked results.

---

## 7. Team 1 Deliverables Status

| # | Deliverable | Status | Description |
| :--- | :--- | :--- | :--- |
| 1 | **`evidence_chunks` table with embeddings** | **Implemented** | Model with pgvector support & fallback defined in `models.py`. Persisted with `source_evidence_ids` for zero-join traceability. |
| 2 | **RAG retrieval service** | **Implemented** | `app/services/rag_retriever.py` provides `search_chunks(...)` with embedding similarity ranking, filtering, and top-k selection. |
| 3 | **Embedding service** | **Implemented** | `app/services/embedding_service.py` provides 1536-dim vector generation and cosine similarity calculation. |
| 4 | **Seeded scenario data** | **Implemented** | Benchmark scenarios seeded via `scripts/seed_database.py`. |

### Minimum viable delivery to unblock Team 2

Team 2 can build the Orchestrator structure and agent scaffolding immediately. To run end-to-end tests, Team 2 needs at minimum:

- **Item 2** (RAG retrieval service) working with realistic chunk data, even if backed by a small seed dataset.

If the full pipeline is not ready, Team 1 can provide **stub implementations** of the service functions that return hardcoded realistic data conforming to the schemas in this document.

---

## 8. Identifier Reference

Aligns with Sprint 1 conventions (`docs/data-contract.md` Section 6).

| Identifier | Description |
| :--- | :--- |
| `incident_id` | Internal UUID for the incident. Defined in Sprint 1. |
| `evidence_id` | Internal UUID of an original `evidence_items` record. Returned as `source_evidence_ids` in chunk results for traceability. |
| `chunk_id` | Internal UUID of a prepared `evidence_chunks` record. |
| `thread_id` | LangGraph execution identifier. Convention: `investigation-{incident_id}`. Defined in Sprint 1. |
