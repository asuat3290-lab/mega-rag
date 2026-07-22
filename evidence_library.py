#!/usr/bin/env python3
"""Persistent, reviewable evidence library for MEGA research packages.

The library is deliberately separate from metadata.db. Retrieval provenance is
insert-only; human review fields remain mutable and are recorded in a history
table. No model or network call is made by this module.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from evidence_identity import stable_evidence_key

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
LIBRARY_SCHEMA_VERSION = "2"
EXPORT_SCHEMA_VERSION = "mega-evidence-library-export-v1"
PACKAGE_SCHEMA_VERSION = "mega-research-package-v1"
REVIEW_STATUSES = ("unreviewed", "accepted", "rejected", "needs_verification")


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def default_library_path() -> Path:
    configured = _load_config().get("paths", {}).get("research_library_db")
    return Path(configured) if configured else SCRIPT_DIR / "research_library.db"


def default_export_dir() -> Path:
    configured = _load_config().get("paths", {}).get("research_library_exports")
    return Path(configured) if configured else SCRIPT_DIR / "research_library_exports"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    database = Path(path) if path else default_library_path()
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(database), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def initialize_library(path: str | Path | None = None) -> Path:
    database = Path(path) if path else default_library_path()
    conn = _connect(database)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS packages (
                package_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                question TEXT NOT NULL,
                query_hash TEXT,
                generated_at TEXT,
                index_version TEXT,
                source_path TEXT,
                query_json TEXT NOT NULL,
                retrieval_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                last_imported_at TEXT NOT NULL,
                import_count INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS evidence_items (
                library_id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_key TEXT NOT NULL UNIQUE,
                quote_sha256 TEXT NOT NULL,
                content_hash TEXT,
                record_type TEXT,
                record_id TEXT,
                page_id TEXT,
                passage_id TEXT,
                source_display_label TEXT,
                source_title TEXT,
                source_collection TEXT,
                source_quality TEXT,
                source_file TEXT,
                source_doc TEXT,
                source_part TEXT,
                source_url TEXT,
                local_path TEXT,
                abteilung TEXT,
                band TEXT,
                text_type TEXT,
                physical_or_source_page TEXT,
                printed_page_label TEXT,
                passage_no INTEGER,
                char_start INTEGER,
                char_end INTEGER,
                locator_kind TEXT,
                locator_verified INTEGER NOT NULL DEFAULT 0,
                citation_stub TEXT,
                citation_note TEXT,
                text_layer TEXT,
                text_layer_label TEXT,
                text_layer_confidence REAL,
                text_layer_provenance TEXT,
                verified_author_text INTEGER NOT NULL DEFAULT 0,
                reliability_class TEXT,
                ocr_quality TEXT,
                german_context TEXT NOT NULL,
                preview TEXT,
                matched_term TEXT,
                matched_priority_terms_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                first_package_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(first_package_id) REFERENCES packages(package_id)
            );

            CREATE TABLE IF NOT EXISTS package_evidence (
                package_id TEXT NOT NULL,
                evidence_key TEXT NOT NULL,
                package_evidence_id TEXT,
                rank INTEGER,
                retrieval_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(package_id, evidence_key),
                FOREIGN KEY(package_id) REFERENCES packages(package_id) ON DELETE CASCADE,
                FOREIGN KEY(evidence_key) REFERENCES evidence_items(evidence_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reviews (
                evidence_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'unreviewed'
                    CHECK(status IN ('unreviewed','accepted','rejected','needs_verification')),
                claim_supported TEXT,
                claim_not_supported TEXT,
                literal_translation TEXT,
                research_notes TEXT,
                thesis_section TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                verified_print_page TEXT,
                citation_override TEXT,
                locator_verified_by_user INTEGER NOT NULL DEFAULT 0,
                verified_by TEXT,
                verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(evidence_key) REFERENCES evidence_items(evidence_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_key TEXT NOT NULL,
                revision INTEGER NOT NULL,
                action TEXT NOT NULL,
                changed_fields_json TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY(evidence_key) REFERENCES evidence_items(evidence_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS provenance_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_key TEXT NOT NULL,
                package_id TEXT NOT NULL,
                changed_fields_json TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY(evidence_key) REFERENCES evidence_items(evidence_key) ON DELETE CASCADE,
                FOREIGN KEY(package_id) REFERENCES packages(package_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_evidence_source
                ON evidence_items(abteilung, band, text_type);
            CREATE INDEX IF NOT EXISTS idx_evidence_quote
                ON evidence_items(quote_sha256);
            CREATE INDEX IF NOT EXISTS idx_reviews_status
                ON reviews(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_reviews_section
                ON reviews(thesis_section);
            CREATE INDEX IF NOT EXISTS idx_package_evidence_key
                ON package_evidence(evidence_key);
            """
        )
        conn.execute(
            "INSERT INTO library_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (LIBRARY_SCHEMA_VERSION,),
        )
        conn.commit()
    finally:
        conn.close()
    return database.resolve()


