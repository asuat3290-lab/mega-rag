#!/usr/bin/env python3
"""Human-governed persistent memory for validated MEGA planning refinements."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
DEFAULT_DB = Path(CONFIG["paths"]["cache_db"])
MEMORY_SCHEMA_VERSION = "planning-memory-v1"
MEMORY_KINDS = {"query_plan", "research_plan"}
MEMORY_STATUSES = {"proposed", "corpus_validated", "promoted", "rejected"}


def _now() -> str:
    return datetime.now().isoformat()


def _normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKC", str(question or "")).casefold()
    return " ".join(value.split())


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_memory(db_path: str | Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS planning_memory (
                memory_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                memory_kind TEXT NOT NULL,
                normalized_question TEXT NOT NULL,
                question TEXT NOT NULL,
                planner_version TEXT,
                index_version TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                validation_json TEXT,
                review_note TEXT,
                reviewer TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                use_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_planning_memory_lookup
            ON planning_memory(
                memory_kind, normalized_question, planner_version, status
            );
            CREATE INDEX IF NOT EXISTS idx_planning_memory_status
            ON planning_memory(status, updated_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _validate_kind(kind: str) -> str:
    value = str(kind or "").strip()
    if value not in MEMORY_KINDS:
        raise ValueError(f"unsupported planning memory kind: {kind}")
    return value


def _stable_id(kind: str, question: str, planner_version: str, payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = "|".join(
        (_validate_kind(kind), _normalize_question(question), planner_version, serialized)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _decode_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    output = dict(row)
    output["payload"] = json.loads(output.pop("payload_json"))
    validation = output.pop("validation_json")
    output["validation"] = json.loads(validation) if validation else None
    return output


def record_candidate(
    kind: str,
    question: str,
    payload: dict,
    *,
    planner_version: str = "",
    index_version: str = "",
    source: str = "agent_supplied",
    db_path: str | Path | None = None,
) -> str:
    """Store a schema-validated proposal without making it reusable."""
    kind = _validate_kind(kind)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("planning memory payload must be a non-empty object")
    question = str(question or "").strip()
    if not question:
        raise ValueError("planning memory question must not be empty")
    init_memory(db_path)
    memory_id = _stable_id(kind, question, planner_version, payload)
    now = _now()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO planning_memory(
                memory_id, schema_version, memory_kind, normalized_question,
                question, planner_version, index_version, source, status,
                payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                index_version=excluded.index_version,
                source=excluded.source,
                updated_at=excluded.updated_at,
                status=CASE
                    WHEN planning_memory.status IN ('promoted', 'rejected')
                    THEN planning_memory.status ELSE planning_memory.status END
            """,
            (
                memory_id,
                MEMORY_SCHEMA_VERSION,
                kind,
                _normalize_question(question),
                question,
                planner_version,
                index_version,
                str(source or "agent_supplied"),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return memory_id


def get_memory(memory_id: str, *, db_path: str | Path | None = None) -> dict | None:
    init_memory(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM planning_memory WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return _decode_row(row)
    finally:
        conn.close()


def find_promoted(
    kind: str,
    question: str,
    *,
    planner_version: str = "",
    db_path: str | Path | None = None,
) -> dict | None:
    """Return only explicitly promoted memory for an exact normalized question."""
    kind = _validate_kind(kind)
    init_memory(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM planning_memory
            WHERE memory_kind=? AND normalized_question=? AND status='promoted'
              AND planner_version=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (kind, _normalize_question(question), planner_version),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE planning_memory
            SET last_used_at=?, use_count=use_count+1
            WHERE memory_id=?
            """,
            (_now(), row["memory_id"]),
        )
        conn.commit()
        return _decode_row(row)
    finally:
        conn.close()


def mark_corpus_validation(
    memory_id: str,
    validation: dict,
    *,
    passed: bool,
    db_path: str | Path | None = None,
) -> dict:
    """Record corpus presence checks; this never promotes a proposal."""
    record = get_memory(memory_id, db_path=db_path)
    if not record:
        raise KeyError(f"planning memory not found: {memory_id}")
    if record["status"] in {"promoted", "rejected"}:
        raise ValueError(f"cannot validate memory in status {record['status']}")
    status = "corpus_validated" if passed else "proposed"
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE planning_memory
            SET status=?, validation_json=?, updated_at=?
            WHERE memory_id=?
            """,
            (
                status,
                json.dumps(validation or {}, ensure_ascii=False, sort_keys=True),
                _now(),
                memory_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_memory(memory_id, db_path=db_path)


def review_memory(
    memory_id: str,
    action: str,
    *,
    note: str,
    reviewer: str = "user",
    db_path: str | Path | None = None,
) -> dict:
    """Promote or reject a candidate through an explicit human review step."""
    action = str(action or "").strip().casefold()
    if action not in {"promote", "reject"}:
        raise ValueError("planning memory action must be promote or reject")
    note = str(note or "").strip()
    if not note:
        raise ValueError("planning memory review requires a note")
    record = get_memory(memory_id, db_path=db_path)
    if not record:
        raise KeyError(f"planning memory not found: {memory_id}")
    if action == "promote" and record["status"] != "corpus_validated":
        raise ValueError("only corpus_validated memory can be promoted")
    status = "promoted" if action == "promote" else "rejected"
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE planning_memory
            SET status=?, review_note=?, reviewer=?, updated_at=?
            WHERE memory_id=?
            """,
            (status, note, str(reviewer or "user"), _now(), memory_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_memory(memory_id, db_path=db_path)


def list_memory(
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict]:
    if kind:
        _validate_kind(kind)
    if status and status not in MEMORY_STATUSES:
        raise ValueError(f"unsupported planning memory status: {status}")
    init_memory(db_path)
    clauses = []
    params: list[Any] = []
    if kind:
        clauses.append("memory_kind=?")
        params.append(kind)
    if status:
        clauses.append("status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM planning_memory {where}
            ORDER BY updated_at DESC LIMIT ?
            """,
            [*params, max(1, min(int(limit), 500))],
        ).fetchall()
        return [_decode_row(row) for row in rows]
    finally:
        conn.close()


def memory_status(*, db_path: str | Path | None = None) -> dict:
    init_memory(db_path)
    conn = _connect(db_path)
    try:
        by_status = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM planning_memory GROUP BY status"
            )
        )
        by_kind = dict(
            conn.execute(
                "SELECT memory_kind, COUNT(*) FROM planning_memory GROUP BY memory_kind"
            )
        )
        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "total": sum(int(value) for value in by_status.values()),
            "by_status": by_status,
            "by_kind": by_kind,
            "reusable": int(by_status.get("promoted", 0)),
        }
    finally:
        conn.close()


