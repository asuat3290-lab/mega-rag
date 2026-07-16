#!/usr/bin/env python3
"""Import structured MEGAdigital JSONL records into the existing MEGA RAG FTS index.

This importer is deliberately FTS-first: importing authoritative digital text does
not invoke Ollama or any API. Vector embeddings remain optional future work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


EXTRA_COLUMNS = {
    "source_collection": "TEXT DEFAULT 'ocr'",
    "source_quality": "TEXT DEFAULT 'ocr'",
    "source_record_id": "TEXT",
    "source_doc": "TEXT",
    "source_part": "TEXT",
    "source_url": "TEXT",
    "source_title": "TEXT",
    "page_kind": "TEXT DEFAULT 'pdf'",
    "page_label": "TEXT",
    "content_hash": "TEXT",
    "text_layer": "TEXT DEFAULT 'unclassified'",
    "text_layer_confidence": "REAL DEFAULT 0",
    "text_layer_provenance": "TEXT DEFAULT ''",
    "text_layer_version": "TEXT DEFAULT ''",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    for name, definition in EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_source_scope "
        "ON chunks(source_collection, mega_abteilung, band, source_type)"
    )
    conn.execute(
        "UPDATE chunks SET source_collection = 'ocr' "
        "WHERE source_collection IS NULL OR source_collection = ''"
    )
    conn.execute(
        "UPDATE chunks SET source_quality = 'ocr' "
        "WHERE source_quality IS NULL OR source_quality = ''"
    )
    conn.execute(
        "UPDATE chunks SET page_kind = 'pdf' "
        "WHERE page_kind IS NULL OR page_kind = ''"
    )
    conn.commit()


def parse_volume(volume_id: str) -> tuple[str, str, str]:
    match = re.fullmatch(r"MEGA_(I|II|III|IV)_(\d+)(?:_(\d+))?", volume_id or "")
    if not match:
        return "UNKNOWN", "UNKNOWN", ""
    abteilung, band, part = match.groups()
    normalized_band = f"{band}.{part}" if part and part != "0" else band
    return abteilung, normalized_band, part or "0"


def extract_page_label(text: str) -> str:
    match = re.search(r"\|([^|\r\n]{1,16})\|", text or "")
    return match.group(1).strip() if match else ""


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            text = str(record.get("text") or "").strip()
            if text:
                record["text"] = text
                yield record


def normalize_record(record: dict[str, Any], source_path: Path) -> dict[str, Any]:
    record_id = str(record.get("id") or "")
    if not record_id:
        raise ValueError("MEGAdigital record has no id")
    volume_id = str(record.get("volume_id") or "")
    abteilung, band, part = parse_volume(volume_id)
    text = record["text"]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = "md_" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:20]
    page = int(record.get("page") or 0)
    source_url = str(record.get("frontend_url") or record.get("endpoint_url") or record.get("url") or "")
    return {
        "id": chunk_id,
        "source_path": f"megadigital/{volume_id}/{record_id}",
        "mega_abteilung": abteilung,
        "band": band,
        "source_type": "TEXT",
        "page": page,
        "is_main_text": 1,
        "is_editorial_comment": 0,
        "language": str(record.get("language") or "de"),
        "chunk_text": text,
        "char_count": len(text),
        "source_file": source_path.name,
        "ocr_quality": "high",
        "alpha_ratio": 1.0,
        "german_word_ratio": 1.0,
        "source_collection": "megadigital",
        "source_quality": "authoritative_digital",
        "source_record_id": record_id,
        "source_doc": str(record.get("doc") or ""),
        "source_part": part,
        "source_url": source_url,
        "source_title": str(record.get("title") or ""),
        "page_kind": "megadigital_page",
        "page_label": extract_page_label(text),
        "content_hash": content_hash,
        "text_layer": "author_text",
        "text_layer_confidence": 0.99,
        "text_layer_provenance": "text-layer-v1:structured_megadigital_text",
        "text_layer_version": "text-layer-v1",
    }


def import_records(source: Path, metadata_db: Path, dry_run: bool, batch_size: int, include_unmapped: bool) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    counters: Counter[str] = Counter()
    volume_counts: Counter[str] = Counter()
    started_at = datetime.now()

    conn = None if dry_run else sqlite3.connect(str(metadata_db))
    if conn:
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(conn)

    sql = """
        INSERT INTO chunks (
            id, source_path, mega_abteilung, band, source_type, page,
            is_main_text, is_editorial_comment, language, chunk_text,
            char_count, source_file, ocr_quality, alpha_ratio,
            german_word_ratio, indexed_at, source_collection, source_quality,
            source_record_id, source_doc, source_part, source_url, source_title,
            page_kind, page_label, content_hash, text_layer,
            text_layer_confidence, text_layer_provenance, text_layer_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_path=excluded.source_path,
            mega_abteilung=excluded.mega_abteilung,
            band=excluded.band,
            source_type=excluded.source_type,
            page=excluded.page,
            language=excluded.language,
            chunk_text=excluded.chunk_text,
            char_count=excluded.char_count,
            source_file=excluded.source_file,
            ocr_quality=excluded.ocr_quality,
            alpha_ratio=excluded.alpha_ratio,
            german_word_ratio=excluded.german_word_ratio,
            indexed_at=excluded.indexed_at,
            source_collection=excluded.source_collection,
            source_quality=excluded.source_quality,
            source_record_id=excluded.source_record_id,
            source_doc=excluded.source_doc,
            source_part=excluded.source_part,
            source_url=excluded.source_url,
            source_title=excluded.source_title,
            page_kind=excluded.page_kind,
            page_label=excluded.page_label,
            content_hash=excluded.content_hash,
            text_layer=CASE
                WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                    THEN chunks.text_layer ELSE excluded.text_layer END,
            text_layer_confidence=CASE
                WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                    THEN chunks.text_layer_confidence ELSE excluded.text_layer_confidence END,
            text_layer_provenance=CASE
                WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                    THEN chunks.text_layer_provenance ELSE excluded.text_layer_provenance END,
            text_layer_version=CASE
                WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                    THEN chunks.text_layer_version ELSE excluded.text_layer_version END
    """

    batch: list[tuple[Any, ...]] = []
    for record in iter_records(source):
        item = normalize_record(record, source)
        counters["records_seen"] += 1
        if item["mega_abteilung"] == "UNKNOWN" and not include_unmapped:
            counters["unmapped_skipped"] += 1
            continue
        counters["records"] += 1
        volume_counts[f"{item['mega_abteilung']}/{item['band']}"] += 1
        if dry_run:
            continue
        batch.append((
            item["id"], item["source_path"], item["mega_abteilung"], item["band"],
            item["source_type"], item["page"], item["is_main_text"],
            item["is_editorial_comment"], item["language"], item["chunk_text"],
            item["char_count"], item["source_file"], item["ocr_quality"],
            item["alpha_ratio"], item["german_word_ratio"], datetime.now().isoformat(),
            item["source_collection"], item["source_quality"], item["source_record_id"],
            item["source_doc"], item["source_part"], item["source_url"],
            item["source_title"], item["page_kind"], item["page_label"], item["content_hash"],
            item["text_layer"], item["text_layer_confidence"],
            item["text_layer_provenance"], item["text_layer_version"],
        ))
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            conn.commit()
            counters["written"] += len(batch)
            batch.clear()

    if conn and batch:
        conn.executemany(sql, batch)
        conn.commit()
        counters["written"] += len(batch)

    if conn:
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        conn.commit()
        conn.close()

    return {
        "source": str(source),
        "metadata_db": str(metadata_db),
        "dry_run": dry_run,
        "records_seen": counters["records_seen"],
        "records": counters["records"],
        "unmapped_skipped": counters["unmapped_skipped"],
        "written": counters["written"],
        "volumes": dict(sorted(volume_counts.items())),
        "finished_at": datetime.now().isoformat(),
        "elapsed_seconds": round((datetime.now() - started_at).total_seconds(), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import authoritative MEGAdigital JSONL into MEGA RAG")
    parser.add_argument("--source", required=True, help="Canonical MEGAdigital JSONL file")
    parser.add_argument("--metadata-db", default="D:/mega_rag/metadata.db")
    parser.add_argument("--dry-run", action="store_true", help="Inspect coverage without modifying the database")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--include-unmapped", action="store_true", help="Import records without a recognized MEGA volume id" )
    parser.add_argument("--manifest", default="D:/mega_rag/megadigital_import_manifest.json")
    args = parser.parse_args()

    report = import_records(
        Path(args.source), Path(args.metadata_db), args.dry_run,
        max(1, args.batch_size), args.include_unmapped,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.dry_run:
        manifest = Path(args.manifest)
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        from index_version import store_version
        report["index_version"] = store_version("megadigital_import")
        manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())