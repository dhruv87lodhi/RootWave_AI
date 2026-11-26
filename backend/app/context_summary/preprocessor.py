import json
import uuid
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple


def detect_source_metadata(filename: str, content: Any) -> Tuple[str, str]:
    """
    Deterministically identify the telemetry type and primary service from filename/content.
    """
    fname = filename.lower()
    if "incident" in fname:
        service = "unknown"
        if isinstance(content, dict):
            service = content.get("affected_service", "unknown")
        return "incident_metadata", service
    elif "jira" in fname:
        return "audit_logs", "jira"
    elif "k8s" in fname or "kubernetes" in fname:
        return "kubernetes_events", "kubernetes"
    elif "slack" in fname or "chat" in fname:
        return "chat_logs", "slack"
    elif "prometheus" in fname or "datadog" in fname or "metrics" in fname:
        return "metrics", "metrics-system"
    elif "pr" in fname or "github" in fname:
        return "code_changes", "github"
    else:
        return "system_logs", "infrastructure"


def extract_timestamps_and_services(content: Any) -> Tuple[List[str], List[str]]:
    """
    Recursively extract all ISO timestamps and system/service names from records.
    """
    timestamps = []
    services = []
    
    # ISO-8601 regex pattern
    iso_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    if k in ("event_time", "started_at", "timestamp") or iso_pattern.search(v):
                        match = iso_pattern.search(v)
                        if match:
                            timestamps.append(match.group(0))
                    if k in ("system", "affected_service", "service"):
                        services.append(v)
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(content)
    return timestamps, services


def check_clock_skew(timestamps: List[str]) -> str:
    """
    Verifies timezone offsets across timestamps.
    """
    if not timestamps:
        return "No timestamps available to assess clock skew."
    
    timezones = set()
    for ts in timestamps:
        if ts.endswith("Z"):
            timezones.add("UTC")
        elif "+" in ts or (len(ts) > 19 and "-" in ts[19:]):
            offset = ts[-6:]
            timezones.add(offset)
        else:
            timezones.add("Local/Unspecified")
            
    if len(timezones) == 1:
        tz = list(timezones)[0]
        return f"All timestamps provided in {tz} timezone. No evidence of clock skew detected across sources."
    else:
        return f"Multiple timezone formats detected: {', '.join(sorted(timezones))}. Timestamps should be normalized to UTC."


def preprocess_scenario_folder(data_folder: Path) -> Dict[str, Any]:
    """
    Layer 1: Deterministic Python Preprocessor.
    Processes all scenario telemetry files into structured, normalized metadata.
    """
    if not data_folder.exists() or not data_folder.is_dir():
        raise FileNotFoundError(f"Scenario directory not found: {data_folder}")

    files = sorted(list(data_folder.glob("*.json")))
    if not files:
        raise ValueError(f"No JSON telemetry files found in {data_folder}")

    raw_files_data = {}
    evidence_inventory = []
    all_timestamps = []
    all_services = set()
    telemetry_types = set()

    for idx, file_path in enumerate(files, start=1):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                content = json.load(f)
            except Exception as e:
                content = {"error": f"Failed to parse JSON: {str(e)}"}

        evidence_id = f"EVID-{idx:03d}"
        tel_type, service = detect_source_metadata(file_path.name, content)
        
        telemetry_types.add(tel_type)
        if service and service != "unknown":
            all_services.add(service)

        ts_list, svc_list = extract_timestamps_and_services(content)
        all_timestamps.extend(ts_list)
        all_services.update(svc_list)

        raw_files_data[file_path.name] = content
        evidence_inventory.append({
            "evidence_id": evidence_id,
            "source_name": file_path.name,
            "type": tel_type,
            "service": service,
            "raw_content": content
        })

    # Sort timestamps to calculate deterministic window
    valid_ts = sorted(all_timestamps)
    reported_window = f"{valid_ts[0]} to {valid_ts[-1]}" if valid_ts else "Unknown"
    clock_skew_notes = check_clock_skew(valid_ts)

    # Extract base incident metadata if present
    incident_content = raw_files_data.get("incident.json", {})
    incident_title = incident_content.get("title", "Service incident")
    severity = incident_content.get("severity", "SEV1")
    affected_service = incident_content.get("affected_service", list(all_services)[0] if all_services else "unknown")

    return {
        "incident_id": str(uuid.uuid4()),
        "title": incident_title,
        "severity": severity,
        "affected_service": affected_service,
        "reported_window": reported_window,
        "clock_skew_notes": clock_skew_notes,
        "total_sources": len(files),
        "services_covered": sorted(list(all_services)),
        "telemetry_types": sorted(list(telemetry_types)),
        "evidence_inventory": evidence_inventory,
        "raw_files_data": raw_files_data
    }
