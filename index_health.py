#!/usr/bin/env python3
"""Read-only consistency checks for the MEGA RAG corpus and indexes."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml

from index_version import compute_index_version, get_current_version

BASE_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? LIMIT 1", (table,)
    ).fetchone() is not None


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False

def collect_health(exact_vectors: bool = True, compute_version: bool = True, deep_checks: bool = True) -> dict[str, Any]:
    config = load_config()
    ocr_root = Path(config["paths"]["ocr_texts"])
    db_path = Path(config["paths"]["metadata_db"])
    vector_path = Path(config["paths"]["vector_db"])

    ocr_paths = {
        str(path.relative_to(ocr_root)) for path in ocr_root.rglob("page_*.txt")
    } if ocr_root.exists() else set()

    pid_file = BASE_DIR / "vector_repair.pid"
    active_repair_pid = 0
    if pid_file.exists():
        try:
            candidate_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if _pid_is_running(candidate_pid):
                active_repair_pid = candidate_pid
        except (OSError, ValueError):
            pass

    report: dict[str, Any] = {
        "active_vector_repair_pid": active_repair_pid,
        "paths": {
            "ocr": str(ocr_root),
            "metadata_db": str(db_path),
            "vector_db": str(vector_path),
        },
        "ocr_files": len(ocr_paths),
        "warnings": [],
        "errors": [],
    }

    if not db_path.exists():
        report["errors"].append("metadata_db_missing")
        report["healthy"] = False
        return report

    conn = connect_read_only(db_path)
    try:
        report["sqlite_integrity"] = (
            conn.execute("PRAGMA integrity_check").fetchone()[0]
            if deep_checks else "not_checked"
        )
        chunk_columns = column_names(conn, "chunks")
        source_expr = (
            "COALESCE(source_collection, 'ocr')"
            if "source_collection" in chunk_columns
            else "'ocr'"
        )
        report["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        report["fts"] = (
            conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            if table_exists(conn, "chunks_fts")
            else 0
        )
        report["passages"] = (
            conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
            if table_exists(conn, "passages")
            else 0
        )
        report["passage_fts"] = (
            conn.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0]
            if table_exists(conn, "passages_fts")
            else 0
        )
        report["passage_pages"] = (
            conn.execute("SELECT COUNT(DISTINCT page_id) FROM passages").fetchone()[0]
            if report["passages"] else 0
        )
        report["passage_orphaned"] = (
            conn.execute(
                """
                SELECT COUNT(*) FROM passages WHERE page_id NOT IN (
                    SELECT id FROM chunks
                    WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
                      AND COALESCE(chunk_text, '') <> ''
                      AND COALESCE(ocr_quality, 'medium') <> 'failed'
                )
                """
            ).fetchone()[0] if report["passages"] else 0
        )
        report["passage_eligible_pages"] = conn.execute(
            """
            SELECT COUNT(*) FROM chunks
            WHERE COALESCE(char_count, LENGTH(chunk_text), 0) >= 80
              AND COALESCE(chunk_text, '') <> ''
              AND COALESCE(ocr_quality, 'medium') <> 'failed'
            """
        ).fetchone()[0]
        report["passage_dirty"] = False
        report["passage_complete"] = report["passages"] == 0
        if table_exists(conn, "index_state"):
            dirty_row = conn.execute(
                "SELECT value FROM index_state WHERE key='passages_fts_dirty'"
            ).fetchone()
            complete_row = conn.execute(
                "SELECT value FROM index_state WHERE key='passages_complete'"
            ).fetchone()
            report["passage_dirty"] = bool(dirty_row and dirty_row[0] == "1")
            report["passage_complete"] = bool(
                complete_row and complete_row[0] == "1"
            )
        report["content_hash_missing"] = (
            conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE COALESCE(content_hash, '')=''"
            ).fetchone()[0]
            if "content_hash" in chunk_columns
            else report["chunks"]
        )
        report["sources"] = {
            row[0]: {"chunks": row[1], "chars": row[2]}
            for row in conn.execute(
                f"""
                SELECT {source_expr}, COUNT(*), COALESCE(SUM(char_count), 0)
                FROM chunks GROUP BY {source_expr} ORDER BY {source_expr}
                """
            )
        }

        progress_paths: set[str] = set()
        report["progress"] = {}
        report["embedding_progress"] = {}
        if table_exists(conn, "index_progress"):
            progress_columns = column_names(conn, "index_progress")
            progress_paths = {
                row[0] for row in conn.execute("SELECT source_path FROM index_progress")
            }
            status_expr = (
                "COALESCE(status, 'unknown')" if "status" in progress_columns else "'legacy'"
            )
            embedding_expr = (
                "COALESCE(embedding_status, 'unknown')"
                if "embedding_status" in progress_columns
                else "'legacy'"
            )
            report["progress"] = {
                row[0]: row[1]
                for row in conn.execute(
                    f"SELECT {status_expr}, COUNT(*) FROM index_progress GROUP BY {status_expr}"
                )
            }
            report["embedding_progress"] = {
                row[0]: row[1]
                for row in conn.execute(
                    f"SELECT {embedding_expr}, COUNT(*) FROM index_progress GROUP BY {embedding_expr}"
                )
            }
        report["progress_rows"] = len(progress_paths)
        report["ocr_untracked"] = len(ocr_paths - progress_paths)
        report["progress_without_ocr"] = len(progress_paths - ocr_paths)

        ocr_ids = {
            row[0]
            for row in conn.execute(
                f"SELECT id FROM chunks WHERE {source_expr}='ocr'"
            )
        }
        digital_ids = {
            row[0]
            for row in conn.execute(
                f"SELECT id FROM chunks WHERE {source_expr}='megadigital'"
            )
        }
    finally:
        conn.close()

    vector_report: dict[str, Any] = {
        "available": False,
        "rows": 0,
        "unique": None,
        "duplicates": None,
        "ocr_covered": None,
        "ocr_missing": None,
        "digital_covered": None,
        "stale": None,
    }
    if vector_path.exists():
        try:
            import lancedb

            table = lancedb.connect(str(vector_path)).open_table("chunks")
            vector_report["available"] = True
            vector_report["rows"] = int(table.count_rows())
            if exact_vectors:
                vector_list = table.to_arrow()["id"].to_pylist()
                vector_ids = set(vector_list)
                vector_report.update(
                    {
                        "unique": len(vector_ids),
                        "duplicates": len(vector_list) - len(vector_ids),
                        "ocr_covered": len(vector_ids & ocr_ids),
                        "ocr_missing": len(ocr_ids - vector_ids),
                        "digital_covered": len(vector_ids & digital_ids),
                        "stale": len(vector_ids - ocr_ids - digital_ids),
                    }
                )
        except Exception as exc:
            vector_report["error"] = f"{type(exc).__name__}: {exc}"
    report["vectors"] = vector_report

    report["stored_index_version"] = get_current_version(config)
    if compute_version:
        try:
            report["computed_index_version"] = compute_index_version(config)
            report["version_matches"] = (
                report["stored_index_version"] == report["computed_index_version"]
            )
        except Exception as exc:
            report["computed_index_version"] = ""
            report["version_matches"] = False
            report["errors"].append(f"version_compute_failed:{type(exc).__name__}")
    else:
        report["computed_index_version"] = ""
        report["version_matches"] = None

    if deep_checks and report["sqlite_integrity"] != "ok":
        report["errors"].append("sqlite_integrity_failed")
    if report["fts"] != report["chunks"]:
        report["errors"].append("fts_count_mismatch")
    if report["ocr_untracked"]:
        report["errors"].append("ocr_files_not_in_progress")
    if report["progress_without_ocr"]:
        report["warnings"].append("progress_rows_without_ocr_file")
    if report["content_hash_missing"]:
        report["errors"].append("legacy_chunks_missing_content_hash")
    if report.get("passages") != report.get("passage_fts"):
        report["errors"].append("passage_fts_count_mismatch")
    if report.get("passage_dirty"):
        report["errors"].append("passage_fts_dirty")
    if report.get("passages") and not report.get("passage_complete"):
        report["warnings"].append("passage_index_incomplete_page_fallback_active")
    if report.get("passage_orphaned"):
        report["errors"].append("orphaned_passages")
    if report.get("passages") and report.get("passage_pages") != report.get("passage_eligible_pages"):
        report["warnings"].append("passage_page_coverage_incomplete")
    if exact_vectors and vector_report.get("duplicates"):
        report["errors"].append("duplicate_vectors")
    if exact_vectors and vector_report.get("stale"):
        report["errors"].append("stale_vectors")
    if exact_vectors and vector_report.get("ocr_missing"):
        report["warnings"].append("ocr_chunks_missing_vectors")
    if report["version_matches"] is False:
        if report.get("active_vector_repair_pid"):
            report["warnings"].append("version_pending_active_vector_repair")
        else:
            report["errors"].append("stored_version_is_stale")

    report["healthy"] = not report["errors"]
    return report


def print_summary(report: dict[str, Any]) -> None:
    print("MEGA RAG index health")
    if report.get("active_vector_repair_pid"):
        print(f"  Active repair:   PID {report['active_vector_repair_pid']}")
    print(f"  OCR files:       {report.get('ocr_files', 0):,}")
    print(
        f"  Progress:        {report.get('progress_rows', 0):,} "
        f"(untracked {report.get('ocr_untracked', 0):,})"
    )
    print(
        f"  Chunks / FTS:    {report.get('chunks', 0):,} / {report.get('fts', 0):,}"
    )
    for source, values in report.get("sources", {}).items():
        print(f"    {source}: {values['chunks']:,} chunks, {values['chars']:,} chars")
    print(
        f"  Passages / FTS:  {report.get('passages', 0):,} / "
        f"{report.get('passage_fts', 0):,}; pages "
        f"{report.get('passage_pages', 0):,}/{report.get('passage_eligible_pages', 0):,}; "
        f"dirty={report.get('passage_dirty', False)} complete={report.get('passage_complete', False)}"
    )
    print(f"  Content hashes:  missing {report.get('content_hash_missing', 0):,}")
    print(f"  Progress status: {report.get('progress', {})}")
    print(f"  Embeddings:      {report.get('embedding_progress', {})}")
    vectors = report.get("vectors", {})
    if vectors.get("unique") is None:
        print(f"  Vectors:         {vectors.get('rows', 0):,} rows (quick mode)")
    else:
        print(
            "  Vectors:         "
            f"{vectors.get('rows', 0):,} rows / {vectors.get('unique', 0):,} unique / "
            f"{vectors.get('duplicates', 0):,} duplicate"
        )
        print(
            "  Vector coverage: "
            f"OCR {vectors.get('ocr_covered', 0):,} covered, "
            f"{vectors.get('ocr_missing', 0):,} missing; "
            f"digital {vectors.get('digital_covered', 0):,}; "
            f"stale {vectors.get('stale', 0):,}"
        )
    print(
        "  Index version:   "
        f"stored={report.get('stored_index_version') or '-'} "
        f"computed={report.get('computed_index_version') or '-'} "
        f"match={report.get('version_matches', False)}"
    )
    print(f"  SQLite:          {report.get('sqlite_integrity', 'unknown')}")
    print(f"  Result:          {'OK' if report.get('healthy') else 'ERROR'}")
    if report.get("warnings"):
        print("  Warnings:        " + ", ".join(report["warnings"]))
    if report.get("errors"):
        print("  Errors:          " + ", ".join(report["errors"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON")
    parser.add_argument(
        "--quick", action="store_true", help="Skip exact vector uniqueness/coverage scan"
    )
    args = parser.parse_args()
    report = collect_health(
        exact_vectors=not args.quick,
        compute_version=not args.quick,
        deep_checks=not args.quick,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
    return 0 if report.get("healthy") else 1


if __name__ == "__main__":
    sys.exit(main())