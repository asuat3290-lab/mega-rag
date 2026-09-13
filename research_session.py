#!/usr/bin/env python3
"""Persistent fail-closed workflow state for formal MEGA research sessions."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SESSION_PROTOCOL = "mega-research-session-v1"
PLANNED = "PLANNED"
NEEDS_REFINEMENT = "NEEDS_REFINEMENT"
RETRIEVED = "RETRIEVED"
RETRIEVAL_GAP = "RETRIEVAL_GAP"
EVIDENCE_QUALIFIED = "EVIDENCE_QUALIFIED"
EVIDENCE_EXPANDED = "EVIDENCE_EXPANDED"
REPORT_VALIDATED = "REPORT_VALIDATED"
COMPLETE = "COMPLETE"
SESSION_STATES = {
    PLANNED,
    NEEDS_REFINEMENT,
    RETRIEVED,
    RETRIEVAL_GAP,
    EVIDENCE_QUALIFIED,
    EVIDENCE_EXPANDED,
    REPORT_VALIDATED,
    COMPLETE,
}

_ALLOWED_TRANSITIONS = {
    PLANNED: {RETRIEVED},
    NEEDS_REFINEMENT: {RETRIEVED},
    RETRIEVED: {NEEDS_REFINEMENT, RETRIEVAL_GAP, EVIDENCE_QUALIFIED},
    RETRIEVAL_GAP: {RETRIEVED},
    EVIDENCE_QUALIFIED: {RETRIEVED, EVIDENCE_EXPANDED},
    EVIDENCE_EXPANDED: {RETRIEVED, EVIDENCE_EXPANDED, REPORT_VALIDATED},
    REPORT_VALIDATED: {COMPLETE},
    COMPLETE: set(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_session_db() -> Path:
    configured = os.environ.get("MEGA_RESEARCH_SESSION_DB")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parent / "research_sessions.db"
    )


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path).expanduser().resolve() if db_path else default_session_db()


@contextmanager
def _connect(db_path: str | Path | None = None):
    path = _db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_sessions (
                session_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                workflow_kind TEXT NOT NULL,
                state TEXT NOT NULL,
                index_version TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_session_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES research_sessions(session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_research_session_events_session
                ON research_session_events(session_id, event_id);
            """
        )
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode_row(row: sqlite3.Row) -> dict:
    payload = json.loads(row["payload_json"])
    return {
        "protocol": SESSION_PROTOCOL,
        "session_id": row["session_id"],
        "question": row["question"],
        "workflow_kind": row["workflow_kind"],
        "state": row["state"],
        "index_version": row["index_version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        **payload,
    }


def _next_action(state: str) -> dict:
    actions = {
        PLANNED: {
            "operation": "research_session_continue",
            "required": True,
            "instruction": "Run the planned retrieval without bypassing the session.",
        },
        NEEDS_REFINEMENT: {
            "operation": "research_session_continue",
            "required": True,
            "instruction": (
                "Supply a research refinement, or explicit focus/context/volume hints, "
                "then rerun retrieval."
            ),
        },
        RETRIEVED: {
            "operation": "wait_for_gate_evaluation",
            "required": True,
            "instruction": "This is an internal transition state.",
        },
        RETRIEVAL_GAP: {
            "operation": "research_session_continue",
            "required": True,
            "instruction": "Repair recall, scope, or semantic qualification and retrieve again.",
        },
        EVIDENCE_QUALIFIED: {
            "operation": "research_session_expand",
            "required": True,
            "instruction": "Select and expand the evidence that will support the report.",
        },
        EVIDENCE_EXPANDED: {
            "operation": "research_session_finalize",
            "required": True,
            "instruction": "Draft structured claims using only expanded evidence, then validate.",
        },
        REPORT_VALIDATED: {
            "operation": "issue_completion_receipt",
            "required": True,
            "instruction": "This is an internal transition state.",
        },
        COMPLETE: {
            "operation": None,
            "required": False,
            "instruction": "Formal research workflow completed; retain the receipt with the report.",
        },
    }
    return actions[state]


def public_session(session: dict, *, include_plan: bool = True) -> dict:
    state = session["state"]
    output = {
        key: session.get(key)
        for key in (
            "protocol", "session_id", "question", "workflow_kind", "state",
            "index_version", "created_at", "updated_at", "planner_mode",
            "planning_advice", "source_artifact_path", "retrieval_summary",
            "expanded_evidence", "artifact_revisions", "active_revision",
            "completion_receipt",
        )
        if session.get(key) is not None
    }
    if include_plan:
        output["research_plan"] = session.get("research_plan")
    output["answer_allowed"] = state in {
        EVIDENCE_EXPANDED, REPORT_VALIDATED, COMPLETE
    }
    output["completion_allowed"] = state == COMPLETE
    output["next_action"] = _next_action(state)
    return output


def create_session(
    question: str,
    *,
    workflow_kind: str,
    state: str,
    index_version: str | None,
    payload: dict,
    db_path: str | Path | None = None,
    session_id: str | None = None,
) -> dict:
    if state not in {PLANNED, NEEDS_REFINEMENT}:
        raise ValueError(f"invalid initial research-session state: {state}")
    identifier = session_id or f"mrs_{secrets.token_hex(8)}"
    now = _now_iso()
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO research_sessions(
                session_id, question, workflow_kind, state, index_version,
                payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier, question, workflow_kind, state, index_version,
                serialized, now, now,
            ),
        )
        connection.execute(
            """
            INSERT INTO research_session_events(
                session_id, event_type, from_state, to_state, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identifier, "session_started", None, state,
                json.dumps({"planning_advice": payload.get("planning_advice", {})},
                           ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
    return get_session(identifier, db_path=db_path)


def get_session(session_id: str, *, db_path: str | Path | None = None) -> dict:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        raise KeyError(f"research session not found: {session_id}")
    return _decode_row(row)


def transition_session(
    session_id: str,
    to_state: str,
    *,
    event_type: str,
    updates: dict | None = None,
    event_payload: dict | None = None,
    expected_states: Iterable[str] | None = None,
    db_path: str | Path | None = None,
) -> dict:
    if to_state not in SESSION_STATES:
        raise ValueError(f"unknown research-session state: {to_state}")
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM research_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"research session not found: {session_id}")
        current = str(row["state"])
        expected = set(expected_states or [])
        if expected and current not in expected:
            raise ValueError(
                f"research session {session_id} is {current}; expected one of {sorted(expected)}"
            )
        if to_state != current and to_state not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"invalid research-session transition: {current} -> {to_state}")
        payload = json.loads(row["payload_json"])
        payload.update(updates or {})
        now = _now_iso()
        connection.execute(
            """
            UPDATE research_sessions
            SET state = ?, payload_json = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                to_state,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now,
                session_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO research_session_events(
                session_id, event_type, from_state, to_state, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, event_type, current, to_state,
                json.dumps(event_payload or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
    return get_session(session_id, db_path=db_path)


def append_session_event(
    session_id: str,
    event_type: str,
    *,
    payload: dict | None = None,
    db_path: str | Path | None = None,
) -> dict:
    session = get_session(session_id, db_path=db_path)
    return transition_session(
        session_id,
        session["state"],
        event_type=event_type,
        event_payload=payload,
        db_path=db_path,
    )


def session_events(
    session_id: str,
    *,
    limit: int = 100,
    db_path: str | Path | None = None,
) -> list[dict]:
    get_session(session_id, db_path=db_path)
    bounded = max(1, min(int(limit), 500))
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT event_id, event_type, from_state, to_state, payload_json, created_at
            FROM research_session_events
            WHERE session_id = ?
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (session_id, bounded),
        ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]