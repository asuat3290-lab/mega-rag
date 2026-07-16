#!/usr/bin/env python3
"""Regression checks for passage-v2 splitting, storage, and retrieval."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from passage_index import passage_status, search_passages, split_passages

BASE_DIR = Path(__file__).resolve().parent


def check_splitter(failures: list[str]) -> None:
    paragraphs = []
    for paragraph_no in range(8):
        words = [f"p{paragraph_no}_word{index}" for index in range(125)]
        paragraphs.append(" ".join(words))
    text = "\n\n".join(paragraphs)
    parts = split_passages(
        text,
        target_tokens=180,
        overlap_tokens=30,
        min_tokens=80,
        max_tokens=240,
    )
    if len(parts) < 4:
        failures.append(f"splitter returned too few passages: {len(parts)}")
    for index, part in enumerate(parts):
        if text[part["char_start"]:part["char_end"]] != part["text"]:
            failures.append(f"passage {index} offsets do not reproduce source text")
        if not 1 <= part["token_count"] <= 240:
            failures.append(f"passage {index} token_count={part['token_count']}")
    for previous, current in zip(parts, parts[1:]):
        if current["char_start"] >= previous["char_end"]:
            failures.append("expected token overlap between adjacent passages")


def check_live_index(failures: list[str]) -> dict:
    status = passage_status()
    if not status.get("available"):
        failures.append(f"passage index unavailable: {status}")
        return status
    if status.get("dirty"):
        failures.append("passage FTS dirty flag is set")
    if not status.get("complete"):
        failures.append("passage index is not marked complete")
    if status.get("passages") != status.get("fts"):
        failures.append("passage table and FTS counts differ")
    if status.get("orphaned"):
        failures.append(f"orphaned passages: {status['orphaned']}")
    if status.get("pages") != status.get("eligible_pages"):
        failures.append(
            f"page coverage {status.get('pages')}/{status.get('eligible_pages')}"
        )

    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    conn = sqlite3.connect(config["paths"]["metadata_db"])
    try:
        samples = conn.execute(
            """
            SELECT p.passage_id, p.text, p.char_start, p.char_end, p.token_count,
                   c.chunk_text
            FROM passages p JOIN chunks c ON c.id=p.page_id
            WHERE p.chunk_config=(
                SELECT value FROM index_state WHERE key='passage_config'
            )
            ORDER BY p.rowid
            LIMIT 500
            """
        ).fetchall()
        if not samples:
            failures.append("no current-config passage samples")
        for passage_id, text, start, end, token_count, page_text in samples:
            if page_text[start:end] != text:
                failures.append(f"stored offset mismatch: {passage_id}")
                break
            if token_count <= 0:
                failures.append(f"invalid token count: {passage_id}")
                break

        results = search_passages(conn, "Subsumtion AND Rechtsphilosophie", route="main_text", limit=10)
        if not results:
            failures.append("passage FTS did not return Subsumtion")
        elif not any(
            result.get("abteilung") == "I"
            and result.get("band") == "2"
            and result.get("record_type") == "passage"
            and "subsumtion" in result.get("text", "").lower()
            for result in results
        ):
            failures.append("Subsumtion passage did not include expected I/2 evidence")
    finally:
        conn.close()
    return status


def main() -> int:
    failures: list[str] = []
    check_splitter(failures)
    status = check_live_index(failures)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if failures:
        print("FAIL")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: passage-v2 splitter, coverage, offsets, and FTS retrieval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
