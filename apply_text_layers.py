#!/usr/bin/env python3
"""Apply conservative text-layer metadata to the MEGA RAG chunks table."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from index_version import store_version
from text_layer import CLASSIFIER_VERSION, classify_records

BASE_DIR = Path(__file__).resolve().parent
LAYER_COLUMNS = {
    "text_layer": "TEXT DEFAULT 'unclassified'",
    "text_layer_confidence": "REAL DEFAULT 0",
    "text_layer_provenance": "TEXT DEFAULT ''",
    "text_layer_version": "TEXT DEFAULT ''",
}


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    for name, definition in LAYER_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} {definition}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS text_layer_runs (
            id INTEGER PRIMARY KEY,
            classifier_version TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            counts_json TEXT NOT NULL,
            manual_rows_preserved INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_text_layer "
        "ON chunks(text_layer, mega_abteilung, band, source_type)"
    )
    conn.commit()


def load_groups(conn: sqlite3.Connection) -> list[list[dict]]:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    provenance_expr = (
        "text_layer_provenance" if "text_layer_provenance" in existing
        else "'' AS text_layer_provenance"
    )
    select_columns = (
        "id, source_file, source_collection, source_type, mega_abteilung, band, "
        f"page, chunk_text, source_title, {provenance_expr}"
    )
    rows = conn.execute(
        f"SELECT {select_columns} FROM chunks ORDER BY source_file, source_collection, page, id"
    ).fetchall()
    names = [
        "id", "source_file", "source_collection", "source_type", "mega_abteilung",
        "band", "page", "chunk_text", "source_title", "text_layer_provenance",
    ]
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        record = dict(zip(names, row))
        key = (
            record.get("source_collection") or "ocr",
            record.get("source_file") or record.get("source_title") or "unknown",
            record.get("source_type") or "UNKNOWN",
        )
        groups[key].append(record)
    return list(groups.values())


def compute_updates(groups: list[list[dict]]) -> tuple[list[tuple], Counter, int]:
    updates: list[tuple] = []
    counts: Counter[str] = Counter()
    manual_preserved = 0
    for group in groups:
        decisions = classify_records(group)
        for record in group:
            provenance = str(record.get("text_layer_provenance") or "")
            if provenance.startswith("manual:"):
                manual_preserved += 1
                continue
            decision = decisions[record["id"]]
            counts[decision.layer] += 1
            updates.append((
                decision.layer,
                decision.confidence,
                decision.provenance,
                CLASSIFIER_VERSION,
                record["id"],
            ))
    return updates, counts, manual_preserved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    config = load_config()
    db_path = Path(config["paths"]["metadata_db"])
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        if args.apply:
            ensure_schema(conn)
        groups = load_groups(conn)
        updates, counts, manual_preserved = compute_updates(groups)
        summary = {
            "classifier_version": CLASSIFIER_VERSION,
            "mode": "apply" if args.apply else "dry_run",
            "updates": len(updates),
            "manual_rows_preserved": manual_preserved,
            "counts": dict(counts.most_common()),
        }
        if not args.apply:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        sql = """
            UPDATE chunks
            SET text_layer=?, text_layer_confidence=?, text_layer_provenance=?,
                text_layer_version=?
            WHERE id=? AND COALESCE(text_layer_provenance, '') NOT LIKE 'manual:%'
        """
        for start in range(0, len(updates), args.batch_size):
            conn.executemany(sql, updates[start:start + args.batch_size])
            conn.commit()
            print(f"  text layers: {min(start + args.batch_size, len(updates))}/{len(updates)}", flush=True)

        conn.execute(
            """
            INSERT INTO text_layer_runs
                (classifier_version, applied_at, counts_json, manual_rows_preserved)
            VALUES (?, ?, ?, ?)
            """,
            (
                CLASSIFIER_VERSION,
                datetime.now().isoformat(),
                json.dumps(dict(counts), ensure_ascii=False, sort_keys=True),
                manual_preserved,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    summary["index_version"] = store_version("text_layer_metadata_v1", config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())