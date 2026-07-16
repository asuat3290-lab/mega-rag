#!/usr/bin/env python3
"""Incremental passage-level FTS index for MEGA RAG."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
PASSAGE_SCHEMA_VERSION = "passage-v2"
DEFAULT_TARGET_TOKENS = 260
DEFAULT_OVERLAP_TOKENS = 50
DEFAULT_MIN_TOKENS = 80
DEFAULT_MAX_TOKENS = 420

PASSAGE_COLUMNS = {
    "source_path": "TEXT DEFAULT ''",
    "source_collection": "TEXT DEFAULT 'ocr'",
    "source_quality": "TEXT DEFAULT 'ocr'",
    "text_layer": "TEXT DEFAULT 'unclassified'",
    "text_layer_confidence": "REAL DEFAULT 0",
    "page_kind": "TEXT DEFAULT 'pdf'",
    "page_label": "TEXT DEFAULT ''",
    "source_url": "TEXT DEFAULT ''",
    "page_content_hash": "TEXT DEFAULT ''",
    "chunk_config": "TEXT DEFAULT ''",
    "token_count": "INTEGER DEFAULT 0",
}


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1", (name,)
    ).fetchone() is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO index_state(key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, datetime.now().isoformat()),
    )


def _get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    if not _table_exists(conn, "index_state"):
        return default
    row = conn.execute("SELECT value FROM index_state WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS passages (
            passage_id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            abteilung TEXT,
            band TEXT,
            text_type TEXT,
            page_no INTEGER,
            passage_no INTEGER,
            text TEXT NOT NULL,
            char_start INTEGER,
            char_end INTEGER,
            ocr_quality REAL DEFAULT 0.5,
            source_pdf TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    existing = _column_names(conn, "passages")
    for name, definition in PASSAGE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE passages ADD COLUMN {name} {definition}")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
            text,
            content='passages',
            content_rowid='rowid'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS passage_builds (
            id INTEGER PRIMARY KEY,
            config_hash TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            pages_total INTEGER NOT NULL DEFAULT 0,
            pages_updated INTEGER NOT NULL DEFAULT 0,
            passages_written INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_passages_page ON passages(page_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_passages_scope "
        "ON passages(abteilung, band, text_type, text_layer)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_passages_config "
        "ON passages(chunk_config, page_content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_passages_page_version "
        "ON passages(page_id, page_content_hash, chunk_config)"
    )
    conn.commit()


def passage_config_hash(
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    payload = {
        "schema": PASSAGE_SCHEMA_VERSION,
        "target_tokens": int(target_tokens),
        "overlap_tokens": int(overlap_tokens),
        "min_tokens": int(min_tokens),
        "max_tokens": int(max_tokens),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _validate_config(target: int, overlap: int, minimum: int, maximum: int) -> None:
    if minimum < 20:
        raise ValueError("min_tokens must be at least 20")
    if not minimum <= target <= maximum:
        raise ValueError("expected min_tokens <= target_tokens <= max_tokens")
    if overlap < 0 or overlap >= minimum:
        raise ValueError("overlap_tokens must be >= 0 and smaller than min_tokens")


def _paragraph_boundaries(text: str, token_ends: list[int]) -> list[int]:
    boundaries: list[int] = []
    for match in re.finditer(r"\n\s*\n+", text):
        index = bisect.bisect_right(token_ends, match.end())
        if 0 < index < len(token_ends):
            boundaries.append(index)
    return sorted(set(boundaries))


def split_passages(
    text: str,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict]:
    """Split one page while preserving exact source character offsets."""
    _validate_config(target_tokens, overlap_tokens, min_tokens, max_tokens)
    text = text or ""
    tokens = list(re.finditer(r"\S+", text, flags=re.UNICODE))
    if not tokens:
        return []
    token_ends = [token.end() for token in tokens]
    boundaries = _paragraph_boundaries(text, token_ends)
    total = len(tokens)
    passages: list[dict] = []
    start = 0

    while start < total:
        remaining = total - start
        if remaining <= max_tokens:
            end = total
        else:
            lower = min(total, start + min_tokens)
            desired = min(total, start + target_tokens)
            upper = min(total, start + max_tokens)
            candidates = [point for point in boundaries if lower <= point <= upper]
            end = min(candidates, key=lambda point: (abs(point - desired), point)) if candidates else desired
            if total - end < min_tokens:
                end = total
        if end <= start:
            end = min(total, start + max(1, target_tokens))

        char_start = tokens[start].start()
        char_end = tokens[end - 1].end()
        raw = text[char_start:char_end]
        left_trim = len(raw) - len(raw.lstrip())
        right_trim = len(raw) - len(raw.rstrip())
        char_start += left_trim
        if right_trim:
            char_end -= right_trim
        passage_text = text[char_start:char_end]
        if passage_text:
            passages.append({
                "text": passage_text,
                "char_start": char_start,
                "char_end": char_end,
                "token_count": end - start,
            })
        if end >= total:
            break
        next_start = end - overlap_tokens
        start = next_start if next_start > start else end

    return passages


def _quality_number(value: str) -> float:
    return {
        "high": 0.9,
        "medium": 0.6,
        "low": 0.3,
        "failed": 0.1,
    }.get(str(value or "").lower(), 0.5)


def _eligible_page_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM chunks
        WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
          AND COALESCE(chunk_text, '') <> ''
          AND COALESCE(ocr_quality, 'medium') <> 'failed'
        """
    ).fetchone()[0]


