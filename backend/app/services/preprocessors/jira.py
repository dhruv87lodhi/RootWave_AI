"""Jira ticket preprocessor.

Groups related Jira events, changelogs, audit records, and comments by issue key,
sorts them chronologically, and synthesizes a readable incident timeline chunk.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parses an ISO timestamp string into a timezone-aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def preprocess_jira(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preprocess Jira ticket evidence items into chronological issue summaries.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized Jira ticket evidence dictionaries.

    Returns
    -------
    list[dict]
        List of preprocessed Jira issue summaries ready for chunking and RAG.
    """
    # 1. Empty guard
    if not evidence:
        return []

    groups = defaultdict(list)

    # 2. Group events by Jira issue key
    for item in evidence:
        metadata = item.get("metadata") or {}

        issue_key = (
            metadata.get("issue_key")
            or metadata.get("source_record_id")
            or item.get("source_record_id")
            or item.get("evidence_id")
        )

        evidence_type = item.get("evidence_type") or item.get("source_type") or "ticket"
        key = (item.get("incident_id"), evidence_type, issue_key)
        groups[key].append(item)

    results: List[Dict[str, Any]] = []

    # 3. Process each Jira issue group
    for (incident_id, evidence_type, issue_key), events in groups.items():

        # Sort events chronologically by timestamp
        events.sort(
            key=lambda e: parse_timestamp(e.get("timestamp"))
            or datetime.max.replace(tzinfo=timezone.utc)
        )

        # Build clean timeline lines with header for RAG retrieval
        lines = [f"Jira Issue: {issue_key}", "Timeline:"]

        for event in events:
            metadata = event.get("metadata") or {}

            details = ", ".join(
                f"{k}: {v}"
                for k, v in metadata.items()
                if k != "issue_key" and v not in (None, "", [])
            )

            ts = event.get("timestamp")
            prefix = f"- [{ts}] " if ts else "- "
            line = f"{prefix}{event.get('content', '')}"
            if details:
                line += f" | {details}"

            lines.append(line)

        # Calculate time range
        valid_times = [
            event.get("timestamp")
            for event in events
            if parse_timestamp(event.get("timestamp"))
        ]

        time_range = None
        if valid_times:
            time_range = {
                "start": valid_times[0],
                "end": valid_times[-1],
            }

        service_names = {
            event.get("service_name")
            for event in events
            if event.get("service_name")
        }

        # 4. Return chunk matching database contracts
        results.append(
            {
                "incident_id": incident_id,
                "evidence_type": evidence_type,
                "representation_type": "jira_summary",
                "content": "\n".join(lines),
                "source_ids": [
                    str(event["evidence_id"])
                    for event in events
                    if event.get("evidence_id") is not None
                ],
                "timestamp": (
                    events[0].get("timestamp") if len(events) == 1 else None
                ),
                "time_range": time_range,
                "service_name": (
                    next(iter(service_names)) if len(service_names) == 1 else None
                ),
                "metadata": {
                    "issue_key": issue_key,
                    "event_count": len(events),
                },
            }
        )

    return results


# Alias for compatibility with external scripts
preprocess_evidence = preprocess_jira
