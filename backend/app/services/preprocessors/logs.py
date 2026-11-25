"""Log evidence preprocessor using Drain3 template mining.

Clusters repetitive application and system logs into high-level pattern
templates with source evidence tracing, temporal intervals, and occurrence counts.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig

    _DRAIN3_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DRAIN3_AVAILABLE = False


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Convert a timestamp string or datetime object into a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str):
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def create_miner():
    """Create a Drain3 miner instance with standard cluster configuration."""
    if not _DRAIN3_AVAILABLE:
        return None

    config = TemplateMinerConfig()
    config.drain_depth = 4
    config.drain_sim_th = 0.5
    config.drain_max_children = 100
    config.parametrize_numeric_tokens = True

    return TemplateMiner(config=config)


def _regex_template_fallback(content: str) -> str:
    """Fallback regex template miner when drain3 is unavailable."""
    # Mask numbers and IP-like / UUID patterns
    content = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<*>", content)
    content = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "<*>", content)
    content = re.sub(r"\b\d+\b", "<*>", content)
    return content.strip()


def preprocess_logs(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preprocess log evidence by grouping similar log messages using Drain3 template mining.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized log evidence dictionaries.

    Returns
    -------
    list[dict]
        List of clustered log representation dictionaries.
    """
    if not evidence:
        return []

    miners: Dict[Tuple[str, Optional[str]], Any] = {}
    clusters: Dict[Tuple[str, Optional[str], Any], Dict[str, Any]] = {}

    for item in evidence:
        if not isinstance(item, dict):
            continue

        # Check evidence_type or source_type
        etype = (item.get("evidence_type") or item.get("source_type") or "").lower()
        if etype and not any(k in etype for k in ("log", "application_log")):
            continue

        incident_id = str(item.get("incident_id") or "")
        evidence_id = str(item.get("evidence_id") or "")
        content = item.get("content")
        service_name = item.get("service_name")

        if not incident_id or not evidence_id:
            continue

        if not isinstance(content, str) or not content.strip():
            continue

        content = content.strip()

        # Dedicated miner per (incident_id, service_name)
        miner_key = (incident_id, service_name)

        if _DRAIN3_AVAILABLE:
            if miner_key not in miners:
                miners[miner_key] = create_miner()

            miner = miners[miner_key]
            result = miner.add_log_message(content)
            cluster_id = result["cluster_id"]
            template_mined = result["template_mined"]
        else:
            # Fallback regex clustering
            template_mined = _regex_template_fallback(content)
            cluster_id = template_mined

        cluster_key = (incident_id, service_name, cluster_id)

        if cluster_key not in clusters:
            clusters[cluster_key] = {
                "incident_id": incident_id,
                "evidence_type": "application_log",
                "representation_type": "log_cluster",
                "content": template_mined,
                "source_ids": [],
                "timestamps": [],
                "service_name": service_name,
            }

        cluster = clusters[cluster_key]
        cluster["content"] = template_mined

        # Preserve original evidence IDs
        if evidence_id not in cluster["source_ids"]:
            cluster["source_ids"].append(evidence_id)

        # Preserve timestamps for range calculation
        timestamp_raw = item.get("timestamp")
        parsed_ts = parse_timestamp(timestamp_raw)
        if parsed_ts is not None:
            cluster["timestamps"].append((parsed_ts, str(timestamp_raw)))

    output: List[Dict[str, Any]] = []

    for cluster in clusters.values():
        timestamps = cluster["timestamps"]
        if timestamps:
            start_time = min(timestamps, key=lambda x: x[0])[1]
            end_time = max(timestamps, key=lambda x: x[0])[1]
        else:
            start_time = None
            end_time = None

        count = len(cluster["source_ids"])
        time_range = {"start": start_time, "end": end_time} if (start_time or end_time) else None

        output.append({
            "incident_id": cluster["incident_id"],
            "evidence_type": cluster["evidence_type"],
            "representation_type": cluster["representation_type"],
            "content": cluster["content"],
            "source_ids": cluster["source_ids"],
            "timestamp": None,
            "time_range": time_range,
            "service_name": cluster["service_name"],
            "metadata": {
                "cluster_count": count,
            },
        })

    return output


# Alias for compatibility with teammate's naming
preprocess_evidence = preprocess_logs