def build_passage_index(
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    rebuild: bool = False,
    max_pages: int | None = None,
    batch_pages: int = 250,
) -> dict:
    _validate_config(target_tokens, overlap_tokens, min_tokens, max_tokens)
    config = load_config()
    db_path = Path(config["paths"]["metadata_db"])
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_schema(conn)
    config_hash = passage_config_hash(
        target_tokens, overlap_tokens, min_tokens, max_tokens
    )
    if not rebuild:
        pending_pages = conn.execute(
            """
            SELECT COUNT(*)
            FROM chunks c
            WHERE COALESCE(c.char_count, LENGTH(c.chunk_text), 0) >= 80
              AND COALESCE(c.chunk_text, '') <> ''
              AND COALESCE(c.ocr_quality, 'medium') <> 'failed'
              AND NOT EXISTS (
                  SELECT 1 FROM passages p
                  WHERE p.page_id=c.id
                    AND p.page_content_hash=COALESCE(c.content_hash, '')
                    AND p.chunk_config=?
              )
            """,
            (config_hash,),
        ).fetchone()[0]
        orphaned = conn.execute(
            """
            SELECT COUNT(*) FROM passages WHERE page_id NOT IN (
                SELECT id FROM chunks
                WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
                  AND COALESCE(chunk_text, '') <> ''
                  AND COALESCE(ocr_quality, 'medium') <> 'failed'
            )
            """
        ).fetchone()[0]
        if (
            pending_pages == 0
            and orphaned == 0
            and _get_state(conn, "passages_fts_dirty", "0") != "1"
        ):
            if _get_state(conn, "passages_complete", "0") != "1":
                _set_state(conn, "passages_complete", "1")
                conn.commit()
            conn.close()
            result = passage_status(config_hash=config_hash)
            result.update({
                "pages_updated": 0,
                "passages_written": 0,
                "stale_removed": 0,
                "no_op": True,
            })
            return result
    started_at = datetime.now().isoformat()
    total_pages = _eligible_page_count(conn)
    build_id = conn.execute(
        """
        INSERT INTO passage_builds(
            config_hash, started_at, pages_total, status
        ) VALUES (?, ?, ?, 'running')
        """,
        (config_hash, started_at, total_pages),
    ).lastrowid
    _set_state(conn, "passages_fts_dirty", "1")
    _set_state(conn, "passages_complete", "0")
    conn.commit()

    pages_updated = 0
    passages_written = 0
    stale_removed = 0
    try:
        if rebuild:
            conn.execute("DELETE FROM passages")
            conn.commit()
        stale_removed = conn.execute(
            """
            DELETE FROM passages WHERE page_id NOT IN (
                SELECT id FROM chunks
                WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
                  AND COALESCE(chunk_text, '') <> ''
                  AND COALESCE(ocr_quality, 'medium') <> 'failed'
            )
            """
        ).rowcount
        conn.commit()

        limit_sql = " LIMIT ?" if max_pages else ""
        params: list[object] = [config_hash]
        if max_pages:
            params.append(int(max_pages))
        cursor = conn.execute(
            f"""
            SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
                   c.page, c.chunk_text, c.source_file, c.ocr_quality,
                   COALESCE(c.source_collection, 'ocr'),
                   COALESCE(c.source_quality, 'ocr'),
                   COALESCE(c.text_layer, 'unclassified'),
                   COALESCE(c.text_layer_confidence, 0),
                   COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''),
                   COALESCE(c.source_url, ''), COALESCE(c.content_hash, '')
            FROM chunks c
            WHERE COALESCE(c.char_count, LENGTH(c.chunk_text), 0) >= 80
              AND COALESCE(c.chunk_text, '') <> ''
              AND COALESCE(c.ocr_quality, 'medium') <> 'failed'
              AND NOT EXISTS (
                  SELECT 1 FROM passages p
                  WHERE p.page_id=c.id
                    AND p.page_content_hash=COALESCE(c.content_hash, '')
                    AND p.chunk_config=?
              )
            ORDER BY c.id{limit_sql}
            """,
            params,
        )
        now = datetime.now().isoformat()
        while True:
            rows = cursor.fetchmany(batch_pages)
            if not rows:
                break
            for row in rows:
                (
                    page_id, source_path, abteilung, band, text_type, page_no,
                    text, source_file, ocr_quality, source_collection,
                    source_quality, text_layer, layer_confidence, page_kind,
                    page_label, source_url, content_hash,
                ) = row
                if not content_hash:
                    content_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
                parts = split_passages(
                    text,
                    target_tokens=target_tokens,
                    overlap_tokens=overlap_tokens,
                    min_tokens=min_tokens,
                    max_tokens=max_tokens,
                )
                conn.execute("DELETE FROM passages WHERE page_id=?", (page_id,))
                values = []
                for passage_no, part in enumerate(parts):
                    identity = (
                        f"{page_id}\0{content_hash}\0{config_hash}\0{passage_no}\0"
                        f"{part['char_start']}\0{part['char_end']}"
                    )
                    passage_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
                    values.append((
                        passage_id, page_id, abteilung, band, text_type, page_no,
                        passage_no, part["text"], part["char_start"], part["char_end"],
                        _quality_number(ocr_quality), source_file, now, source_path,
                        source_collection, source_quality, text_layer, layer_confidence,
                        page_kind, page_label, source_url, content_hash, config_hash,
                        part["token_count"],
                    ))
                conn.executemany(
                    """
                    INSERT INTO passages(
                        passage_id, page_id, abteilung, band, text_type, page_no,
                        passage_no, text, char_start, char_end, ocr_quality,
                        source_pdf, created_at, source_path, source_collection,
                        source_quality, text_layer, text_layer_confidence,
                        page_kind, page_label, source_url, page_content_hash,
                        chunk_config, token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                pages_updated += 1
                passages_written += len(values)
            conn.commit()
            conn.execute(
                """
                UPDATE passage_builds
                SET pages_updated=?, passages_written=? WHERE id=?
                """,
                (pages_updated, passages_written, build_id),
            )
            conn.commit()
            print(
                f"  passage pages: {pages_updated:,} | written: {passages_written:,}",
                flush=True,
            )

        dirty = _get_state(conn, "passages_fts_dirty", "1") == "1"
        if pages_updated or stale_removed or dirty:
            print("  rebuilding passages_fts once...", flush=True)
            conn.execute("INSERT INTO passages_fts(passages_fts) VALUES ('rebuild')")
        _set_state(conn, "passages_fts_dirty", "0")
        _set_state(conn, "passage_config", config_hash)
        covered_pages = conn.execute(
            "SELECT COUNT(DISTINCT page_id) FROM passages WHERE chunk_config=?",
            (config_hash,),
        ).fetchone()[0]
        _set_state(
            conn, "passages_complete",
            "1" if covered_pages == total_pages else "0",
        )
        conn.execute(
            """
            UPDATE passage_builds
            SET completed_at=?, pages_updated=?, passages_written=?, status='complete'
            WHERE id=?
            """,
            (datetime.now().isoformat(), pages_updated, passages_written, build_id),
        )
        conn.commit()
    except Exception as exc:
        conn.execute(
            """
            UPDATE passage_builds SET completed_at=?, pages_updated=?,
                passages_written=?, status='failed', error=? WHERE id=?
            """,
            (
                datetime.now().isoformat(), pages_updated, passages_written,
                f"{type(exc).__name__}: {exc}"[:500], build_id,
            ),
        )
        conn.commit()
        raise
    finally:
        conn.close()

    result = passage_status(config_hash=config_hash)
    result.update({
        "pages_updated": pages_updated,
        "passages_written": passages_written,
        "stale_removed": stale_removed,
    })
    return result


def passage_status(config_hash: str | None = None) -> dict:
    config = load_config()
    db_path = Path(config["paths"]["metadata_db"])
    if not db_path.exists():
        return {"available": False, "reason": "metadata_db_missing"}
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "passages"):
            return {"available": False, "reason": "passages_table_missing"}
        columns = _column_names(conn, "passages")
        total = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        pages = conn.execute("SELECT COUNT(DISTINCT page_id) FROM passages").fetchone()[0]
        fts = (
            conn.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0]
            if _table_exists(conn, "passages_fts") else 0
        )
        orphaned = conn.execute(
            """
            SELECT COUNT(*) FROM passages WHERE page_id NOT IN (
                SELECT id FROM chunks
                WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
                  AND COALESCE(chunk_text, '') <> ''
                  AND COALESCE(ocr_quality, 'medium') <> 'failed'
            )
            """
        ).fetchone()[0]
        current_config = config_hash or _get_state(conn, "passage_config", "")
        current_rows = 0
        current_pages = 0
        if current_config and "chunk_config" in columns:
            current_rows, current_pages = conn.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT page_id)
                FROM passages WHERE chunk_config=?
                """,
                (current_config,),
            ).fetchone()
        eligible_pages = _eligible_page_count(conn)
        dirty = _get_state(conn, "passages_fts_dirty", "0") == "1"
        complete = _get_state(conn, "passages_complete", "0") == "1"
        coverage_complete = (
            pages == eligible_pages
            and current_pages == eligible_pages
        )
        return {
            "available": bool(
                total and not dirty and complete and coverage_complete and total == fts
            ),
            "schema_version": PASSAGE_SCHEMA_VERSION,
            "config": current_config,
            "passages": total,
            "fts": fts,
            "pages": pages,
            "eligible_pages": eligible_pages,
            "current_config_passages": current_rows,
            "current_config_pages": current_pages,
            "orphaned": orphaned,
            "dirty": dirty,
            "complete": complete,
            "coverage": round(pages / eligible_pages, 6) if eligible_pages else 1.0,
        }
    finally:
        conn.close()


