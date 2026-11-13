"""Metric evidence preprocessor placeholder.

Teammate implementation target: Anomaly / Changepoint Detection.
Detects significant shifts in time-series metrics and creates compact period summaries.
"""

from typing import Any, Dict, List


def preprocess_metrics(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preprocess metric evidence items into anomaly summaries.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized metric evidence dictionaries.

    Returns
    -------
    list[dict]
        List of preprocessed metric anomaly representations.
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

    summary_content = (
        f"Metric Anomaly Summary ({len(evidence)} data points): "
        f"Sample: {evidence[0].get('content', '')[:200]}"
    )

    return [
        {
            "incident_id": incident_id,
            "evidence_type": "metric",
            "representation_type": "metric_anomaly",
            "content": summary_content,
            "source_ids": source_ids,
            "timestamp": None,
            "time_range": time_range,
            "service_name": service_name,
            "metadata": {
                "points_count": len(evidence),
                "is_placeholder": True,
            },
        }
    ]

