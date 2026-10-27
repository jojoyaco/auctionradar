"""History presentation contract, separate from raw evidence and private internals."""

import math
import re
from datetime import datetime

from .urls import sanitise

_PRIVATE_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookies",
    "headers",
    "config",
    "debug_config",
    "requests",
    "raw_path",
    "body",
}
_COUNTERS = ("ingested", "new", "updated", "skipped", "preserved_reviews")


def readable_message(value, depth=0):
    """Render heterogeneous messages without dumping JSON or coercing objects."""
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        return (text.replace("_", " ") if re.fullmatch(r"[a-z][a-z0-9_]*", text) else text)[:2000]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value) if math.isfinite(value) else ""
    if isinstance(value, (list, tuple)):
        return " · ".join(filter(None, (readable_message(v, depth + 1) for v in value[:20])))[:2000]
    if isinstance(value, dict):
        for key in ("message", "detail", "reason", "error", "description", "text"):
            if key in value:
                message = readable_message(value[key], depth + 1)
                quantities = [
                    f"{k.replace('_', ' ')}: {readable_message(value[k], depth + 1)}"
                    for k in ("limit", "max_pages", "max_details", "max_documents")
                    if k in value
                ]
                return " · ".join(filter(None, [message, *quantities]))[:2000]
        parts = []
        for key, child in list(value.items())[:12]:
            if str(key).casefold() in _PRIVATE_KEYS:
                continue
            text = readable_message(child, depth + 1)
            if text:
                parts.append(f"{str(key).replace('_', ' ')}: {text}")
        return " · ".join(parts)[:2000]
    return ""


def _number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else 0
    )


def source_report(value, run_status):
    if isinstance(value, str):
        value = {
            "source_id": value,
            "status": "queued" if run_status in ("queued", "running") else "unknown",
        }
    if not isinstance(value, dict):
        return None
    raw = value.get("limitations") or []
    if not isinstance(raw, list):
        raw = [raw]
    limitations = [readable_message(v) for v in raw[:100]]
    identifier = value.get("source_id") or value.get("id") or "unknown"
    if not isinstance(identifier, str):
        identifier = "unknown"
    return {
        "source_id": identifier[:120],
        "name": value.get("name") if isinstance(value.get("name"), str) else None,
        "source_url": sanitise(value.get("source_url")),
        "status": value.get("status") if isinstance(value.get("status"), str) else "unknown",
        "depth": value.get("depth") if isinstance(value.get("depth"), str) else None,
        "items_count": _number(value.get("items_count")),
        "limitations": list(dict.fromkeys(text for text in limitations if text)),
    }


def present_summary(raw, status, names=None):
    if not isinstance(raw, dict):
        raw = {}
    ingest = raw.get("ingest") if isinstance(raw.get("ingest"), dict) else raw
    summary = {"ingest": {key: _number(ingest.get(key)) for key in _COUNTERS}}
    for key in ("source_status_counts", "selection_counts"):
        values = ingest.get(key) or raw.get(key) or {}
        summary["ingest"][key] = (
            {
                str(k): _number(v)
                for k, v in values.items()
                if str(k).casefold() not in _PRIVATE_KEYS
            }
            if isinstance(values, dict)
            else {}
        )
    sources = raw.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    reports = [source_report(source, status) for source in sources[:200]]
    summary["sources"] = [source for source in reports if source is not None]
    names = names or {}
    health = {
        key: 0
        for key in (
            "total",
            "with_results",
            "access_only",
            "blocked",
            "error",
            "disabled",
            "ineligible",
            "limited",
            "pending",
            "not_selected",
        )
    }
    common = []
    for source in summary["sources"]:
        source["name"] = names.get(source["source_id"]) or source.get("name") or source["source_id"]
        state = source["status"]
        if state in ("queued", "running"):
            group = "pending"
        elif state in ("not_selected", "asset_not_applicable"):
            group = "not_selected"
        elif state == "disabled":
            group = "disabled"
        elif state == "eligibility_unknown":
            group = "ineligible"
        elif state in ("blocked", "authentication_required"):
            group = "blocked"
        elif state == "error":
            group = "error"
        elif source["items_count"] > 0:
            group = "with_results"
        elif source.get("depth") in ("access_only", "none", None):
            group = "access_only"
        else:
            group = "limited"
        source["health"] = group
        health[group] += 1
        health["total"] += 1
        unique = []
        for text in source["limitations"]:
            if text.startswith("Inventario no exhaustivo:"):
                if text not in common:
                    common.append(text)
            else:
                unique.append(text)
        source["limitations"] = unique
    summary["health"] = health
    summary["common_limitations"] = common
    return summary


def duration_seconds(started, finished):
    if not started or not finished:
        return None
    try:
        delta = datetime.fromisoformat(finished.replace("Z", "+00:00")) - datetime.fromisoformat(
            started.replace("Z", "+00:00")
        )
        return round(max(0, delta.total_seconds()), 2)
    except (ValueError, TypeError, AttributeError):
        return None
