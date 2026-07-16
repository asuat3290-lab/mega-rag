#!/usr/bin/env python3
"""Stable versioning for the actual MEGA RAG index state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_MARKERS = (
    "fts_v3",
    "lancedb_v3",
    "passage_v2",
    "progress_v2",
    "source_provenance_v1",
    "text_layer_v1",
)


def _load_config(config: dict | None = None) -> dict:
    if config is not None:
        return config
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1", (table,)
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _chunk_fingerprint(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "chunks"):
        return {"count": 0, "digest": "missing", "sources": []}

    columns = _column_names(conn, "chunks")
    source_expr = (
        "COALESCE(source_collection, 'ocr')"
        if "source_collection" in columns
        else "'ocr'"
    )
    sources = [
        list(row)
        for row in conn.execute(
            f"""
            SELECT {source_expr}, COUNT(*), COALESCE(SUM(char_count), 0),
                   SUM(CASE WHEN COALESCE(content_hash, '') = '' THEN 1 ELSE 0 END)
            FROM chunks
            GROUP BY {source_expr}
            ORDER BY {source_expr}
            """
        )
    ]

    layer_expr = "COALESCE(text_layer, '')" if "text_layer" in columns else "''"
    confidence_expr = (
        "COALESCE(text_layer_confidence, 0)"
        if "text_layer_confidence" in columns else "0"
    )
    provenance_expr = (
        "COALESCE(text_layer_provenance, '')"
        if "text_layer_provenance" in columns else "''"
    )
    layer_version_expr = (
        "COALESCE(text_layer_version, '')"
        if "text_layer_version" in columns else "''"
    )

    digest = hashlib.sha256()
    missing_hashes = 0
    query = f"""
        SELECT id, COALESCE(content_hash, ''), COALESCE(char_count, 0),
               CASE WHEN COALESCE(content_hash, '') = '' THEN chunk_text ELSE NULL END,
               {layer_expr}, {confidence_expr}, {provenance_expr}, {layer_version_expr}
        FROM chunks
        ORDER BY id
    """
    for (
        record_id, content_hash, char_count, fallback_text,
        text_layer, layer_confidence, layer_provenance, layer_version,
    ) in conn.execute(query):
        if not content_hash:
            missing_hashes += 1
            content_hash = hashlib.sha256((fallback_text or "").encode("utf-8")).hexdigest()
        digest.update(str(record_id).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(char_count).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(text_layer).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(layer_confidence).encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(layer_provenance).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(layer_version).encode("utf-8"))
        digest.update(b"\n")

    return {
        "count": sum(row[1] for row in sources),
        "digest": digest.hexdigest(),
        "missing_hashes": missing_hashes,
        "sources": sources,
    }


def _passage_fingerprint(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "passages"):
        return {"count": 0, "digest": "missing"}
    count = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    if not count:
        return {"count": 0, "digest": hashlib.sha256(b"").hexdigest()}

    digest = hashlib.sha256()
    for passage_id, page_id, char_start, char_end, text in conn.execute(
        """
        SELECT passage_id, page_id, COALESCE(char_start, -1),
               COALESCE(char_end, -1), text
        FROM passages
        ORDER BY passage_id
        """
    ):
        text_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
        digest.update(
            f"{passage_id}\0{page_id}\0{char_start}\0{char_end}\0{text_hash}\n".encode(
                "utf-8"
            )
        )
    return {"count": count, "digest": digest.hexdigest()}


def _sqlite_state(config: dict) -> dict[str, Any]:
    db_path = Path(config["paths"]["metadata_db"])
    if not db_path.exists():
        return {"available": False}

    conn = _connect_read_only(db_path)
    try:
        state: dict[str, Any] = {
            "available": True,
            "chunks": _chunk_fingerprint(conn),
            "passages": _passage_fingerprint(conn),
        }
        state["fts_count"] = (
            conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            if _table_exists(conn, "chunks_fts")
            else 0
        )
        if _table_exists(conn, "index_progress"):
            progress_columns = _column_names(conn, "index_progress")
            status_expr = (
                "COALESCE(status, 'unknown')" if "status" in progress_columns else "'legacy'"
            )
            embedding_expr = (
                "COALESCE(embedding_status, 'unknown')"
                if "embedding_status" in progress_columns
                else "'legacy'"
            )
            state["progress"] = [
                list(row)
                for row in conn.execute(
                    f"""
                    SELECT {status_expr}, {embedding_expr}, COUNT(*)
                    FROM index_progress
                    GROUP BY {status_expr}, {embedding_expr}
                    ORDER BY {status_expr}, {embedding_expr}
                    """
                )
            ]
        else:
            state["progress"] = []
        if _table_exists(conn, "index_state"):
            state["index_state"] = [
                list(row)
                for row in conn.execute(
                    "SELECT key, value FROM index_state ORDER BY key"
                )
                if row[0] in {"chunks_fts_dirty", "vector_revision"}
            ]
        else:
            state["index_state"] = []
        return state
    finally:
        conn.close()


def _vector_state(config: dict) -> dict[str, Any]:
    vector_path = Path(config["paths"]["vector_db"])
    if not vector_path.exists():
        return {"available": False, "rows": 0}
    try:
        import lancedb

        table = lancedb.connect(str(vector_path)).open_table("chunks")
        return {"available": True, "rows": int(table.count_rows())}
    except Exception as exc:
        return {"available": False, "rows": 0, "error": type(exc).__name__}


def compute_index_version(config: dict | None = None) -> str:
    """Compute a stable hash from the state that is actually searchable."""
    cfg = _load_config(config)
    glossary_path = BASE_DIR / "glossary.yaml"
    payload = {
        "schema": SCHEMA_MARKERS,
        "chunking": cfg.get("chunking", {}),
        "embedding_model": cfg.get("ollama", {}).get("embedding_model", ""),
        "embedding_dim": cfg.get("ollama", {}).get("embedding_dim", ""),
        "glossary_hash": (
            hashlib.sha256(glossary_path.read_bytes()).hexdigest()
            if glossary_path.exists()
            else "missing"
        ),
        "sqlite": _sqlite_state(cfg),
        "vectors": _vector_state(cfg),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def get_current_version(config: dict | None = None) -> str:
    """Read the latest stored version without creating or mutating the database."""
    try:
        cfg = _load_config(config)
        db_path = Path(cfg["paths"]["metadata_db"])
        if not db_path.exists():
            return ""
        conn = _connect_read_only(db_path)
        try:
            if not _table_exists(conn, "index_versions"):
                return ""
            row = conn.execute(
                "SELECT version_hash FROM index_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def store_version(description: str = "", config: dict | None = None) -> str:
    """Store a new version only when the actual searchable state changed."""
    cfg = _load_config(config)
    version = compute_index_version(cfg)
    db_path = Path(cfg["paths"]["metadata_db"])
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_versions (
                id INTEGER PRIMARY KEY,
                version_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                description TEXT
            )
            """
        )
        row = conn.execute(
            "SELECT version_hash FROM index_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or row[0] != version:
            conn.execute(
                """
                INSERT INTO index_versions
                    (version_hash, created_at, description)
                VALUES (?, ?, ?)
                """,
                (version, datetime.now().isoformat(), description),
            )
            conn.commit()
        return version
    finally:
        conn.close()


def version_matches(config: dict | None = None) -> bool:
    cfg = _load_config(config)
    return get_current_version(cfg) == compute_index_version(cfg)