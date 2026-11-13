"""Jira ticket preprocessor placeholder.

Teammate implementation target: Ticket Summarization / Direct Chunking.
Summarizes ticket description, priority, comments, and decisions.
"""

from typing import Any, Dict, List


def preprocess_jira(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preprocess Jira ticket evidence items into compact summaries.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized Jira ticket evidence dictionaries.

    Returns
    -------
    list[dict]
        List of preprocessed Jira ticket summaries.
    """
    if not evidence:
        return []

    results: List[Dict[str, Any]] = []
    for item in evidence:
        source_id = str(item["evidence_id"])
        incident_id = str(item["incident_id"])
        service_name = item.get("service_name")
        timestamp = item.get("timestamp")

        summary_content = f"Jira Ticket Summary: {item.get('content', '')}"

        results.append(
            {
                "incident_id": incident_id,
                "evidence_type": "ticket",
                "representation_type": "jira_summary",
                "content": summary_content,
                "source_ids": [source_id],
                "timestamp": timestamp,
                "time_range": None,
                "service_name": service_name,
                "metadata": {
                    **(item.get("metadata") or {}),
                    "is_placeholder": True,
                },
            }
        )

    return results