def _collect_terms_and_volumes(payload: Any) -> tuple[list[str], list[dict]]:
    terms: list[str] = []
    volumes: list[dict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"core_terms", "historical_variants"} and isinstance(item, list):
                    terms.extend(str(term).strip() for term in item if str(term).strip())
                elif key == "target_volumes" and isinstance(item, list):
                    volumes.extend(volume for volume in item if isinstance(volume, dict))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    term_output = list(dict.fromkeys(terms))
    volume_output = list(
        {
            json.dumps(volume, ensure_ascii=False, sort_keys=True): volume
            for volume in volumes
        }.values()
    )
    return term_output, volume_output


def validate_against_corpus(
    memory_id: str,
    *,
    db_path: str | Path | None = None,
    metadata_db_path: str | Path | None = None,
) -> dict:
    """Check that proposed exact terms occur in indexed MEGA TEXT records."""
    record = get_memory(memory_id, db_path=db_path)
    if not record:
        raise KeyError(f"planning memory not found: {memory_id}")
    terms, volumes = _collect_terms_and_volumes(record["payload"])
    if not terms:
        validation = {
            "passed": False,
            "reason": "no_exact_terms_to_validate",
            "terms_checked": [],
        }
        return mark_corpus_validation(
            memory_id, validation, passed=False, db_path=db_path
        )
    from term_probe import DEFAULT_DB as DEFAULT_METADATA_DB
    from term_probe import probe_terms

    probe = probe_terms(
        terms,
        target_volumes=volumes,
        route="main_text",
        sample_limit=0,
        db_path=metadata_db_path or DEFAULT_METADATA_DB,
    )
    text_hits = int(probe.get("summary", {}).get("unique_text_page_hits", 0))
    validation = {
        "passed": text_hits > 0,
        "reason": "text_hits_found" if text_hits else "no_text_hits",
        "terms_checked": terms,
        "target_volumes": volumes,
        "probe_summary": probe.get("summary", {}),
    }
    return mark_corpus_validation(
        memory_id, validation, passed=text_hits > 0, db_path=db_path
    )
