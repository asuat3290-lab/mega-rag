#!/usr/bin/env python3
"""Build and query a lightweight Sachregister navigation index.

Register pages are deliberately kept outside the evidence index.  They point
to likely locations in a volume, but never count as Marx/Engels author text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
DEFAULT_SOURCE_DB = Path(CONFIG["paths"]["metadata_db"])
DEFAULT_REGISTER_DB = Path(CONFIG.get("paths", {}).get("sachregister_db", BASE_DIR / "sachregister.db"))
REGISTER_SCHEMA_VERSION = "sachregister-pages-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    return sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)


def register_page_confidence(text: str, text_layer: str = "") -> tuple[float, str]:
    sample = str(text or "")[:240]
    stripped = sample.lstrip(" \t\r\n|._-")
    if str(text_layer or "").casefold() == "register":
        return 0.99, "text_layer"
    match = re.search(r"(?im)^\s*sachregister\b", sample)
    if match and match.start() <= 80:
        return 0.95, "header"
    if stripped.casefold().startswith("sachregister"):
        return 0.95, "header"
    return 0.0, "not_register"


def _source_rows(source_db: str | Path) -> list[sqlite3.Row]:
    conn = _ro_connect(source_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, mega_abteilung, band, source_type, page, source_file,
                   COALESCE(page_label, ''), COALESCE(source_collection, 'ocr'),
                   COALESCE(content_hash, ''), COALESCE(text_layer, ''), chunk_text
            FROM chunks
            WHERE source_type = 'APPARAT'
              AND (text_layer = 'register' OR chunk_text LIKE '%Sachregister%')
            ORDER BY mega_abteilung, band, page
            """
        ).fetchall()
    finally:
        conn.close()
    return rows


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE register_pages (
            register_id TEXT PRIMARY KEY,
            source_chunk_id TEXT NOT NULL,
            abteilung TEXT,
            band TEXT,
            pdf_page INTEGER,
            page_label TEXT,
            source_file TEXT,
            source_collection TEXT,
            text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            detection_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_eligible INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL
        );
        CREATE INDEX idx_register_scope ON register_pages(abteilung, band);
        CREATE VIRTUAL TABLE register_pages_fts USING fts5(
            text,
            content='register_pages',
            content_rowid='rowid'
        );
        CREATE TABLE register_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def build_sachregister_index(source_db: str | Path = DEFAULT_SOURCE_DB,
                             output_db: str | Path = DEFAULT_REGISTER_DB) -> dict:
    """Rebuild the small auxiliary index atomically from existing metadata."""
    output = Path(output_db)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    if temp.exists():
        temp.unlink()
    source_rows = _source_rows(source_db)
    accepted = []
    for row in source_rows:
        confidence, method = register_page_confidence(row[10], row[9])
        if confidence <= 0:
            continue
        content_hash = row[8] or hashlib.sha256(row[10].encode("utf-8")).hexdigest()
        accepted.append((
            f"register:{row[0]}", row[0], row[1], row[2], row[4], row[6], row[5],
            row[7], row[10], content_hash, method, confidence, 0, _now(),
        ))

    conn = sqlite3.connect(temp)
    try:
        _init_db(conn)
        conn.executemany(
            """
            INSERT INTO register_pages(
                register_id, source_chunk_id, abteilung, band, pdf_page,
                page_label, source_file, source_collection, text, content_hash,
                detection_method, confidence, evidence_eligible, indexed_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            accepted,
        )
        conn.execute("INSERT INTO register_pages_fts(register_pages_fts) VALUES ('rebuild')")
        version_payload = "\n".join(f"{row[1]}:{row[9]}" for row in accepted)
        version = hashlib.sha256(
            (REGISTER_SCHEMA_VERSION + "\n" + version_payload).encode("utf-8")
        ).hexdigest()[:16]
        conn.executemany(
            "INSERT INTO register_state(key, value) VALUES (?, ?)",
            (("schema_version", REGISTER_SCHEMA_VERSION), ("register_version", version),
             ("built_at", _now()), ("source_db", str(Path(source_db).resolve()))),
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(temp, output)
    volumes = sorted({f"{row[2]}/{row[3]}" for row in accepted})
    return {
        "register_db": str(output),
        "register_version": version,
        "pages": len(accepted),
        "volumes": len(volumes),
        "volume_labels": volumes,
        "evidence_eligible": False,
    }


def register_status(path: str | Path = DEFAULT_REGISTER_DB) -> dict:
    db_path = Path(path)
    if not db_path.exists():
        return {"ready": False, "register_db": str(db_path), "pages": 0,
                "register_version": None}
    conn = _ro_connect(db_path)
    try:
        state = dict(conn.execute("SELECT key, value FROM register_state"))
        pages = int(conn.execute("SELECT COUNT(*) FROM register_pages").fetchone()[0])
        volumes = int(conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT abteilung, band FROM register_pages)"
        ).fetchone()[0])
    finally:
        conn.close()
    return {"ready": bool(pages), "register_db": str(db_path), "pages": pages,
            "volumes": volumes, "register_version": state.get("register_version"),
            "schema_version": state.get("schema_version"), "built_at": state.get("built_at")}


def _fts_query(terms: Iterable[str]) -> str:
    quoted = []
    seen = set()
    for term in terms:
        value = str(term or "").strip()
        key = value.casefold()
        if len(value) > 1 and key not in seen:
            seen.add(key)
            quoted.append('"' + value.replace('"', '""') + '"')
    return " OR ".join(quoted)


def _best_window(text: str, terms: list[str], before: int = 120, after: int = 420) -> tuple[str, str]:
    lowered = text.casefold()
    matches = []
    for priority, term in enumerate(terms):
        pos = lowered.find(str(term).casefold())
        if pos >= 0:
            matches.append((priority, pos, term))
    if not matches:
        return text[: before + after].strip(), ""
    _, pos, term = min(matches)
    start = max(0, pos - before)
    end = min(len(text), pos + len(term) + after)
    return text[start:end].strip(), str(term)


def _reference_pages(snippet: str, matched_term: str) -> list[str]:
    start = snippet.casefold().find(matched_term.casefold()) if matched_term else 0
    tail = snippet[max(0, start): max(0, start) + 340]
    refs = re.findall(r"(?<![\w/])\d{1,4}(?:\s*[\u2013\u2014-]\s*\d{1,4})?(?:\s*\(\d{1,4}\))?", tail)
    return list(dict.fromkeys(value.strip() for value in refs))[:24]


def search_sachregister(terms: list[str], *, top_k: int = 12,
                        target_volumes: list[dict] | None = None,
                        db_path: str | Path = DEFAULT_REGISTER_DB) -> dict:
    """Search register pages and return navigation hints, never evidence."""
    query = _fts_query(terms)
    status = register_status(db_path)
    if not status["ready"] or not query:
        return {"status": status, "query": query, "hits": [], "evidence_eligible": False}
    filters = []
    params: list[object] = [query]
    scoped = [value for value in target_volumes or [] if value.get("abteilung")]
    if scoped:
        clauses = []
        for volume in scoped:
            if volume.get("band"):
                clauses.append("(p.abteilung=? AND p.band=?)")
                params.extend([volume["abteilung"], str(volume["band"])])
            else:
                clauses.append("p.abteilung=?")
                params.append(volume["abteilung"])
        filters.append("(" + " OR ".join(clauses) + ")")
    where = (" AND " + " AND ".join(filters)) if filters else ""
    params.append(max(top_k * 3, 30))
    conn = _ro_connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT p.*, bm25(register_pages_fts) AS bm25_score
            FROM register_pages_fts f
            JOIN register_pages p ON p.rowid=f.rowid
            WHERE register_pages_fts MATCH ? {where}
            ORDER BY bm25_score ASC LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    hits = []
    for row in rows:
        snippet, matched = _best_window(row["text"], terms)
        hits.append({
            "register_id": row["register_id"], "source_chunk_id": row["source_chunk_id"],
            "abteilung": row["abteilung"], "band": row["band"],
            "pdf_page": row["pdf_page"], "page_label": row["page_label"],
            "source_file": row["source_file"], "matched_term": matched,
            "reference_pages": _reference_pages(snippet, matched), "snippet": snippet,
            "bm25_score": row["bm25_score"], "confidence": row["confidence"],
            "evidence_eligible": False,
        })
    hits.sort(key=lambda hit: (terms.index(hit["matched_term"]) if hit["matched_term"] in terms else 999,
                               hit["bm25_score"]))
    return {"status": status, "query": query, "hits": hits[:top_k],
            "evidence_eligible": False}


def _expand_reference_pages(values: Iterable[str], max_range: int = 12) -> list[str]:
    pages = []
    seen = set()
    for raw in values:
        cleaned = re.sub(r"\s*\([^)]*\)\s*", "", str(raw or "")).strip()
        match = re.fullmatch(r"(\d{1,4})(?:\s*[\u2013\u2014-]\s*(\d{1,4}))?", cleaned)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2) or start)
        candidates = [start]
        if end >= start and end - start <= max_range:
            candidates = list(range(start, end + 1))
        for value in candidates:
            page = str(value)
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages


def resolve_register_targets(register_hits: list[dict], *,
                             metadata_db: str | Path = DEFAULT_SOURCE_DB,
                             text_only: bool = True, top_k: int = 24) -> list[dict]:
    """Resolve register references only against explicit printed page labels.

    OCR PDF physical pages have no trustworthy edition-page mapping and are
    intentionally excluded from this resolver.
    """
    scopes: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for hit in register_hits:
        abteilung = str(hit.get("abteilung") or "")
        band = str(hit.get("band") or "")
        if not abteilung or not band:
            continue
        for page in _expand_reference_pages(hit.get("reference_pages", [])):
            scopes.setdefault((abteilung, band), {}).setdefault(page, []).append(hit)
    if not scopes:
        return []

    conn = _ro_connect(metadata_db)
    conn.row_factory = sqlite3.Row
    output = []
    seen = set()
    try:
        for (abteilung, band), page_map in scopes.items():
            labels = list(page_map)
            for offset in range(0, len(labels), 200):
                batch = labels[offset: offset + 200]
                placeholders = ",".join("?" for _ in batch)
                type_filter = " AND source_type='TEXT'" if text_only else ""
                rows = conn.execute(
                    f"""
                    SELECT id, source_path, mega_abteilung, band, source_type,
                           page, is_main_text, chunk_text,
                           COALESCE(source_collection, 'ocr') AS source_collection,
                           COALESCE(source_quality, 'ocr') AS source_quality,
                           COALESCE(page_kind, 'pdf') AS page_kind,
                           COALESCE(page_label, '') AS page_label,
                           COALESCE(source_url, '') AS source_url,
                           COALESCE(text_layer, 'unclassified') AS text_layer,
                           COALESCE(text_layer_confidence, 0) AS text_layer_confidence,
                           COALESCE(text_layer_provenance, '') AS text_layer_provenance,
                           COALESCE(ocr_quality, 'medium') AS ocr_quality
                    FROM chunks
                    WHERE mega_abteilung=? AND band=?
                      AND COALESCE(page_label, '') IN ({placeholders})
                      AND COALESCE(page_label, '')<>'' {type_filter}
                    ORDER BY CASE WHEN source_collection='megadigital' THEN 0 ELSE 1 END,
                             CASE WHEN text_layer='author_text' THEN 0 ELSE 1 END
                    """,
                    [abteilung, band, *batch],
                ).fetchall()
                for row in rows:
                    if row["id"] in seen:
                        continue
                    seen.add(row["id"])
                    source_hits = page_map.get(str(row["page_label"]), [])
                    output.append({
                        "id": row["id"], "record_type": "page",
                        "source_path": row["source_path"], "abteilung": row["mega_abteilung"],
                        "band": row["band"], "type": row["source_type"],
                        "page": row["page"], "is_main_text": bool(row["is_main_text"]),
                        "text": row["chunk_text"], "source_collection": row["source_collection"],
                        "source_quality": row["source_quality"], "page_kind": row["page_kind"],
                        "page_label": row["page_label"], "source_url": row["source_url"],
                        "text_layer": row["text_layer"],
                        "text_layer_confidence": row["text_layer_confidence"],
                        "text_layer_provenance": row["text_layer_provenance"],
                        "ocr_quality": row["ocr_quality"], "rank": 99, "_bm25_rank": 99,
                        "_retrieval_source": "sachregister_resolved",
                        "_retrieval_sources": ["sachregister_resolved"],
                        "_register_references": [
                            {"register_id": hit.get("register_id"),
                             "matched_term": hit.get("matched_term"),
                             "printed_page": row["page_label"]}
                            for hit in source_hits
                        ],
                    })
                    if len(output) >= top_k:
                        return output
    finally:
        conn.close()
    return output
def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB))
    build.add_argument("--output-db", default=str(DEFAULT_REGISTER_DB))
    status = sub.add_parser("status")
    status.add_argument("--db", default=str(DEFAULT_REGISTER_DB))
    search = sub.add_parser("search")
    search.add_argument("terms", nargs="+")
    search.add_argument("--top-k", type=int, default=12)
    search.add_argument("--db", default=str(DEFAULT_REGISTER_DB))
    args = parser.parse_args()
    if args.command == "build":
        payload = build_sachregister_index(args.source_db, args.output_db)
    elif args.command == "status":
        payload = register_status(args.db)
    else:
        payload = search_sachregister(args.terms, top_k=args.top_k, db_path=args.db)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
