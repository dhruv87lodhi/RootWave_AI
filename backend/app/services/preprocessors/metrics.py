"""Metric evidence preprocessor.

Implements statistical metric event detection:
- Rolling Z-score anomaly detection (sudden spikes)
- Mean-shift changepoint detection (permanent level shifts)
- Time-windowed consolidation (deduplicating related metric events)
- Synthesis into compact metric anomaly RAG chunks.
"""

from collections import defaultdict
from datetime import datetime, timezone
import math
from statistics import mean, stdev
from typing import Any, Dict, List, Optional


def parse_timestamp(timestamp: Optional[str]) -> datetime:
    """Parses timestamp into UTC datetime, using max datetime for missing values."""
    if timestamp is None:
        return datetime.max.replace(tzinfo=timezone.utc)

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return datetime.max.replace(tzinfo=timezone.utc)


def organize_metrics(valid_records: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    """Groups metric observations by (incident_id, service_name, metric_name)."""
    organized = defaultdict(list)

    for record in valid_records:
        metadata = record.get("metadata") or {}

        metric_name = (
            metadata.get("metric_name")
            or record.get("metric_name")
            or record.get("content")
        )
        if not isinstance(metric_name, str) or not metric_name.strip():
            continue

        metric_name = metric_name.strip()

        # Extract numeric value (supports "value", "val", or key named after metric)
        value = metadata.get("value")
        if value is None:
            value = metadata.get("val")
        if value is None and metric_name in metadata:
            value = metadata.get(metric_name)

        # Check numeric validity
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            continue

        unit = metadata.get("unit")
        service_name = record.get("service_name")
        incident_id = str(record["incident_id"])

        group_key = (incident_id, service_name, metric_name)

        observation = {
            "evidence_id": str(record["evidence_id"]),
            "value": float(value),
            "unit": unit,
            "timestamp": record.get("timestamp"),
        }
        organized[group_key].append(observation)

    # Sort observations chronologically
    for group_key in organized:
        organized[group_key].sort(
            key=lambda item: parse_timestamp(item.get("timestamp"))
        )

    return dict(organized)


def rolling_z_score(
    observations: List[Dict[str, Any]], window_size: int = 3, threshold: float = 3.0
) -> List[Dict[str, Any]]:
    """Detects sudden point anomalies where value deviates significantly from rolling baseline."""
    anomalies = []

    for i in range(window_size, len(observations)):
        previous_values = [
            item["value"] for item in observations[i - window_size : i]
        ]
        current = observations[i]["value"]

        baseline_mean = mean(previous_values)
        baseline_std = stdev(previous_values) if len(previous_values) > 1 else 0

        if baseline_std == 0:
            continue

        z_score = abs((current - baseline_mean) / baseline_std)

        if z_score >= threshold:
            anomalies.append(
                {
                    "detection_type": "anomaly",
                    "method": "rolling_z_score",
                    "timestamp": observations[i]["timestamp"],
                    "value": current,
                    "score": round(z_score, 2),
                    "source_ids": [observations[i]["evidence_id"]],
                }
            )

    return anomalies


def detect_changepoints(
    observations: List[Dict[str, Any]], min_segment_size: int = 2, threshold: float = 3.0
) -> List[Dict[str, Any]]:
    """Detects structural level shifts in metric series."""
    changepoints = []
    n = len(observations)

    if n < min_segment_size * 2:
        return changepoints

    best_score = 0
    best_split = None

    for split in range(min_segment_size, n - min_segment_size + 1):
        before = [item["value"] for item in observations[:split]]
        after = [item["value"] for item in observations[split:]]

        before_mean = mean(before)
        after_mean = mean(after)

        denom = len(before) + len(after) - 2
        if denom <= 0:
            continue

        combined_std = math.sqrt(
            (
                sum((x - before_mean) ** 2 for x in before)
                + sum((x - after_mean) ** 2 for x in after)
            )
            / denom
        )

        if combined_std == 0:
            if before_mean != after_mean:
                score = float("inf")
            else:
                continue
        else:
            score = abs(after_mean - before_mean) / combined_std

        if score > best_score:
            best_score = score
            best_split = split

    if best_split is not None and best_score >= threshold:
        before = observations[best_split - min_segment_size : best_split]
        after = observations[best_split : best_split + min_segment_size]

        changepoints.append(
            {
                "detection_type": "changepoint",
                "method": "mean_shift",
                "timestamp": after[0]["timestamp"],
                "before_mean": round(mean(item["value"] for item in before), 2),
                "after_mean": round(mean(item["value"] for item in after), 2),
                "score": round(best_score, 2) if math.isfinite(best_score) else 999.0,
                "source_ids": [item["evidence_id"] for item in before + after],
            }
        )

    return changepoints


def detect_metric_events(
    organized_metrics: Dict[tuple, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Runs rolling Z-score and changepoint detection over all metric groups."""
    all_detections = []

    for group_key, observations in organized_metrics.items():
        incident_id, service_name, metric_name = group_key

        anomalies = rolling_z_score(observations)
        changepoints = detect_changepoints(observations)

        for event in anomalies + changepoints:
            event["incident_id"] = incident_id
            event["service_name"] = service_name
            event["metric_name"] = metric_name
            all_detections.append(event)

    return all_detections


def consolidate_detections(
    detections: List[Dict[str, Any]], time_window_minutes: float = 5.0
) -> List[Dict[str, Any]]:
    """Merges detections of the same metric within a short time proximity."""
    if not detections:
        return []

    # Sort detections by timestamp
    valid_detections = [d for d in detections if d.get("timestamp")]
    valid_detections.sort(key=lambda x: parse_timestamp(x["timestamp"]))

    consolidated: List[Dict[str, Any]] = []

    for detection in valid_detections:
        matched = False

        for existing in consolidated:
            same_group = (
                existing["incident_id"] == detection["incident_id"]
                and existing["service_name"] == detection["service_name"]
                and existing["metric_name"] == detection["metric_name"]
            )
            if not same_group:
                continue

            t1 = parse_timestamp(existing["timestamp"])
            t2 = parse_timestamp(detection["timestamp"])
            time_difference = abs((t2 - t1).total_seconds()) / 60.0

            if time_difference <= time_window_minutes:
                existing["source_ids"] = sorted(
                    list(set(existing["source_ids"] + detection["source_ids"]))
                )
                existing["methods"] = sorted(
                    list(set(existing["methods"] + [detection["method"]]))
                )
                existing["detection_types"] = sorted(
                    list(set(existing["detection_types"] + [detection["detection_type"]]))
                )
                matched = True
                break

        if not matched:
            consolidated.append(
                {
                    "incident_id": detection["incident_id"],
                    "service_name": detection["service_name"],
                    "metric_name": detection["metric_name"],
                    "timestamp": detection["timestamp"],
                    "source_ids": list(detection["source_ids"]),
                    "methods": [detection["method"]],
                    "detection_types": [detection["detection_type"]],
                }
            )

    return consolidated


def generate_metric_output(
    consolidated: List[Dict[str, Any]], evidence: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Synthesizes consolidated detections into final RAG chunk dictionaries."""
    final_output = []
    evidence_lookup = {str(r["evidence_id"]): r for r in evidence}

    for finding in consolidated:
        source_ids = sorted(list(set(finding["source_ids"])))
        source_records = [
            evidence_lookup[sid] for sid in source_ids if sid in evidence_lookup
        ]

        timestamps = [
            parse_timestamp(r.get("timestamp"))
            for r in source_records
            if r.get("timestamp")
        ]

        if timestamps:
            start_time = min(timestamps).isoformat()
            end_time = max(timestamps).isoformat()
        else:
            start_time = None
            end_time = None

        metric_name = finding["metric_name"]
        service_name = finding["service_name"] or "unknown service"
        detection_types = finding["detection_types"]

        if "anomaly" in detection_types and "changepoint" in detection_types:
            summary = (
                f"Unusual {metric_name} behaviour detected in {service_name}: "
                f"sudden anomaly spike combined with a significant changepoint level shift."
            )
        elif "anomaly" in detection_types:
            summary = f"Anomalous spike in {metric_name} detected in {service_name}."
        else:
            summary = (
                f"A significant changepoint level shift in {metric_name} "
                f"was detected in {service_name}."
            )

        output_record = {
            "incident_id": finding["incident_id"],
            "evidence_type": "metric",
            "source_type": "metrics",
            "representation_type": "metric_anomaly",
            "content": summary,
            "source_ids": source_ids,
            "timestamp": finding["timestamp"],
            "time_range": {
                "start": start_time,
                "end": end_time,
            },
            "service_name": finding["service_name"],
            "metadata": {
                "metric_name": metric_name,
                "detection_methods": finding["methods"],
                "detection_types": detection_types,
            },
        }
        final_output.append(output_record)

    return final_output


def preprocess_metrics(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Main entrypoint: converts metric observations into anomaly/changepoint chunks.

    Parameters
    ----------
    evidence : list[dict]
        List of normalized metric evidence records.

    Returns
    -------
    list[dict]
        List of preprocessed metric anomaly chunks conforming to Sprint 2 contracts.
    """
    if not evidence:
        return []

    # Filter for metric evidence
    metric_records = []
    for r in evidence:
        etype = (r.get("evidence_type") or r.get("source_type") or "").lower()
        if etype and not any(k in etype for k in ("metric", "telemetry", "gauge", "counter")):
            continue
        metric_records.append(r)

    if not metric_records:
        return []

    # 1. Organize
    organized = organize_metrics(metric_records)
    if not organized:
        return []

    # 2. Detect
    detections = detect_metric_events(organized)
    if not detections:
        # If no statistical anomaly or changepoint is detected (e.g. few points or steady baseline),
        # create a compact series summary so metric evidence is preserved and not discarded.
        fallback_chunks = []
        for (inc_id, svc, m_name), obs in organized.items():
            s_ids = [o["evidence_id"] for o in obs]
            values = [o["value"] for o in obs]
            latest_val = values[-1]
            unit = obs[0].get("unit") or ""
            summary = (
                f"Metric telemetry summary for {m_name} ({svc or 'service'}): "
                f"observed {len(obs)} data point(s), latest reading {latest_val} {unit}."
            ).strip()

            fallback_chunks.append(
                {
                    "incident_id": inc_id,
                    "evidence_type": "metric",
                    "source_type": "metrics",
                    "representation_type": "metric_anomaly",
                    "content": summary,
                    "source_ids": s_ids,
                    "timestamp": obs[0].get("timestamp"),
                    "time_range": (
                        {
                            "start": obs[0].get("timestamp"),
                            "end": obs[-1].get("timestamp"),
                        }
                        if len(obs) > 1
                        else None
                    ),
                    "service_name": svc,
                    "metadata": {
                        "metric_name": m_name,
                        "points_count": len(obs),
                        "mean_value": round(mean(values), 2),
                        "detection_types": ["summary"],
                    },
                }
            )
        return fallback_chunks

    # 3. Consolidate
    consolidated = consolidate_detections(detections)

    # 4. Generate Output
    return generate_metric_output(consolidated, metric_records)


# Alias for compatibility with external scripts
preprocess_evidence = preprocess_metrics