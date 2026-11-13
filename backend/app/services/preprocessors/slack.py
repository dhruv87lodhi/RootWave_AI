"""Slack incident conversation preprocessor placeholder.

Teammate implementation target: Thread Summarization.
Preserves timeline, observed symptoms, decisions, and participants while reducing token usage.
"""

from typing import Any, Dict, List


def preprocess_slack(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preprocess Slack message evidence items into incident thread summaries.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized Slack message evidence dictionaries.

    Returns
    -------
    list[dict]
        List of preprocessed Slack conversation summaries.
    """
    if not evidence:
        return []

    source_ids = [str(item["evidence_id"]) for item in evidence]
    incident_id = str(evidence[0]["incident_id"])
    service_name = evidence[0].get("service_name")

    timestamps = [item.get("timestamp") for item in evidence if item.get("timestamp")]
    time_range = None
    if timestamps:
        time_range = {"start": min(timestamps), "end": max(timestamps)}

    combined_sample = " | ".join(e.get("content", "")[:80] for e in evidence[:3])
    summary_content = (
        f"Slack Incident Thread Summary ({len(evidence)} messages): {combined_sample}"
    )

    return [
        {
            "incident_id": incident_id,
            "evidence_type": "slack_message",
            "representation_type": "slack_summary",
            "content": summary_content,
            "source_ids": source_ids,
            "timestamp": None,
            "time_range": time_range,
            "service_name": service_name,
            "metadata": {
                "message_count": len(evidence),
                "is_placeholder": True,
            },
        }
    ]

