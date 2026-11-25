"""Slack incident conversation preprocessor.

Cleans Slack noise/formatting, categorizes messages into findings/actions/observations,
groups by thread or 30-minute time-gap windows, and synthesizes structured conversation summaries.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
THREAD_GAP_MINUTES = 30  # fallback grouping when no thread_ts
MAX_MSG_CHARS = 300  # truncate very long messages in summaries

NOISE = {
    "ok", "okay", "k", "thanks", "thank you", "thx", "ty", "+1", "ack",
    "noted", "yes", "yep", "yup", "got it", "lgtm", "sure", "cool", "👍",
}

# Order matters: first match wins (finding/decision > action > observation).
CATEGORY_PATTERNS = [
    (
        "decision",
        re.compile(
            r"\b(decided|decision|we will|we'll go|let'?s go with|going with|agreed|"
            r"approved|approve|final call|plan is)\b",
            re.I,
        ),
    ),
    (
        "finding",
        re.compile(
            r"\b(was pointing to|wrong (cluster|context|env\w*|namespace)|accidentally|"
            r"by mistake|root cause|caused by|oh no|my mistake|typo|misconfigur\w*|"
            r"misspell\w*|required key)\b",
            re.I,
        ),
    ),
    (
        "action",
        re.compile(
            r"\b(restart(ed)?|roll(ed|ing)?[ -]?back|deploy(ed)?|scaled?|fixed|applied|"
            r"disabled|enabled|failover|failed over|mitigat\w*|i'?ll|i will|working on|"
            r"investigating|checking|running|reverted|patched|increased|decreased|"
            r"paged|escalat\w*|apply(ing)?|draft(ed)?|bump(ed)?|going to|opening)\b",
            re.I,
        ),
    ),
    (
        "observation",
        re.compile(
            r"\b(seeing|noticed|observed|error|errors|timeout|timeouts|latency|spike|"
            r"down|failing|failed|alert|cpu|memory|oom|5\d\d|500|crash\w*|"
            r"unavailable|slow|degraded|saturat\w*)\b",
            re.I,
        ),
    ),
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:  # treat naive timestamps as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_text(text: str) -> str:
    """Normalize Slack markup into plain readable text."""
    if not text:
        return ""
    text = re.sub(r"<@([A-Z0-9]+)>", r"@\1", text)  # <@U123> -> @U123
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)  # <#C1|chan> -> #chan
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)  # <url|label>
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)  # <url>
    text = re.sub(r":[a-z0-9_+\-]+:", "", text)  # :emoji:
    text = re.sub(r"[*_~`]", "", text)  # formatting chars
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_noise(text: str) -> bool:
    return text.lower().strip(" .!") in NOISE or len(text) == 0


def _categorize(text: str) -> str:
    pats = dict(CATEGORY_PATTERNS)
    if pats["finding"].search(text):  # root-cause clues always win
        return "finding"
    if re.search(
        r"\b(resolved|back to normal|recovered|all clear|running and healthy)\b",
        text,
        re.I,
    ):
        return "resolution"
    obs = pats["observation"]
    if "?" in text and obs.search(text):  # questions are observation/inquiry
        return "observation"
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.search(text):
            return name
    return "discussion"


def _fmt_time(dt: Optional[datetime], raw: Optional[str]) -> str:
    return dt.strftime("%H:%M") if dt else (raw or "??:??")


def _truncate(text: str, limit: int = MAX_MSG_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ----------------------------------------------------------------------------
# Grouping
# ----------------------------------------------------------------------------
def _group_messages(msgs: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group by (channel, thread_ts) when available, else by time gaps."""
    threaded: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    loose: List[Dict[str, Any]] = []

    for m in msgs:
        meta = m.get("metadata") or {}
        thread_ts = meta.get("thread_ts")
        if thread_ts:
            threaded[(m["incident_id"], meta.get("channel"), str(thread_ts))].append(m)
        else:
            loose.append(m)

    groups = list(threaded.values())

    # time-gap windowing for non-threaded messages
    loose.sort(key=lambda x: (x["_dt"] is None, x["_dt"] or datetime.min))
    current: List[Dict[str, Any]] = []
    for m in loose:
        if current:
            prev = current[-1]
            same_channel = (prev.get("metadata") or {}).get("channel") == (
                m.get("metadata") or {}
            ).get("channel")
            same_incident = prev["incident_id"] == m["incident_id"]
            gap_ok = (
                prev["_dt"]
                and m["_dt"]
                and (m["_dt"] - prev["_dt"]) <= timedelta(minutes=THREAD_GAP_MINUTES)
            )
            if not (same_channel and same_incident and gap_ok):
                groups.append(current)
                current = []
        current.append(m)
    if current:
        groups.append(current)

    for g in groups:
        g.sort(key=lambda x: (x["_dt"] is None, x["_dt"] or datetime.min))
    groups.sort(key=lambda g: (g[0]["_dt"] is None, g[0]["_dt"] or datetime.min))
    return groups


# ----------------------------------------------------------------------------
# Summarization
# ----------------------------------------------------------------------------
def _rule_based_summary(group: List[Dict[str, Any]]) -> str:
    """Deterministic fallback: categorized, timestamped bullets with evidence IDs."""
    order = [
        "observation",
        "finding",
        "action",
        "resolution",
        "decision",
        "discussion",
    ]
    by_cat: Dict[str, List[str]] = defaultdict(list)
    for m in group:
        line = f"[{_fmt_time(m['_dt'], m.get('timestamp'))}] {_truncate(m['_clean'])} ({m['evidence_id']})"
        by_cat[m["_cat"]].append(line)

    first, last = group[0], group[-1]
    header = (
        f"Slack thread with {len(group)} message(s) "
        f"from {_fmt_time(first['_dt'], first.get('timestamp'))} "
        f"to {_fmt_time(last['_dt'], last.get('timestamp'))}."
    )
    parts = [header]
    for cat in order:
        if by_cat[cat]:
            parts.append(
                f"{cat.capitalize()}s:" if cat != "discussion" else "Discussion:"
            )
            parts.extend(f"- {line}" for line in by_cat[cat])
    return "\n".join(parts)