def search_passages(
    conn: sqlite3.Connection,
    fts_query: str,
    route: str = "all",
    limit: int = 40,
    max_per_page: int = 2,
) -> list[dict]:
    """Return passage BM25 candidates, or an empty list when the index is unsafe."""
    if not _table_exists(conn, "passages") or not _table_exists(conn, "passages_fts"):
        return []
    if _get_state(conn, "passages_fts_dirty", "0") == "1":
        return []
    if _get_state(conn, "passages_complete", "0") != "1":
        return []
    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM passages), (SELECT COUNT(*) FROM passages_fts)"
    ).fetchone()
    if not counts or not counts[0] or counts[0] != counts[1]:
        return []

    route_filter = ""
    if route == "main_text":
        route_filter = "AND p.text_type='TEXT'"
    elif route == "apparat":
        route_filter = "AND p.text_type='APPARAT'"
    rows = conn.execute(
        f"""
        SELECT p.passage_id, p.page_id, p.abteilung, p.band, p.text_type,
               p.page_no, p.passage_no, p.text, p.char_start, p.char_end,
               rank, COALESCE(NULLIF(p.source_path, ''), c.source_path),
               COALESCE(c.source_collection, p.source_collection, 'ocr'),
               COALESCE(c.source_quality, p.source_quality, 'ocr'),
               COALESCE(c.text_layer, p.text_layer, 'unclassified'),
               COALESCE(c.text_layer_confidence, p.text_layer_confidence, 0),
               COALESCE(c.text_layer_provenance, ''),
               COALESCE(c.page_kind, p.page_kind, 'pdf'),
               COALESCE(c.page_label, p.page_label, ''),
               COALESCE(c.source_url, p.source_url, ''),
               p.token_count, COALESCE(c.ocr_quality, 'medium')
        FROM passages_fts f
        JOIN passages p ON f.rowid=p.rowid
        JOIN chunks c ON c.id=p.page_id
        WHERE passages_fts MATCH ? {route_filter}
        ORDER BY rank
        LIMIT ?
        """,
        (fts_query, max(limit * 3, limit)),
    ).fetchall()

    results: list[dict] = []
    page_counts: dict[str, int] = {}
    for row in rows:
        page_id = row[1]
        if page_counts.get(page_id, 0) >= max_per_page:
            continue
        page_counts[page_id] = page_counts.get(page_id, 0) + 1
        results.append({
            "id": f"passage:{row[0]}",
            "record_type": "passage",
            "passage_id": row[0],
            "page_id": page_id,
            "abteilung": row[2],
            "band": row[3],
            "type": row[4],
            "page": row[5],
            "passage_no": row[6],
            "text": row[7],
            "char_start": row[8],
            "char_end": row[9],
            "rank": row[10],
            "_bm25_rank": row[10],
            "source_path": row[11],
            "source_collection": row[12],
            "source_quality": row[13],
            "text_layer": row[14],
            "text_layer_confidence": row[15],
            "text_layer_provenance": row[16],
            "page_kind": row[17],
            "page_label": row[18],
            "source_url": row[19],
            "token_count": row[20],
            "ocr_quality": row[21],
            "is_main_text": row[4] == "TEXT",
            "is_editorial_comment": row[4] == "APPARAT",
            "_retrieval_source": "passage_fts",
            "_retrieval_sources": ["passage_fts"],
        })
        if len(results) >= limit:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--overlap-tokens", type=int, default=DEFAULT_OVERLAP_TOKENS)
    parser.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_TOKENS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()
    if args.status:
        print(json.dumps(passage_status(), ensure_ascii=False, indent=2))
        return 0
    if args.build:
        result = build_passage_index(
            target_tokens=args.target_tokens,
            overlap_tokens=args.overlap_tokens,
            min_tokens=args.min_tokens,
            max_tokens=args.max_tokens,
            rebuild=args.rebuild,
            max_pages=args.max_pages,
        )
        from index_version import get_current_version, store_version

        result["index_version"] = (
            get_current_version() if result.get("no_op")
            else store_version("passage_v2_build")
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
