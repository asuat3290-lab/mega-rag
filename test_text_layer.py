#!/usr/bin/env python3
"""Focused regression checks for conservative text-layer classification."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from text_layer import classify_records

BASE_DIR = Path(__file__).resolve().parent


def load_records(conn, abteilung, band, source_type, pages, collection="ocr"):
    placeholders = ",".join("?" for _ in pages)
    rows = conn.execute(
        f"""
        SELECT id, source_file, source_collection, source_type, mega_abteilung,
               band, page, chunk_text, source_title
        FROM chunks
        WHERE COALESCE(source_collection, 'ocr')=? AND mega_abteilung=?
          AND band=? AND source_type=? AND page IN ({placeholders})
        ORDER BY page
        """,
        [collection, abteilung, band, source_type, *pages],
    ).fetchall()
    names = [
        "id", "source_file", "source_collection", "source_type", "mega_abteilung",
        "band", "page", "chunk_text", "source_title",
    ]
    return [dict(zip(names, row)) for row in rows]


def check_incremental_upsert(failures: list[str]) -> None:
    """Incremental OCR writes must keep rowid and human-reviewed layer metadata."""
    from build_index import _flush_batch

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("""
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY, source_path TEXT, mega_abteilung TEXT,
                band TEXT, source_type TEXT, page INTEGER, is_main_text INTEGER,
                is_editorial_comment INTEGER, language TEXT, chunk_text TEXT,
                char_count INTEGER, source_file TEXT, ocr_quality TEXT,
                alpha_ratio REAL, german_word_ratio REAL, content_hash TEXT,
                text_layer TEXT, text_layer_confidence REAL,
                text_layer_provenance TEXT, text_layer_version TEXT,
                indexed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE index_progress (
                source_path TEXT PRIMARY KEY, indexed_at TEXT, status TEXT,
                reason TEXT, content_hash TEXT, embedding_status TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE index_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        meta = {
            "id": "fixture", "source_path": "fixture/page_1.txt",
            "mega_abteilung": "I", "band": "2", "source_type": "TEXT",
            "page": 1, "is_main_text": True, "is_editorial_comment": False,
            "language": "de", "chunk_text": "first", "char_count": 5,
            "source_file": "fixture", "ocr_quality": "high",
            "alpha_ratio": 1.0, "german_word_ratio": 1.0,
            "content_hash": "hash-1", "text_layer": "textband_unclassified",
            "text_layer_confidence": 0.0,
            "text_layer_provenance": "text-layer-v1:new_ocr_row_unclassified",
            "text_layer_version": "text-layer-v1",
        }
        _flush_batch(conn, [meta], None, "", False)
        first_rowid = conn.execute(
            "SELECT rowid FROM chunks WHERE id='fixture'"
        ).fetchone()[0]
        conn.execute("""
            UPDATE chunks SET text_layer='author_text', text_layer_confidence=1,
                text_layer_provenance='manual:fixture/2026-07-16/test',
                text_layer_version='manual-v1' WHERE id='fixture'
        """)
        conn.commit()
        meta = dict(meta, chunk_text="second", char_count=6, content_hash="hash-2")
        _flush_batch(conn, [meta], None, "", False)
        row = conn.execute("""
            SELECT rowid, chunk_text, text_layer, text_layer_provenance
            FROM chunks WHERE id='fixture'
        """).fetchone()
        if row[0] != first_rowid:
            failures.append("incremental upsert changed chunks.rowid")
        if row[1] != "second":
            failures.append("incremental upsert did not refresh OCR text")
        if row[2] != "author_text" or not row[3].startswith("manual:"):
            failures.append("incremental upsert overwrote manual text-layer metadata")
        passage_state = conn.execute(
            "SELECT value FROM index_state WHERE key='passages_complete'"
        ).fetchone()
        if passage_state != ("0",):
            failures.append("incremental OCR write did not invalidate passage coverage")
    finally:
        conn.close()


def main() -> int:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    conn = sqlite3.connect(config["paths"]["metadata_db"])
    failures = []
    check_incremental_upsert(failures)
    try:
        i2_pages = [5, 10, 39, 60, 66, 115, 298, 305]
        i2_records = load_records(conn, "I", "2", "TEXT", i2_pages)
        i2_decisions = classify_records(i2_records)
        by_page = {
            record["page"]: i2_decisions[record["id"]].layer for record in i2_records
        }
        expected = {
            5: "table_of_contents",
            10: "editorial_intro",
            39: "editorial_intro",
            60: "editorial_note",
            66: "textband_unclassified",
            115: "textband_unclassified",
            298: "textband_unclassified",
            305: "textband_unclassified",
        }
        for page, layer in expected.items():
            if by_page.get(page) != layer:
                failures.append(f"I/2 p.{page}: expected {layer}, got {by_page.get(page)}")

        ii3_source = "ZWEITE BAND 3 TEXT.TEIL 2"
        ii3_rows = conn.execute(
            """
            SELECT id, source_file, source_collection, source_type, mega_abteilung,
                   band, page, chunk_text, source_title
            FROM chunks WHERE source_file=? ORDER BY page
            """,
            (ii3_source,),
        ).fetchall()
        names = [
            "id", "source_file", "source_collection", "source_type", "mega_abteilung",
            "band", "page", "chunk_text", "source_title",
        ]
        ii3_records = [dict(zip(names, row)) for row in ii3_rows]
        ii3_decisions = classify_records(ii3_records)
        target = next(record for record in ii3_records if record["page"] == 18)
        decision = ii3_decisions[target["id"]]
        if decision.layer != "editorial_intro":
            failures.append(
                f"II/3 part 2 p.18: expected editorial_intro, got {decision.layer}"
            )
        ii3_part5_rows = conn.execute(
            """
            SELECT id, source_file, source_collection, source_type, mega_abteilung,
                   band, page, chunk_text, source_title
            FROM chunks WHERE source_file=? ORDER BY page
            """,
            ("ZWEITE BAND 3TEXT.TEIL 5",),
        ).fetchall()
        ii3_part5 = [dict(zip(names, row)) for row in ii3_part5_rows]
        part5_decisions = classify_records(ii3_part5)
        part5_target = next(record for record in ii3_part5 if record["page"] == 26)
        part5_decision = part5_decisions[part5_target["id"]]
        if part5_decision.layer != "editorial_intro":
            failures.append(
                "II/3 part 5 p.26: expected editorial_intro, "
                f"got {part5_decision.layer}"
            )
        apparat = load_records(conn, "I", "2", "APPARAT", [1, 100])
        for record in apparat:
            decision = classify_records([record])[record["id"]]
            if decision.layer != "apparatus":
                failures.append(f"APPARAT {record['id']}: {decision.layer}")

        digital_row = conn.execute(
            """
            SELECT id, source_file, source_collection, source_type, mega_abteilung,
                   band, page, chunk_text, source_title
            FROM chunks WHERE source_collection='megadigital' LIMIT 1
            """
        ).fetchone()
        names = [
            "id", "source_file", "source_collection", "source_type", "mega_abteilung",
            "band", "page", "chunk_text", "source_title",
        ]
        digital = dict(zip(names, digital_row))
        if classify_records([digital])[digital["id"]].layer != "author_text":
            failures.append("MEGAdigital TEXT was not classified author_text")
    finally:
        conn.close()

    if failures:
        print("FAIL")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: conservative text-layer fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())