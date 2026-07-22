#!/usr/bin/env python3
"""Cheap exact-term coverage probes over the local MEGA metadata index."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
DEFAULT_DB = Path(CONFIG["paths"]["metadata_db"])


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    return sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)


def _fts_phrase(term: str) -> str:
    return '"' + str(term).strip().replace('"', '""') + '"'


def _scope_sql(target_volumes: list[dict] | None, alias: str = "c") -> tuple[str, list[str]]:
    clauses = []
    params: list[str] = []
    for volume in target_volumes or []:
        abteilung = volume.get("abteilung")
        band = volume.get("band")
        if not abteilung:
            continue
        if band:
            clauses.append(f"({alias}.mega_abteilung=? AND {alias}.band=?)")
            params.extend([str(abteilung), str(band)])
        else:
            clauses.append(f"{alias}.mega_abteilung=?")
            params.append(str(abteilung))
    return ((" AND (" + " OR ".join(clauses) + ")") if clauses else "", params)


def _centered_snippet(text: str, term: str, before: int = 120, after: int = 300) -> str:
    pos = text.casefold().find(term.casefold())
    if pos < 0:
        return text[: before + after].strip()
    return text[max(0, pos - before): min(len(text), pos + len(term) + after)].strip()


def probe_terms(terms: Iterable[str], *, target_volumes: list[dict] | None = None,
                route: str = "all", sample_limit: int = 2,
                db_path: str | Path = DEFAULT_DB) -> dict:
    """Count exact phrase hits by layer, volume and source without embeddings."""
    cleaned = []
    seen = set()
    for raw in terms:
        term = str(raw or "").strip()
        key = term.casefold()
        if len(term) > 1 and key not in seen:
            seen.add(key)
            cleaned.append(term)
    conn = _ro_connect(db_path)
    conn.row_factory = sqlite3.Row
    output = []
    try:
        for term in cleaned:
            filters, params = _scope_sql(target_volumes)
            if route == "main_text":
                filters += " AND c.source_type='TEXT'"
            elif route == "apparat":
                filters += " AND c.source_type='APPARAT'"
            query = _fts_phrase(term)
            rows = conn.execute(
                f"""
                SELECT c.mega_abteilung, c.band, c.source_type,
                       COALESCE(c.text_layer, 'unclassified') AS text_layer,
                       COALESCE(c.source_collection, 'ocr') AS source_collection,
                       COUNT(*) AS hits
                FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid
                WHERE chunks_fts MATCH ? {filters}
                GROUP BY c.mega_abteilung, c.band, c.source_type,
                         COALESCE(c.text_layer, 'unclassified'),
                         COALESCE(c.source_collection, 'ocr')
                ORDER BY hits DESC
                """,
                [query, *params],
            ).fetchall()
            sample_rows = conn.execute(
                f"""
                SELECT c.id, c.mega_abteilung, c.band, c.source_type, c.page,
                       c.source_file, COALESCE(c.page_label, '') AS page_label,
                       COALESCE(c.text_layer, 'unclassified') AS text_layer,
                       COALESCE(c.source_collection, 'ocr') AS source_collection,
                       c.chunk_text
                FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid
                WHERE chunks_fts MATCH ? {filters}
                ORDER BY CASE WHEN c.source_type='TEXT' THEN 0 ELSE 1 END,
                         CASE WHEN c.source_collection='megadigital' THEN 0 ELSE 1 END,
                         c.mega_abteilung, c.band, c.page
                LIMIT ?
                """,
                [query, *params, sample_limit],
            ).fetchall()
            total = sum(int(row["hits"]) for row in rows)
            text_hits = sum(int(row["hits"]) for row in rows if row["source_type"] == "TEXT")
            output.append({
                "term": term,
                "total_pages": total,
                "text_pages": text_hits,
                "apparat_pages": total - text_hits,
                "by_scope": [dict(row) for row in rows],
                "samples": [{
                    "id": row["id"], "abteilung": row["mega_abteilung"],
                    "band": row["band"], "text_type": row["source_type"],
                    "page": row["page"], "page_label": row["page_label"],
                    "source_file": row["source_file"], "text_layer": row["text_layer"],
                    "source_collection": row["source_collection"],
                    "snippet": _centered_snippet(row["chunk_text"], term),
                } for row in sample_rows],
            })
    finally:
        conn.close()
    return {
        "terms": output,
        "summary": {
            "probed_terms": len(output),
            "terms_with_hits": sum(1 for item in output if item["total_pages"]),
            "terms_with_text_hits": sum(1 for item in output if item["text_pages"]),
            "total_page_hits": sum(item["total_pages"] for item in output),
        },
        "target_volumes": target_volumes or [],
        "route": route,
    }


def probe_query_plan(plan: dict, *, db_path: str | Path = DEFAULT_DB,
                     sample_limit: int = 2) -> dict:
    terms = (
        list(plan.get("core_terms", []))
        + list(plan.get("historical_variants", []))
        + list(plan.get("related_non_equivalent", []))
        + list(plan.get("supporting_terms", []))
    )
    return probe_terms(
        terms,
        target_volumes=plan.get("target_volumes", []),
        route="main_text" if plan.get("routing_intent") == "author_argument" else "all",
        sample_limit=sample_limit,
        db_path=db_path,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="+")
    parser.add_argument("--route", choices=("all", "main_text", "apparat"), default="all")
    parser.add_argument("--volume", action="append", default=[])
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()
    volumes = []
    for value in args.volume:
        match = re.fullmatch(r"([IVX]+)(?:/([0-9]+(?:\.[0-9]+)?))?", value, re.I)
        if not match:
            parser.error(f"invalid volume: {value}")
        volumes.append({"label": value, "abteilung": match.group(1).upper(),
                        "band": match.group(2)})
    payload = probe_terms(args.terms, target_volumes=volumes, route=args.route,
                          sample_limit=args.samples)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
