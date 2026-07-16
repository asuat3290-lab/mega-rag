#!/usr/bin/env python3
"""Create a consistent, self-describing backup of the MEGA RAG index."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

from index_health import collect_health
from index_version import get_current_version

BASE_DIR = Path(__file__).resolve().parent
SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".bat", ".ps1", ".json", ".txt"}
SOURCE_NAMES = {".gitignore"}


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def sqlite_backup(source: Path, destination: Path) -> dict:
    if not source.exists():
        return {"source": str(source), "copied": False, "reason": "missing"}
    source_conn = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    destination_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
        integrity = destination_conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        destination_conn.close()
        source_conn.close()
    return {
        "source": str(source),
        "destination": str(destination),
        "copied": True,
        "bytes": destination.stat().st_size,
        "integrity": integrity,
        "sha256": sha256_file(destination),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def directory_manifest(root: Path) -> dict:
    digest = hashlib.sha256()
    count = 0
    total_bytes = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        file_hash = sha256_file(path)
        digest.update(f"{relative}\0{size}\0{file_hash}\n".encode("utf-8"))
        count += 1
        total_bytes += size
    return {"files": count, "bytes": total_bytes, "sha256": digest.hexdigest()}


def copy_project_sources(destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in sorted(BASE_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.name in SOURCE_NAMES or path.suffix.lower() in SOURCE_SUFFIXES:
            if path.name in {"test_report.json", "vector_repair.pid"} or path.suffix.lower() == ".log":
                continue
            shutil.copy2(path, destination / path.name)
            copied.append(path.name)
    docs_source = BASE_DIR / "docs"
    if docs_source.exists():
        shutil.copytree(docs_source, destination / "docs")
        copied.append("docs/")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, help="Backup directory")
    args = parser.parse_args()

    config = load_config()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = args.destination or (BASE_DIR / "backups" / f"full_index_{timestamp}")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")

    pid_file = BASE_DIR / "vector_repair.pid"
    if pid_file.exists():
        try:
            repair_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            repair_pid = 0
        if repair_pid:
            try:
                import os
                os.kill(repair_pid, 0)
            except OSError:
                pass
            else:
                raise RuntimeError(f"vector repair is still running: PID {repair_pid}")

    destination.mkdir(parents=True)
    databases_dir = destination / "databases"
    databases_dir.mkdir()

    health = collect_health(exact_vectors=True, compute_version=True, deep_checks=True)
    if not health.get("healthy") or not health.get("version_matches"):
        raise RuntimeError(f"index health gate failed: {health.get('errors', [])}")

    db_results = []
    metadata_db = Path(config["paths"]["metadata_db"])
    cache_db = Path(config["paths"]["cache_db"])
    ocr_progress_db = Path(config["paths"]["ocr_texts"]) / "progress.db"
    for label, source in (
        ("metadata.db", metadata_db),
        ("cache.db", cache_db),
        ("ocr_progress.db", ocr_progress_db),
    ):
        db_results.append(sqlite_backup(source, databases_dir / label))

    vector_source = Path(config["paths"]["vector_db"])
    vector_destination = destination / "vectors.lancedb"
    shutil.copytree(vector_source, vector_destination, copy_function=shutil.copy2)
    vector_info = directory_manifest(vector_destination)

    source_files = copy_project_sources(destination / "project")
    manifest = {
        "created_at": datetime.now().isoformat(),
        "index_version": get_current_version(config),
        "health": health,
        "databases": db_results,
        "vectors": {
            "source": str(vector_source),
            "destination": str(vector_destination),
            **vector_info,
        },
        "project_files": source_files,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "backup": str(destination),
        "index_version": manifest["index_version"],
        "vector_files": vector_info["files"],
        "vector_bytes": vector_info["bytes"],
        "database_integrity": {
            Path(item["destination"]).name: item.get("integrity")
            for item in db_results if item.get("copied")
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())