def _build_prompt(group: List[Dict[str, Any]]) -> str:
    lines = []
    for m in group:
        meta_ = m.get("metadata") or {}
        user = meta_.get("user") or meta_.get("sender") or "unknown"
        lines.append(
            f"[{m.get('timestamp')}] ({m['evidence_id']}) <{user}> [{m['_cat']}] {m['_clean']}"
        )
    transcript = "\n".join(lines)
    return (
        "You are summarizing a Slack incident-response thread for a root-cause "
        "analysis system.\n"
        "Rules:\n"
        "1. Be concise (max ~120 words). Use only facts in the transcript; never invent.\n"
        "2. Preserve timestamps (HH:MM), actions taken, decisions made, observations, "
        "and open discussion points.\n"
        "3. After each point, cite the source evidence IDs in parentheses, e.g. (EV-201).\n"
        "4. Group into: Observations, Findings (root-cause clues), Actions, Resolution, Decisions, Discussion (skip empty ones).\n"
        "5. Output plain text bullets only.\n\n"
        f"Transcript:\n{transcript}"
    )


def _llm_summary(
    group: List[Dict[str, Any]], llm_fn: Callable[[str], str]
) -> Optional[str]:
    """Call the LLM; return None if the output is unusable (caller falls back)."""
    try:
        out = llm_fn(_build_prompt(group))
    except Exception:
        return None
    if not isinstance(out, str) or not out.strip():
        return None
    out = out.strip()
    valid_ids = {m["evidence_id"] for m in group}
    # must cite at least one real ID
    if not any(i in out for i in valid_ids):
        return None
    # must not cite IDs that look like ours but are not in this thread
    prefixes = set()
    for i in valid_ids:
        mt = re.match(r"[A-Za-z]+[-_]", str(i))
        if mt:
            prefixes.add(mt.group())
    for tok in re.findall(r"[A-Za-z][\w\-]*", out):
        if any(tok.startswith(pf) for pf in prefixes) and tok not in valid_ids:
            return None
    return out


# ----------------------------------------------------------------------------
# Main function
# ----------------------------------------------------------------------------
def preprocess_slack(
    evidence: List[Dict[str, Any]],
    llm_fn: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    """Convert normalized Slack evidence into concise, timestamped thread summaries.

    Parameters
    ----------
    evidence : list of normalized evidence dicts.
    llm_fn : optional callable(prompt: str) -> str.
             If None or if it fails, a deterministic rule-based summary is used.

    Returns
    -------
    list of output records adhering to the Sprint 2 chunk contracts.
    """
    if not evidence:
        return []

    prepared: List[Dict[str, Any]] = []
    noise_ids: Dict[str, List[str]] = defaultdict(list)

    for rec in evidence:
        # Accept slack messages regardless of DB upload/integration channel
        etype = (rec.get("evidence_type") or rec.get("source_type") or "").lower()
        if etype and not any(k in etype for k in ("slack", "chat", "message")):
            continue

        if not rec.get("evidence_id") or not rec.get("incident_id"):
            continue

        clean = _clean_text(rec.get("content") or "")
        if _is_noise(clean):
            noise_ids[str(rec["incident_id"])].append(str(rec["evidence_id"]))
            continue

        m = dict(rec)
        m["_clean"] = clean
        m["_dt"] = _parse_ts(rec.get("timestamp"))
        m["_cat"] = _categorize(clean)
        prepared.append(m)

    if not prepared:
        return []

    output: List[Dict[str, Any]] = []
    for group in _group_messages(prepared):
        summary = _llm_summary(group, llm_fn) if llm_fn else None
        method = "llm" if summary else "rule_based"
        if not summary:
            summary = _rule_based_summary(group)

        timed = [m for m in group if m["_dt"]]
        if timed:
            start = min(timed, key=lambda m: m["_dt"])["timestamp"]
            end = max(timed, key=lambda m: m["_dt"])["timestamp"]
        else:
            start = end = None
        time_range = {"start": start, "end": end} if start else None

        services = [m.get("service_name") for m in group if m.get("service_name")]
        service = Counter(services).most_common(1)[0][0] if services else None

        metas = [(m.get("metadata") or {}) for m in group]
        incident_id = str(group[0]["incident_id"])

        output.append(
            {
                "incident_id": incident_id,
                "evidence_type": "slack_message",
                "source_type": "slack",
                "representation_type": "slack_summary",
                "content": summary,
                "source_ids": [str(m["evidence_id"]) for m in group],
                "timestamp": (
                    group[0].get("timestamp") if len(group) == 1 else None
                ),
                "time_range": time_range,
                "service_name": service,
                "metadata": {
                    "message_count": len(group),
                    "category_counts": dict(Counter(m["_cat"] for m in group)),
                    "channel": next(
                        (x.get("channel") for x in metas if x.get("channel")),
                        None,
                    ),
                    "thread_ts": next(
                        (x.get("thread_ts") for x in metas if x.get("thread_ts")),
                        None,
                    ),
                    "participants": sorted(
                        {
                            x.get("user") or x.get("sender")
                            for x in metas
                            if x.get("user") or x.get("sender")
                        }
                    ),
                    "summarization_method": method,
                    "noise_filtered_ids": noise_ids.get(incident_id, []),
                },
            }
        )

    return output


# Alias for compatibility with external scripts
preprocess_evidence = preprocess_slack