def _evidence_key(item: dict) -> str:
    return stable_evidence_key(item)


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    tags = []
    seen = set()
    for part in parts:
        tag = str(part or "").strip()
        normalized = tag.casefold()
        if tag and normalized not in seen:
            seen.add(normalized)
            tags.append(tag)
    return tags


def _package_from_path(path: str | Path) -> dict:
    package_path = Path(path)
    data = json.loads(package_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported research package schema: {data.get('schema_version')!r}"
        )
    data["_source_path"] = str(package_path.resolve())
    return data


def import_research_package(
    package_or_path: dict | str | Path,
    library_path: str | Path | None = None,
) -> dict:
    """Import a research package without overwriting prior human review."""
    initialize_library(library_path)
    package = (
        dict(package_or_path)
        if isinstance(package_or_path, dict)
        else _package_from_path(package_or_path)
    )
    if package.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported research package schema: {package.get('schema_version')!r}"
        )
    package_id = str(package.get("package_id") or "").strip()
    if not package_id:
        raise ValueError("research package has no package_id")
    question = str(package.get("question") or "").strip()
    evidence_items = package.get("evidence")
    if not isinstance(evidence_items, list):
        raise ValueError("research package evidence must be a list")

    now = _now_iso()
    stats = {
        "package_id": package_id,
        "evidence_total": len(evidence_items),
        "evidence_inserted": 0,
        "evidence_reused": 0,
        "associations_added": 0,
        "reviews_created": 0,
        "provenance_enriched": 0,
    }
    conn = _connect(library_path)
    try:
        with conn:
            existing_package = conn.execute(
                "SELECT 1 FROM packages WHERE package_id=?", (package_id,)
            ).fetchone()
            if existing_package:
                conn.execute(
                    "UPDATE packages SET last_imported_at=?, import_count=import_count+1, "
                    "source_path=COALESCE(?, source_path) WHERE package_id=?",
                    (now, package.get("_source_path"), package_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO packages(
                        package_id, schema_version, question, query_hash,
                        generated_at, index_version, source_path, query_json,
                        retrieval_json, summary_json, imported_at,
                        last_imported_at, import_count
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        package_id,
                        package["schema_version"],
                        question,
                        package.get("query_hash"),
                        package.get("generated_at"),
                        package.get("index_version"),
                        package.get("_source_path"),
                        _json(package.get("query") or {}),
                        _json(package.get("retrieval") or {}),
                        _json(package.get("summary") or {}),
                        now,
                        now,
                    ),
                )

            for item in evidence_items:
                if not isinstance(item, dict):
                    continue
                evidence = item.get("evidence") or {}
                context = str(evidence.get("german_context") or "").strip()
                if not context:
                    continue
                record = item.get("record") or {}
                source = item.get("source") or {}
                locator = item.get("locator") or {}
                provenance = item.get("provenance") or {}
                evidence_key = _evidence_key(item)
                quote_hash = evidence.get("quote_sha256") or hashlib.sha256(
                    context.encode("utf-8")
                ).hexdigest()
                values = {
                    "evidence_key": evidence_key,
                    "quote_sha256": quote_hash,
                    "content_hash": record.get("content_hash"),
                    "record_type": record.get("record_type"),
                    "record_id": record.get("record_id"),
                    "page_id": record.get("page_id"),
                    "passage_id": record.get("passage_id"),
                    "source_display_label": source.get("display_label"),
                    "source_title": source.get("title"),
                    "source_collection": source.get("collection"),
                    "source_quality": source.get("quality"),
                    "source_file": source.get("source_file"),
                    "source_doc": source.get("source_doc"),
                    "source_part": source.get("source_part"),
                    "source_url": source.get("source_url"),
                    "local_path": source.get("local_path"),
                    "abteilung": locator.get("abteilung"),
                    "band": locator.get("band"),
                    "text_type": locator.get("text_type"),
                    "physical_or_source_page": locator.get("physical_or_source_page"),
                    "printed_page_label": locator.get("printed_page_label"),
                    "passage_no": locator.get("passage_no"),
                    "char_start": locator.get("char_start"),
                    "char_end": locator.get("char_end"),
                    "locator_kind": locator.get("locator_kind"),
                    "locator_verified": int(bool(locator.get("locator_verified"))),
                    "citation_stub": locator.get("citation_stub"),
                    "citation_note": locator.get("citation_note"),
                    "text_layer": provenance.get("text_layer"),
                    "text_layer_label": provenance.get("text_layer_label"),
                    "text_layer_confidence": provenance.get("text_layer_confidence"),
                    "text_layer_provenance": provenance.get("text_layer_provenance"),
                    "verified_author_text": int(
                        bool(provenance.get("verified_author_text"))
                    ),
                    "reliability_class": provenance.get("reliability_class"),
                    "ocr_quality": provenance.get("ocr_quality"),
                    "german_context": context,
                    "preview": evidence.get("preview"),
                    "matched_term": evidence.get("matched_term"),
                    "matched_priority_terms_json": _json(
                        evidence.get("matched_priority_terms") or []
                    ),
                    "warnings_json": _json(item.get("warnings") or []),
                    "first_package_id": package_id,
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
                columns = ", ".join(values)
                placeholders = ", ".join("?" for _ in values)
                inserted = conn.execute(
                    f"INSERT OR IGNORE INTO evidence_items({columns}) "
                    f"VALUES({placeholders})",
                    tuple(values.values()),
                ).rowcount
                if inserted:
                    stats["evidence_inserted"] += 1
                else:
                    stats["evidence_reused"] += 1
                    old = dict(
                        conn.execute(
                            """
                            SELECT content_hash, source_title, source_doc, source_part,
                                   source_url, printed_page_label, locator_kind,
                                   locator_verified, citation_stub, citation_note
                            FROM evidence_items WHERE evidence_key=?
                            """,
                            (evidence_key,),
                        ).fetchone()
                    )
                    enrich = {}
                    fill_if_missing = {
                        "content_hash": values["content_hash"],
                        "source_title": values["source_title"],
                        "source_doc": values["source_doc"],
                        "source_part": values["source_part"],
                        "source_url": values["source_url"],
                    }
                    for field, new_value in fill_if_missing.items():
                        if old.get(field) in (None, "") and new_value not in (None, ""):
                            enrich[field] = new_value

                    new_printed_page = values["printed_page_label"]
                    if (
                        old.get("printed_page_label") in (None, "")
                        and new_printed_page not in (None, "")
                    ):
                        enrich.update(
                            {
                                "printed_page_label": new_printed_page,
                                "locator_kind": values["locator_kind"],
                                "locator_verified": values["locator_verified"],
                                "citation_stub": values["citation_stub"],
                                "citation_note": values["citation_note"],
                            }
                        )
                    if enrich:
                        assignments = ", ".join(f"{field}=?" for field in enrich)
                        conn.execute(
                            f"UPDATE evidence_items SET {assignments} WHERE evidence_key=?",
                            (*enrich.values(), evidence_key),
                        )
                        changes = {
                            field: {"old": old.get(field), "new": new_value}
                            for field, new_value in enrich.items()
                        }
                        conn.execute(
                            "INSERT INTO provenance_history("
                            "evidence_key, package_id, changed_fields_json, changed_at"
                            ") VALUES(?,?,?,?)",
                            (evidence_key, package_id, _json(changes), now),
                        )
                        stats["provenance_enriched"] += 1

                package_link = conn.execute(
                    """
                    INSERT OR IGNORE INTO package_evidence(
                        package_id, evidence_key, package_evidence_id, rank,
                        retrieval_json
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        package_id,
                        evidence_key,
                        item.get("evidence_id"),
                        item.get("rank"),
                        _json(item.get("retrieval") or {}),
                    ),
                ).rowcount
                if package_link:
                    stats["associations_added"] += 1
                    conn.execute(
                        "UPDATE evidence_items SET occurrence_count=occurrence_count+1, "
                        "last_seen_at=? WHERE evidence_key=?",
                        (now, evidence_key),
                    )

                review = item.get("review") or {}
                status = review.get("status")
                if status not in REVIEW_STATUSES:
                    status = "unreviewed"
                review_created = conn.execute(
                    """
                    INSERT OR IGNORE INTO reviews(
                        evidence_key, status, claim_supported,
                        literal_translation, research_notes, tags_json,
                        created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        evidence_key,
                        status,
                        review.get("claim_supported"),
                        review.get("literal_translation"),
                        review.get("research_notes"),
                        _json(_normalize_tags(review.get("tags"))),
                        now,
                        now,
                    ),
                ).rowcount
                stats["reviews_created"] += int(bool(review_created))
    finally:
        conn.close()
    return stats


def _row_to_item(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["library_ref"] = f"L{int(item['library_id']):06d}"
    item["matched_priority_terms"] = _parse_json(
        item.pop("matched_priority_terms_json", "[]"), []
    )
    item["warnings"] = _parse_json(item.pop("warnings_json", "[]"), [])
    item["tags"] = _parse_json(item.pop("tags_json", "[]"), [])
    questions = item.pop("questions_joined", "") or ""
    item["questions"] = [part for part in questions.split(" || ") if part]
    item["locator_verified"] = bool(item.get("locator_verified"))
    item["verified_author_text"] = bool(item.get("verified_author_text"))
    item["locator_verified_by_user"] = bool(
        item.get("locator_verified_by_user")
    )
    return item


def list_evidence(
    library_path: str | Path | None = None,
    status: str | None = None,
    search: str | None = None,
    source_collection: str | None = None,
    text_type: str | None = None,
    thesis_section: str | None = None,
    tag: str | None = None,
    locator_verified: bool | None = None,
    evidence_key: str | None = None,
    limit: int = 200,
) -> list[dict]:
    initialize_library(library_path)
    if status and status not in REVIEW_STATUSES:
        raise ValueError(f"invalid review status: {status}")
    conditions = []
    params: list[Any] = []
    if evidence_key:
        conditions.append("e.evidence_key=?")
        params.append(evidence_key)
    if status:
        conditions.append("r.status=?")
        params.append(status)
    if source_collection:
        conditions.append("e.source_collection=?")
        params.append(source_collection)
    if text_type:
        conditions.append("e.text_type=?")
        params.append(text_type)
    if thesis_section:
        conditions.append("COALESCE(r.thesis_section, '') LIKE ?")
        params.append(f"%{thesis_section}%")
    if tag:
        conditions.append("r.tags_json LIKE ?")
        params.append(f"%{tag}%")
    if locator_verified is not None:
        if locator_verified:
            conditions.append(
                "(e.locator_verified=1 OR r.locator_verified_by_user=1)"
            )
        else:
            conditions.append(
                "(e.locator_verified=0 AND r.locator_verified_by_user=0)"
            )
    if search:
        needle = f"%{search.strip()}%"
        conditions.append(
            "(e.german_context LIKE ? OR COALESCE(e.source_title,'') LIKE ? "
            "OR COALESCE(e.citation_stub,'') LIKE ? "
            "OR COALESCE(r.claim_supported,'') LIKE ? "
            "OR COALESCE(r.research_notes,'') LIKE ? OR p.question LIKE ?)"
        )
        params.extend([needle] * 6)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"""
        SELECT e.*,
               r.status AS review_status,
               r.claim_supported, r.claim_not_supported,
               r.literal_translation, r.research_notes, r.thesis_section,
               r.tags_json, r.verified_print_page, r.citation_override,
               r.locator_verified_by_user, r.verified_by, r.verified_at,
               r.created_at AS review_created_at,
               r.updated_at AS review_updated_at, r.revision,
               (
                   SELECT GROUP_CONCAT(question, ' || ') FROM (
                       SELECT DISTINCT p2.question AS question
                       FROM package_evidence pe2
                       JOIN packages p2 ON p2.package_id=pe2.package_id
                       WHERE pe2.evidence_key=e.evidence_key
                       ORDER BY p2.imported_at, p2.package_id
                   )
               ) AS questions_joined
        FROM evidence_items e
        JOIN reviews r ON r.evidence_key=e.evidence_key
        LEFT JOIN package_evidence pe ON pe.evidence_key=e.evidence_key
        LEFT JOIN packages p ON p.package_id=pe.package_id
        {where}
        GROUP BY e.evidence_key
        ORDER BY r.updated_at DESC, e.library_id ASC
        LIMIT ?
    """
    params.append(max(1, min(int(limit), 10000)))
    conn = _connect(library_path)
    try:
        return [_row_to_item(row) for row in conn.execute(query, params)]
    finally:
        conn.close()


def _resolve_key(conn: sqlite3.Connection, identifier: str | int) -> str:
    text = str(identifier).strip()
    if text.upper().startswith("L") and text[1:].isdigit():
        row = conn.execute(
            "SELECT evidence_key FROM evidence_items WHERE library_id=?",
            (int(text[1:]),),
        ).fetchone()
    elif text.isdigit():
        row = conn.execute(
            "SELECT evidence_key FROM evidence_items WHERE library_id=?", (int(text),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT evidence_key FROM evidence_items WHERE evidence_key=?", (text,)
        ).fetchone()
        if not row and len(text) >= 8:
            matches = conn.execute(
                "SELECT evidence_key FROM evidence_items WHERE evidence_key LIKE ? LIMIT 2",
                (text + "%",),
            ).fetchall()
            row = matches[0] if len(matches) == 1 else None
    if not row:
        raise KeyError(f"evidence not found: {identifier}")
    return str(row[0])


def get_evidence(
    identifier: str | int, library_path: str | Path | None = None
) -> dict:
    initialize_library(library_path)
    conn = _connect(library_path)
    try:
        key = _resolve_key(conn, identifier)
    finally:
        conn.close()
    items = list_evidence(
        library_path=library_path, evidence_key=key, limit=1
    )
    return items[0]


def update_review(
    identifier: str | int,
    changes: dict,
    library_path: str | Path | None = None,
) -> dict:
    """Update mutable review fields and append an audit-history record."""
    allowed = {
        "status",
        "claim_supported",
        "claim_not_supported",
        "literal_translation",
        "research_notes",
        "thesis_section",
        "tags",
        "verified_print_page",
        "citation_override",
        "locator_verified_by_user",
        "verified_by",
    }
    unexpected = set(changes) - allowed
    if unexpected:
        raise ValueError(f"unsupported review fields: {sorted(unexpected)}")
    if "status" in changes and changes["status"] not in REVIEW_STATUSES:
        raise ValueError(f"invalid review status: {changes['status']}")
    initialize_library(library_path)
    conn = _connect(library_path)
    try:
        with conn:
            key = _resolve_key(conn, identifier)
            old = dict(
                conn.execute("SELECT * FROM reviews WHERE evidence_key=?", (key,)).fetchone()
            )
            normalized = dict(changes)
            if "tags" in normalized:
                normalized["tags_json"] = _json(_normalize_tags(normalized.pop("tags")))
            if "locator_verified_by_user" in normalized:
                normalized["locator_verified_by_user"] = int(
                    bool(normalized["locator_verified_by_user"])
                )
                normalized["verified_at"] = (
                    _now_iso() if normalized["locator_verified_by_user"] else None
                )
            changed = {}
            for field, value in normalized.items():
                if old.get(field) != value:
                    changed[field] = {"old": old.get(field), "new": value}
            if changed:
                revision = int(old.get("revision") or 0) + 1
                assignments = [f"{field}=?" for field in normalized]
                values = list(normalized.values())
                assignments.extend(["updated_at=?", "revision=?"])
                values.extend([_now_iso(), revision, key])
                conn.execute(
                    f"UPDATE reviews SET {', '.join(assignments)} WHERE evidence_key=?",
                    values,
                )
                conn.execute(
                    """
                    INSERT INTO review_history(
                        evidence_key, revision, action, changed_fields_json,
                        changed_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (key, revision, "update", _json(changed), _now_iso()),
                )
    finally:
        conn.close()
    return get_evidence(identifier, library_path)


def library_status(library_path: str | Path | None = None) -> dict:
    database = initialize_library(library_path)
    conn = _connect(database)
    try:
        status_counts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM reviews GROUP BY status"
            )
        }
        return {
            "database": str(database),
            "schema_version": conn.execute(
                "SELECT value FROM library_meta WHERE key='schema_version'"
            ).fetchone()[0],
            "packages": conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0],
            "evidence": conn.execute(
                "SELECT COUNT(*) FROM evidence_items"
            ).fetchone()[0],
            "package_links": conn.execute(
                "SELECT COUNT(*) FROM package_evidence"
            ).fetchone()[0],
            "review_history": conn.execute(
                "SELECT COUNT(*) FROM review_history"
            ).fetchone()[0],
            "provenance_history": conn.execute(
                "SELECT COUNT(*) FROM provenance_history"
            ).fetchone()[0],
            "status": {
                review_status: status_counts.get(review_status, 0)
                for review_status in REVIEW_STATUSES
            },
            "citation_ready": conn.execute(
                """
                SELECT COUNT(*) FROM evidence_items e JOIN reviews r USING(evidence_key)
                WHERE e.locator_verified=1 OR r.locator_verified_by_user=1
                """
            ).fetchone()[0],
        }
    finally:
        conn.close()


def _resolved_citation(item: dict) -> tuple[str, bool]:
    if item.get("citation_override"):
        return str(item["citation_override"]), bool(
            item.get("locator_verified_by_user")
        )
    if item.get("verified_print_page"):
        citation = (
            f"MEGA² {item.get('abteilung')}/{item.get('band')}, "
            f"{item.get('text_type')}, S. {item['verified_print_page']}"
        )
        return citation, bool(item.get("locator_verified_by_user"))
    return str(item.get("citation_stub") or "Unresolved source"), bool(
        item.get("locator_verified")
    )


def build_library_export(
    items: list[dict], filters: dict | None = None
) -> dict:
    evidence = []
    for item in items:
        citation, citation_ready = _resolved_citation(item)
        evidence.append(
            {
                "library_ref": item["library_ref"],
                "evidence_key": item["evidence_key"],
                "review": {
                    "status": item["review_status"],
                    "claim_supported": item.get("claim_supported"),
                    "claim_not_supported": item.get("claim_not_supported"),
                    "literal_translation": item.get("literal_translation"),
                    "research_notes": item.get("research_notes"),
                    "thesis_section": item.get("thesis_section"),
                    "tags": item.get("tags") or [],
                    "revision": item.get("revision"),
                    "verified_by": item.get("verified_by"),
                    "verified_at": item.get("verified_at"),
                },
                "source": {
                    "citation": citation,
                    "citation_ready": citation_ready,
                    "original_citation_stub": item.get("citation_stub"),
                    "title": item.get("source_title"),
                    "source_url": item.get("source_url"),
                    "collection": item.get("source_collection"),
                    "text_layer": item.get("text_layer"),
                    "reliability_class": item.get("reliability_class"),
                },
                "german_context": item.get("german_context"),
                "matched_terms": item.get("matched_priority_terms") or [],
                "warnings": item.get("warnings") or [],
                "questions": item.get("questions") or [],
            }
        )
    total_chars = sum(len(entry.get("german_context") or "") for entry in evidence)
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "filters": filters or {},
        "summary": {
            "evidence_count": len(evidence),
            "citation_ready_count": sum(
                entry["source"]["citation_ready"] for entry in evidence
            ),
            "rough_evidence_tokens": max(0, round(total_chars / 4.2)),
        },
        "usage_constraints": [
            "Use only the supplied German context as primary evidence.",
            "Retain every L###### reference when drafting or revising claims.",
            "Do not use citation_ready=false as a final scholarly citation.",
            "Do not attribute editorial or unclassified material to Marx or Engels.",
        ],
        "evidence": evidence,
    }


def render_library_markdown(export: dict) -> str:
    summary = export["summary"]
    lines = [
        "# MEGA² Reviewed Evidence Library Export",
        "",
        f"- Generated: `{export['generated_at']}`",
        f"- Evidence items: {summary['evidence_count']}",
        f"- Citation-ready items: {summary['citation_ready_count']}",
        f"- Approximate evidence tokens: {summary['rough_evidence_tokens']}",
        "",
        "## Use boundary",
        "",
        "Every substantive claim should retain its `L######` reference. "
        "A source marked `citation ready: no` is a retrieval lead and must be "
        "checked before it becomes a formal footnote.",
    ]
    current_section = None
    for item in export["evidence"]:
        section = item["review"].get("thesis_section") or "Unassigned"
        if section != current_section:
            lines.extend(["", f"## {section}"])
            current_section = section
        review = item["review"]
        source = item["source"]
        context = str(item.get("german_context") or "").replace("```", "`` `")
        lines.extend(
            [
                "",
                f"### {item['library_ref']} - {source['citation']}",
                "",
                f"- Status: `{review['status']}`",
                f"- Citation ready: `{'yes' if source['citation_ready'] else 'no'}`",
                f"- Layer: `{source.get('text_layer') or 'unclassified'}`",
                f"- Reliability: `{source.get('reliability_class') or 'unknown'}`",
                f"- Tags: {', '.join(review.get('tags') or []) or '-'}",
                f"- Claim supported: {review.get('claim_supported') or '_not recorded_'}",
                f"- Claim not supported: {review.get('claim_not_supported') or '_not recorded_'}",
                f"- Literal translation: {review.get('literal_translation') or '_not recorded_'}",
                f"- Research notes: {review.get('research_notes') or '_not recorded_'}",
                "",
                "```text",
                context,
                "```",
            ]
        )
        if source.get("source_url"):
            lines.append(f"- Source URL: {source['source_url']}")
        if item.get("warnings"):
            lines.append(f"- Warnings: {'; '.join(item['warnings'])}")
    lines.extend(
        [
            "",
            "## Instructions for Codex",
            "",
            "1. Cite the library reference after each evidence-dependent claim.",
            "2. Preserve the German wording for conceptual or philological claims.",
            "3. Treat `citation ready: no` as a verification task, not a footnote.",
            "4. Report contradictions and missing evidence instead of filling gaps.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def export_library(
    library_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    output_format: str = "both",
    status: str | None = "accepted",
    search: str | None = None,
    thesis_section: str | None = None,
    tag: str | None = None,
) -> dict:
    selected_status = None if status in (None, "all") else status
    items = list_evidence(
        library_path=library_path,
        status=selected_status,
        search=search,
        thesis_section=thesis_section,
        tag=tag,
        limit=10000,
    )
    filters = {
        "status": status or "all",
        "search": search,
        "thesis_section": thesis_section,
        "tag": tag,
    }
    export = build_library_export(items, filters)
    destination = Path(output_dir) if output_dir else default_export_dir()
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = hashlib.sha256(_json(filters).encode("utf-8")).hexdigest()[:8]
    base = destination / f"evidence_library_{timestamp}_{suffix}"
    paths: dict[str, str | None] = {"json": None, "markdown": None}
    if output_format in {"both", "json"}:
        json_path = base.with_suffix(".json")
        _atomic_write(json_path, json.dumps(export, ensure_ascii=False, indent=2))
        paths["json"] = str(json_path.resolve())
    if output_format in {"both", "markdown"}:
        markdown_path = base.with_suffix(".md")
        _atomic_write(markdown_path, render_library_markdown(export))
        paths["markdown"] = str(markdown_path.resolve())
    return {"export": export, "paths": paths}


def _iter_package_paths(inputs: Iterable[str]) -> Iterable[Path]:
    seen = set()
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            candidates = sorted(path.glob("*.json"))
        elif any(character in value for character in "*?["):
            candidates = [Path(match) for match in sorted(glob.glob(value))]
        else:
            candidates = [path]
        for candidate in candidates:
            resolved = str(candidate.resolve())
            if candidate.is_file() and resolved not in seen:
                seen.add(resolved)
                yield candidate


def _print_list(items: list[dict]) -> None:
    for item in items:
        citation, ready = _resolved_citation(item)
        question = item.get("questions", [""])[0] if item.get("questions") else ""
        print(
            f"{item['library_ref']}  {item['review_status']:<18} "
            f"{'ready' if ready else 'verify':<6}  {citation}  {question[:50]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Override research library database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the evidence library")

    import_parser = subparsers.add_parser("import", help="Import package JSON files")
    import_parser.add_argument("paths", nargs="+")

    status_parser = subparsers.add_parser("status", help="Show library status")
    status_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="List evidence")
    list_parser.add_argument("--status", choices=REVIEW_STATUSES)
    list_parser.add_argument("--search")
    list_parser.add_argument("--section")
    list_parser.add_argument("--tag")
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.add_argument("--json", action="store_true")

    review_parser = subparsers.add_parser("review", help="Update human review")
    review_parser.add_argument("identifier", help="L######, numeric ID, or evidence key")
    review_parser.add_argument("--status", choices=REVIEW_STATUSES)
    review_parser.add_argument("--claim-supported")
    review_parser.add_argument("--claim-not-supported")
    review_parser.add_argument("--translation")
    review_parser.add_argument("--notes")
    review_parser.add_argument("--section")
    review_parser.add_argument("--tags")
    review_parser.add_argument("--print-page")
    review_parser.add_argument("--citation")
    review_parser.add_argument(
        "--locator-verified", action=argparse.BooleanOptionalAction, default=None
    )
    review_parser.add_argument("--verified-by")

    export_parser = subparsers.add_parser("export", help="Export reviewed evidence")
    export_parser.add_argument(
        "--status", choices=(*REVIEW_STATUSES, "all"), default="accepted"
    )
    export_parser.add_argument("--search")
    export_parser.add_argument("--section")
    export_parser.add_argument("--tag")
    export_parser.add_argument("--output-dir")
    export_parser.add_argument(
        "--format", choices=("both", "json", "markdown"), default="both"
    )

    args = parser.parse_args()
    database = args.db
    try:
        if args.command == "init":
            print(initialize_library(database))
        elif args.command == "import":
            package_paths = list(_iter_package_paths(args.paths))
            if not package_paths:
                raise ValueError("no research package JSON files found")
            failures = 0
            for path in package_paths:
                try:
                    stats = import_research_package(path, database)
                    print(f"{path}: {_json(stats)}")
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    failures += 1
                    print(f"SKIP {path}: {exc}", file=sys.stderr)
            return 1 if failures == len(package_paths) else 0
        elif args.command == "status":
            current = library_status(database)
            print(json.dumps(current, ensure_ascii=False, indent=2) if args.json else _json(current))
        elif args.command == "list":
            items = list_evidence(
                database,
                status=args.status,
                search=args.search,
                thesis_section=args.section,
                tag=args.tag,
                limit=args.limit,
            )
            print(json.dumps(items, ensure_ascii=False, indent=2) if args.json else "", end="")
            if not args.json:
                _print_list(items)
        elif args.command == "review":
            changes = {
                key: value
                for key, value in {
                    "status": args.status,
                    "claim_supported": args.claim_supported,
                    "claim_not_supported": args.claim_not_supported,
                    "literal_translation": args.translation,
                    "research_notes": args.notes,
                    "thesis_section": args.section,
                    "tags": args.tags,
                    "verified_print_page": args.print_page,
                    "citation_override": args.citation,
                    "locator_verified_by_user": args.locator_verified,
                    "verified_by": args.verified_by,
                }.items()
                if value is not None
            }
            if not changes:
                raise ValueError("no review changes supplied")
            item = update_review(args.identifier, changes, database)
            print(_json({"library_ref": item["library_ref"], "revision": item["revision"]}))
        elif args.command == "export":
            result = export_library(
                database,
                args.output_dir,
                args.format,
                args.status,
                args.search,
                args.section,
                args.tag,
            )
            print(_json({"summary": result["export"]["summary"], "paths": result["paths"]}))
    except (OSError, sqlite3.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
