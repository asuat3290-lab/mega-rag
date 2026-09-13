#!/usr/bin/env python3
"""Fail-soft protocol tracing for agent-driven MEGA research sessions."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TRACE_PROTOCOL = "mega-protocol-trace-v1"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = SCRIPT_DIR / "protocol_trace.db"
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def _trace_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not _TRACE_ID_RE.fullmatch(cleaned):
        raise ValueError(
            "trace_id must be 1-120 characters using letters, digits, '.', '_', ':', or '-'"
        )
    return cleaned


def _db_path() -> Path:
    configured = os.environ.get("MEGA_PROTOCOL_TRACE_DB", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=8)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS protocol_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            query TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_protocol_events_trace
        ON protocol_events(trace_id, event_id)
        """
    )
    return connection


def record_protocol_event(
    trace_id: str | None,
    operation: str,
    *,
    query: str = "",
    payload: dict[str, Any] | None = None,
) -> dict:
    """Record one compact event; tracing failures never fail the research call."""
    if not trace_id:
        return {"recorded": False, "reason": "trace_id_not_supplied"}
    try:
        normalized = _trace_id(trace_id)
        serialized = json.dumps(
            payload or {}, ensure_ascii=False, sort_keys=True, default=str
        )
        created_at = datetime.now(timezone.utc).isoformat()
        with closing(_connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO protocol_events(
                    trace_id, operation, query, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    str(operation or "unknown")[:60],
                    str(query or "")[:1000],
                    serialized,
                    created_at,
                ),
            )
            connection.commit()
        return {
            "recorded": True,
            "trace_id": normalized,
            "event_id": cursor.lastrowid,
        }
    except Exception as exc:
        return {
            "recorded": False,
            "trace_id": str(trace_id or ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _load_events(trace_id: str) -> list[dict]:
    normalized = _trace_id(trace_id)
    with closing(_connect()) as connection:
        rows = connection.execute(
            """
            SELECT event_id, operation, query, payload_json, created_at
            FROM protocol_events
            WHERE trace_id = ?
            ORDER BY event_id
            """,
            (normalized,),
        ).fetchall()
    events = []
    for event_id, operation, query, payload_json, created_at in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            payload = {"trace_payload_error": "invalid_json"}
        events.append(
            {
                "event_id": event_id,
                "operation": operation,
                "query": query,
                "payload": payload,
                "created_at": created_at,
            }
        )
    return events


def build_protocol_report(trace_id: str) -> dict:
    """Summarize process adherence without blocking agent discretion."""
    normalized = _trace_id(trace_id)
    events = _load_events(normalized)
    operation_counts = Counter(event["operation"] for event in events)
    searches = [event for event in events if event["operation"] == "search"]
    research_runs = [
        event for event in events if event["operation"] == "research_run"
    ]
    register_consulted = any(
        bool(event["payload"].get("register_consulted")) for event in events
    )
    register_hits = sum(
        int(event["payload"].get("register_hits", 0) or 0) for event in events
    )
    refinement_used = any(
        bool(event["payload"].get("refinement_used")) for event in events
    )
    evidence_expanded = sum(
        int(event["payload"].get("evidence_expanded", 0) or 0)
        for event in events
    )

    focus_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "candidate_hits": 0,
            "final_hits": 0,
            "qualified_final_hits": 0,
        }
    )
    reason_codes = []
    warnings = []
    for event in events:
        payload = event["payload"]
        advice = payload.get("planning_advice") or {}
        reason_codes.extend(advice.get("reason_codes") or [])
        focus = payload.get("focus_diagnostics") or {}
        term_rows = focus.get("per_term") or []
        if isinstance(term_rows, dict):
            term_rows = [
                {"term": term, **(counts or {})}
                for term, counts in term_rows.items()
            ]
        for counts in term_rows:
            term = str((counts or {}).get("term") or "")
            if not term:
                continue
            target = focus_totals[term]
            for field in target:
                target[field] += int((counts or {}).get(field, 0) or 0)
        warnings.extend(focus.get("warnings") or [])

    if len(searches) >= 4 and not research_runs:
        warnings.append(
            "multiple_sequential_searches_without_research_run"
        )
    if "flat_multi_term_query_needs_focus" in reason_codes and not any(
        bool(event["payload"].get("focus_explicit")) for event in searches
    ):
        warnings.append("flat_multi_term_search_without_explicit_focus")
    if (
        "unmapped_lexical_concept_needs_semantic_refinement" in reason_codes
        and not refinement_used
    ):
        warnings.append("unmapped_lexical_concept_not_refined")
    if (
        "unmapped_lexical_concept_needs_semantic_refinement" in reason_codes
        and not register_consulted
    ):
        warnings.append("sachregister_not_consulted_for_unmapped_concept")

    warnings = list(dict.fromkeys(str(value) for value in warnings if value))
    return {
        "protocol": TRACE_PROTOCOL,
        "trace_id": normalized,
        "found": bool(events),
        "event_count": len(events),
        "operation_counts": dict(operation_counts),
        "searches_run": len(searches),
        "research_run_used": bool(research_runs),
        "sachregister_consulted": register_consulted,
        "sachregister_hits": register_hits,
        "refinement_used": refinement_used,
        "evidence_expanded": evidence_expanded,
        "core_term_hit_rate": dict(focus_totals),
        "planning_reason_codes": list(dict.fromkeys(reason_codes)),
        "warnings": warnings,
        "advisory_status": "review_recommended" if warnings else "clean",
        "events": [
            {
                "event_id": event["event_id"],
                "operation": event["operation"],
                "query": event["query"],
                "created_at": event["created_at"],
            }
            for event in events
        ],
    }

