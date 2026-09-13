#!/usr/bin/env python3
"""Research-oriented retrieval benchmark for the local MEGA² RAG index.

The benchmark is deliberately API-free. It compares page FTS, passage FTS,
and the current local hybrid pipeline before recommending any new embedding
work.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from glossary_loader import expand_with_glossary, load_glossary
from index_version import get_current_version
from passage_index import passage_status, search_passages
from query_analyzer import analyze_query, build_priority_terms
from rerank import rerank
from webui import (
    do_scoped_search,
    do_search,
    fts5_query,
    merge_scoped_candidates,
)

CONFIG = yaml.safe_load((SCRIPT_DIR / "config.yaml").read_text(encoding="utf-8"))
META_DB = Path(CONFIG["paths"]["metadata_db"])
DEFAULT_MODES = ("page_bm25", "passage_bm25", "hybrid_current")
MODE_ALIASES = {
    "page": "page_bm25",
    "page_bm25": "page_bm25",
    "passage": "passage_bm25",
    "passage_bm25": "passage_bm25",
    "hybrid": "hybrid_current",
    "hybrid_current": "hybrid_current",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{META_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_cases(path: str | Path) -> tuple[list[dict], dict]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = payload.get("defaults", {})
    cases = []
    for index, raw in enumerate(payload.get("queries", []), start=1):
        case = dict(defaults)
        case.update(raw or {})
        case["query"] = case.get("query") or case.get("zh") or ""
        case["id"] = case.get("id") or f"query_{index:02d}"
        case["expected_terms"] = list(
            case.get("expected_terms") or case.get("de_expected") or []
        )
        if not case.get("preferred_volumes"):
            abteilung = case.get("preferred_abteilung")
            band = case.get("preferred_band")
            case["preferred_volumes"] = (
                [{"abteilung": str(abteilung), "band": str(band) if band else None}]
                if abteilung else []
            )
        for volume in case.get("preferred_volumes", []):
            if volume.get("abteilung") is not None:
                volume["abteilung"] = str(volume["abteilung"])
            if volume.get("band") is not None:
                volume["band"] = str(volume["band"])
        case["pass_at"] = int(case.get("pass_at", 10))
        cases.append(case)
    return cases, payload


def _parse_modes(raw: str) -> list[str]:
    modes = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not key:
            continue
        if key not in MODE_ALIASES:
            raise ValueError(f"unknown retrieval mode: {item}")
        mode = MODE_ALIASES[key]
        if mode not in modes:
            modes.append(mode)
    return modes or list(DEFAULT_MODES)


def _chunk_layer_select(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "text_layer" not in columns:
        return "'unclassified' AS text_layer, 0 AS text_layer_confidence"
    return (
        "COALESCE(c.text_layer, 'unclassified') AS text_layer, "
        "COALESCE(c.text_layer_confidence, 0) AS text_layer_confidence"
    )


def search_page_bm25(query: str, route: str, limit: int) -> list[dict]:
    conn = _connect()
    try:
        layer_select = _chunk_layer_select(conn)
        route_filter = ""
        if route == "main_text":
            route_filter = "AND c.is_main_text=1"
        elif route == "apparat":
            route_filter = "AND c.is_editorial_comment=1"
        rows = conn.execute(
            f"""
            SELECT c.id, c.source_path, c.mega_abteilung, c.band,
                   c.source_type, c.page, c.is_main_text, c.chunk_text,
                   rank AS fts_rank,
                   COALESCE(c.source_collection, 'ocr') AS source_collection,
                   COALESCE(c.source_quality, 'ocr') AS source_quality,
                   COALESCE(c.page_kind, 'pdf') AS page_kind,
                   COALESCE(c.page_label, '') AS page_label,
                   COALESCE(c.source_url, '') AS source_url,
                   COALESCE(c.ocr_quality, 'medium') AS ocr_quality,
                   {layer_select}
            FROM chunks_fts f JOIN chunks c ON f.rowid=c.rowid
            WHERE chunks_fts MATCH ? {route_filter}
            ORDER BY rank LIMIT ?
            """,
            (fts5_query(query), limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "page_id": row["id"],
                "record_type": "page",
                "source_path": row["source_path"],
                "abteilung": row["mega_abteilung"],
                "band": row["band"],
                "type": row["source_type"],
                "page": row["page"],
                "is_main_text": bool(row["is_main_text"]),
                "text": str(row["chunk_text"] or ""),
                "rank": row["fts_rank"],
                "_bm25_rank": row["fts_rank"],
                "source_collection": row["source_collection"],
                "source_quality": row["source_quality"],
                "page_kind": row["page_kind"],
                "page_label": row["page_label"],
                "source_url": row["source_url"],
                "ocr_quality": row["ocr_quality"],
                "text_layer": row["text_layer"],
                "text_layer_confidence": row["text_layer_confidence"],
                "_retrieval_source": "page_fts",
                "_retrieval_sources": ["page_fts"],
            }
            for row in rows
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def search_passage_bm25(query: str, route: str, limit: int) -> list[dict]:
    conn = _connect()
    try:
        return search_passages(
            conn,
            fts5_query(query),
            route=route,
            limit=limit,
            max_per_page=2,
        )
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def prepare_query(case: dict, glossary: dict) -> dict:
    question = case["query"]
    expanded, matched, hints = expand_with_glossary(question, glossary)
    profile = analyze_query(question, expanded, matched, glossary)
    priority_terms = build_priority_terms(profile, glossary)
    return {
        "question": question,
        "expanded": expanded,
        "matched_terms": matched,
        "hints": hints,
        "query_profile": profile,
        "priority_terms": priority_terms,
    }


def search_hybrid(prepared: dict, route: str, limit: int) -> tuple[list[dict], dict]:
    profile = prepared["query_profile"]
    expanded = prepared["expanded"]
    search_limit = max(30, limit * 4 if profile.get("target_abteilung") else limit * 3)
    results = do_search(expanded, route, search_limit, use_passage=True)
    debug = {
        "scoped_injected_count": 0,
        "scoped_text_count": 0,
        "authoritative_injected_count": 0,
        "authoritative_text_count": 0,
    }
    if profile.get("target_abteilung") and profile.get("intent") == "author_argument":
        scoped_terms = [term for term in prepared["priority_terms"][:10] if len(term) > 2]
        scoped = do_scoped_search(
            scoped_terms,
            target_abt=profile["target_abteilung"],
            target_band=profile.get("target_band"),
            text_only=True,
            top_k=20,
        )
        debug["scoped_text_count"] = len(scoped)
        debug["scoped_injected_count"] = merge_scoped_candidates(results, scoped)
        authoritative = do_scoped_search(
            scoped_terms,
            target_abt=profile["target_abteilung"],
            target_band=profile.get("target_band"),
            text_only=True,
            top_k=12,
            source_collection="megadigital",
        )
        debug["authoritative_text_count"] = len(authoritative)
        debug["authoritative_injected_count"] = merge_scoped_candidates(
            results, authoritative
        )
    ranked = rerank(
        results,
        query=expanded,
        mode="balanced",
        method="rule",
        glossary_terms=prepared["matched_terms"],
        query_profile=profile,
    )
    if ranked:
        scope = ranked[0].get("_scope_debug", {})
        debug["scope_constrained_applied"] = bool(
            scope.get("scope_constrained_applied")
        )
        debug["scope_reason"] = scope.get("reason", "")
    return ranked[:limit], debug


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term.strip())
    if re.fullmatch(r"[\wÄÖÜäöüẞß]+", term.strip(), flags=re.UNICODE):
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE | re.UNICODE)
    return re.compile(escaped, re.IGNORECASE | re.UNICODE)


def matched_terms(text: Any, terms: list[str]) -> list[str]:
    value = str(text or "")
    return [term for term in terms if _term_pattern(term).search(value)]


def _matches_volume(result: dict, volumes: list[dict]) -> bool:
    if not volumes:
        return False
    abteilung = str(result.get("abteilung") or "")
    band = str(result.get("band") or "")
    for volume in volumes:
        if abteilung != str(volume.get("abteilung") or ""):
            continue
        expected_band = volume.get("band")
        if expected_band in (None, "") or band == str(expected_band):
            return True
    return False


def _is_relevant(result: dict, case: dict) -> bool:
    expected = case.get("expected_terms", [])
    term_ok = bool(matched_terms(result.get("text"), expected)) if expected else True
    volume_ok = _matches_volume(result, case.get("preferred_volumes", []))
    type_ok = result.get("type") == case.get("preferred_type")
    layer_ok = result.get("text_layer") in set(case.get("preferred_layers", []))
    if case.get("must_hit_terms", True) and not term_ok:
        return False
    if case.get("must_hit_volume") and not volume_ok:
        return False
    if case.get("must_hit_type") and not type_ok:
        return False
    if case.get("must_hit_layer") and not layer_ok:
        return False
    return True


def _expected_fts(terms: list[str]) -> str:
    expressions = []
    for term in terms:
        clean = str(term).strip().replace('"', '""')
        if clean:
            expressions.append(f'"{clean}"')
    return " OR ".join(expressions)


def corpus_coverage(case: dict) -> dict:
    expected = case.get("expected_terms", [])
    if not expected:
        return {"global_count": 0, "eligible_count": 0, "authoritative_count": 0}
    expression = _expected_fts(expected)
    conn = _connect()
    try:
        global_count = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (expression,)
        ).fetchone()[0]
        conditions = ["chunks_fts MATCH ?"]
        params: list[Any] = [expression]
        volumes = case.get("preferred_volumes", [])
        if case.get("must_hit_volume") and volumes:
            clauses = []
            for volume in volumes:
                clause = ["c.mega_abteilung=?"]
                params.append(str(volume.get("abteilung") or ""))
                if volume.get("band") not in (None, ""):
                    clause.append("c.band=?")
                    params.append(str(volume["band"]))
                clauses.append("(" + " AND ".join(clause) + ")")
            conditions.append("(" + " OR ".join(clauses) + ")")
        if case.get("must_hit_type") and case.get("preferred_type"):
            conditions.append("c.source_type=?")
            params.append(case["preferred_type"])
        if case.get("must_hit_layer") and case.get("preferred_layers"):
            placeholders = ",".join("?" for _ in case["preferred_layers"])
            conditions.append(f"c.text_layer IN ({placeholders})")
            params.extend(case["preferred_layers"])
        where = " AND ".join(conditions)
        eligible = conn.execute(
            f"""
            SELECT COUNT(*) FROM chunks_fts f JOIN chunks c ON f.rowid=c.rowid
            WHERE {where}
            """,
            params,
        ).fetchone()[0]
        authoritative = conn.execute(
            f"""
            SELECT COUNT(*) FROM chunks_fts f JOIN chunks c ON f.rowid=c.rowid
            WHERE {where} AND COALESCE(c.source_collection, 'ocr')='megadigital'
            """,
            params,
        ).fetchone()[0]
        return {
            "global_count": int(global_count),
            "eligible_count": int(eligible),
            "authoritative_count": int(authoritative),
        }
    except sqlite3.Error as exc:
        return {
            "global_count": 0,
            "eligible_count": 0,
            "authoritative_count": 0,
            "error": str(exc),
        }
    finally:
        conn.close()


def _result_page_key(result: dict) -> str:
    return str(result.get("page_id") or result.get("id") or "")


def _quality_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return {"high": 1.0, "medium": 0.66, "low": 0.33, "failed": 0.0}.get(
        str(value).lower(), 0.5
    )


def evaluate_results(results: list[dict], case: dict, top_k: int, coverage: dict) -> dict:
    expected = case.get("expected_terms", [])
    pass_at = min(int(case.get("pass_at", top_k)), top_k)

    def subset(k: int) -> list[dict]:
        return results[: min(k, len(results))]

    evidence_rank = next(
        (index for index, result in enumerate(results, start=1) if _is_relevant(result, case)),
        None,
    )
    term_ranks = [
        index
        for index, result in enumerate(results, start=1)
        if matched_terms(result.get("text"), expected)
    ]
    found_terms = []
    for term in expected:
        if any(matched_terms(result.get("text"), [term]) for result in subset(10)):
            found_terms.append(term)

    preferred_type = case.get("preferred_type")
    preferred_layers = set(case.get("preferred_layers", []))
    top = subset(top_k)
    page_keys = [key for key in (_result_page_key(r) for r in top) if key]
    duplicate_rate = (
        (len(page_keys) - len(set(page_keys))) / len(page_keys) if page_keys else 0.0
    )
    status = "PASS" if evidence_rank and evidence_rank <= pass_at else "FAIL_RETRIEVAL"
    if coverage.get("eligible_count", 0) == 0:
        status = "XFAIL_DATA"

    compact_results = []
    for rank, result in enumerate(top, start=1):
        compact_results.append(
            {
                "rank": rank,
                "record_type": result.get("record_type", "page"),
                "id": result.get("id"),
                "page_id": result.get("page_id") or result.get("id"),
                "abteilung": result.get("abteilung"),
                "band": result.get("band"),
                "type": result.get("type"),
                "page": result.get("page"),
                "text_layer": result.get("text_layer"),
                "source_collection": result.get("source_collection", "ocr"),
                "retrieval_sources": result.get("_retrieval_sources", []),
                "matched_expected_terms": matched_terms(result.get("text"), expected),
                "relevant": _is_relevant(result, case),
                "score": result.get("_score"),
                "final_score": result.get("final_score"),
                "chars": len(str(result.get("text") or "")),
            }
        )

    return {
        "status": status,
        "pass_at": pass_at,
        "results_count": len(results),
        "evidence_hit_at_5": bool(evidence_rank and evidence_rank <= 5),
        "evidence_hit_at_10": bool(evidence_rank and evidence_rank <= 10),
        "evidence_mrr": round(1.0 / evidence_rank, 4) if evidence_rank else 0.0,
        "first_evidence_rank": evidence_rank,
        "term_hit_at_5": bool(term_ranks and term_ranks[0] <= 5),
        "term_hit_at_10": bool(term_ranks and term_ranks[0] <= 10),
        "term_coverage_at_10": round(len(found_terms) / len(expected), 4) if expected else 1.0,
        "found_terms_at_10": found_terms,
        "volume_hit_at_5": any(
            _matches_volume(result, case.get("preferred_volumes", [])) for result in subset(5)
        ),
        "volume_hit_at_10": any(
            _matches_volume(result, case.get("preferred_volumes", [])) for result in subset(10)
        ),
        "preferred_type_rate": round(
            sum(1 for r in top if r.get("type") == preferred_type) / len(top), 4
        ) if top and preferred_type else None,
        "preferred_layer_rate": round(
            sum(1 for r in top if r.get("text_layer") in preferred_layers) / len(top), 4
        ) if top and preferred_layers else None,
        "authoritative_hit_at_10": any(
            r.get("source_collection") == "megadigital" for r in subset(10)
        ),
        "passage_rate": round(
            sum(1 for r in top if r.get("record_type") == "passage") / len(top), 4
        ) if top else 0.0,
        "duplicate_page_rate": round(duplicate_rate, 4),
        "average_chars": round(
            sum(len(str(r.get("text") or "")) for r in top) / len(top), 1
        ) if top else 0.0,
        "average_ocr_quality": round(
            sum(_quality_value(r.get("ocr_quality")) for r in top) / len(top), 3
        ) if top else 0.0,
        "top_results": compact_results,
    }


def _run_mode(mode: str, prepared: dict, case: dict, top_k: int) -> tuple[list[dict], dict]:
    route = case.get("route", "all")
    if mode == "page_bm25":
        return search_page_bm25(prepared["expanded"], route, top_k), {}
    if mode == "passage_bm25":
        return search_passage_bm25(prepared["expanded"], route, top_k), {}
    if mode == "hybrid_current":
        return search_hybrid(prepared, route, top_k)
    raise ValueError(mode)


def _mode_summary(records: list[dict], mode: str) -> dict:
    rows = [record["modes"][mode] for record in records if mode in record["modes"]]
    evaluable = [row for row in rows if row["status"] != "XFAIL_DATA"]
    passes = [row for row in evaluable if row["status"] == "PASS"]
    divisor = len(evaluable) or 1
    return {
        "queries": len(rows),
        "evaluable": len(evaluable),
        "xfail_data": sum(row["status"] == "XFAIL_DATA" for row in rows),
        "pass_count": len(passes),
        "fail_count": sum(row["status"] == "FAIL_RETRIEVAL" for row in rows),
        "pass_rate": round(len(passes) / divisor, 4),
        "evidence_hit_at_5": round(sum(row["evidence_hit_at_5"] for row in evaluable) / divisor, 4),
        "evidence_hit_at_10": round(sum(row["evidence_hit_at_10"] for row in evaluable) / divisor, 4),
        "mean_mrr": round(sum(row["evidence_mrr"] for row in evaluable) / divisor, 4),
        "mean_term_coverage_at_10": round(
            sum(row["term_coverage_at_10"] for row in evaluable) / divisor, 4
        ),
        "authoritative_hit_rate": round(
            sum(row["authoritative_hit_at_10"] for row in evaluable) / divisor, 4
        ),
        "mean_duplicate_page_rate": round(
            sum(row["duplicate_page_rate"] for row in evaluable) / divisor, 4
        ),
        "mean_candidate_chars": round(
            sum(row["average_chars"] for row in evaluable) / divisor, 1
        ),
        "mean_latency_seconds": round(
            sum(row["latency_seconds"] for row in evaluable) / divisor, 3
        ),
    }


def _digital_vector_assessment(records: list[dict]) -> dict:
    digital_cases = [r for r in records if r["coverage"].get("authoritative_count", 0) > 0]
    semantic_gap = []
    fusion_gap = []
    visibility_gap = []
    for record in digital_cases:
        hybrid = record["modes"].get("hybrid_current")
        if not hybrid:
            continue
        lexical_pass = any(
            record["modes"].get(mode, {}).get("status") == "PASS"
            for mode in ("page_bm25", "passage_bm25")
        )
        if hybrid["status"] == "FAIL_RETRIEVAL":
            (fusion_gap if lexical_pass else semantic_gap).append(record["id"])
        elif not hybrid["authoritative_hit_at_10"]:
            visibility_gap.append(record["id"])

    if len(semantic_gap) >= 2:
        recommendation = "run_selective_megadigital_embedding_pilot"
        reason = "Multiple digital-covered queries fail both lexical and hybrid retrieval."
    else:
        recommendation = "postpone_full_megadigital_embedding"
        reason = (
            "The benchmark does not isolate enough semantic-only failures to justify "
            "embedding all 11,647 digital records."
        )
    return {
        "recommendation": recommendation,
        "reason": reason,
        "digital_coverage_queries": len(digital_cases),
        "semantic_gap_candidates": semantic_gap,
        "fusion_or_rerank_gap_candidates": fusion_gap,
        "authoritative_visibility_gaps": visibility_gap,
    }


def _print_result(record: dict, modes: list[str], verbose: bool) -> None:
    coverage = record["coverage"]
    print(
        f"\n[{record['id']}] {record['query']}\n"
        f"  coverage: eligible={coverage.get('eligible_count', 0)} "
        f"digital={coverage.get('authoritative_count', 0)}"
    )
    if verbose:
        print(f"  profile: {json.dumps(record['query_profile'], ensure_ascii=False)}")
        print(f"  priority: {record['priority_terms'][:20]}")
    for mode in modes:
        row = record["modes"][mode]
        print(
            f"  {mode:16s} {row['status']:14s} "
            f"rank={str(row['first_evidence_rank']):>4s} "
            f"MRR={row['evidence_mrr']:.3f} chars={row['average_chars']:.0f} "
            f"time={row['latency_seconds']:.2f}s"
        )
        if verbose:
            for result in row["top_results"]:
                marker = "*" if result["relevant"] else " "
                print(
                    f"    {marker}{result['rank']:2d} {result['record_type']:7s} "
                    f"{result['abteilung']}/{result['band']} {result['type']} "
                    f"p.{result['page']} {result['source_collection']} "
                    f"terms={result['matched_expected_terms']}"
                )


def _write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# MEGA² RAG Research Retrieval Benchmark",
        "",
        f"- Timestamp: `{report['timestamp']}`",
        f"- Index version: `{report['index_version']}`",
        f"- Queries: {report['num_queries']}",
        f"- Top K: {report['top_k']}",
        "",
        "## Summary",
        "",
        "| Mode | Pass | XFAIL | Hit@5 | Hit@10 | MRR | Avg chars | Avg seconds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, summary in report["summary"].items():
        lines.append(
            f"| {mode} | {summary['pass_count']}/{summary['evaluable']} | "
            f"{summary['xfail_data']} | {summary['evidence_hit_at_5']:.1%} | "
            f"{summary['evidence_hit_at_10']:.1%} | {summary['mean_mrr']:.3f} | "
            f"{summary['mean_candidate_chars']:.0f} | {summary['mean_latency_seconds']:.2f} |"
        )
    lines.extend([
        "",
        "## Per-query status",
        "",
        "| ID | Query | " + " | ".join(report["modes"]) + " | Eligible evidence |",
        "|---|---|" + "---|" * len(report["modes"]) + "---:|",
    ])
    for record in report["per_query"]:
        statuses = " | ".join(record["modes"][mode]["status"] for mode in report["modes"])
        lines.append(
            f"| {record['id']} | {record['query']} | {statuses} | "
            f"{record['coverage'].get('eligible_count', 0)} |"
        )
    digital = report["megadigital_vector_assessment"]
    lines.extend([
        "",
        "## MEGAdigital vector decision",
        "",
        f"- Recommendation: `{digital['recommendation']}`",
        f"- Reason: {digital['reason']}",
        f"- Queries with authoritative digital coverage: {digital['digital_coverage_queries']}",
        f"- Semantic-gap candidates: {digital['semantic_gap_candidates']}",
        f"- Fusion/rerank-gap candidates: {digital['fusion_or_rerank_gap_candidates']}",
        f"- Visibility gaps: {digital['authoritative_visibility_gaps']}",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _baseline_regressions(report: dict, baseline_path: Path) -> list[str]:
    if not baseline_path.exists():
        return []
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    old = {
        record["id"]: record.get("modes", {}).get("hybrid_current", {}).get("status")
        for record in baseline.get("per_query", [])
    }
    current = {
        record["id"]: record.get("modes", {}).get("hybrid_current", {}).get("status")
        for record in report.get("per_query", [])
    }
    return sorted(
        query_id for query_id, status in old.items()
        if status == "PASS" and current.get(query_id) == "FAIL_RETRIEVAL"
    )


def _write_baseline(report: dict, path: Path) -> None:
    """Persist only stable pass/fail signals, not the 500 KB diagnostic payload."""
    payload = {
        "schema_version": report.get("schema_version", 2),
        "timestamp": report.get("timestamp"),
        "index_version": report.get("index_version"),
        "summary": report.get("summary", {}),
        "per_query": [
            {
                "id": record["id"],
                "modes": {
                    mode: {"status": values.get("status")}
                    for mode, values in record.get("modes", {}).items()
                },
            }
            for record in report.get("per_query", [])
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_eval(
    queries_yaml: str,
    top_k: int = 10,
    output_dir: str | None = None,
    modes: list[str] | None = None,
    verbose: bool = False,
) -> dict:
    cases, source_payload = _load_cases(queries_yaml)
    glossary = load_glossary()
    modes = modes or list(DEFAULT_MODES)
    records = []

    print(
        f"MEGA² research benchmark: {len(cases)} queries, "
        f"modes={','.join(modes)}, top_k={top_k}"
    )
    for case in cases:
        prepared = prepare_query(case, glossary)
        coverage = corpus_coverage(case)
        record = {
            "id": case["id"],
            "query": case["query"],
            "category": case.get("category", "general"),
            "notes": case.get("notes", ""),
            "expected_terms": case.get("expected_terms", []),
            "preferred_volumes": case.get("preferred_volumes", []),
            "preferred_type": case.get("preferred_type"),
            "expanded_query": prepared["expanded"],
            "matched_glossary_terms": prepared["matched_terms"],
            "query_profile": prepared["query_profile"],
            "priority_terms": prepared["priority_terms"],
            "coverage": coverage,
            "modes": {},
        }
        for mode in modes:
            started = time.perf_counter()
            error = None
            debug = {}
            try:
                results, debug = _run_mode(mode, prepared, case, top_k)
            except Exception as exc:
                results = []
                error = f"{type(exc).__name__}: {exc}"
            metrics = evaluate_results(results, case, top_k, coverage)
            metrics["latency_seconds"] = round(time.perf_counter() - started, 3)
            metrics["retrieval_debug"] = debug
            if error:
                metrics["error"] = error
            record["modes"][mode] = metrics
        records.append(record)
        _print_result(record, modes, verbose)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "schema_version": 2,
        "timestamp": timestamp,
        "index_version": get_current_version(),
        "query_set_version": source_payload.get("version", 1),
        "top_k": top_k,
        "modes": modes,
        "num_queries": len(cases),
        "passage_index": passage_status(),
        "summary": {mode: _mode_summary(records, mode) for mode in modes},
        "megadigital_vector_assessment": _digital_vector_assessment(records),
        "per_query": records,
    }

    output = Path(output_dir) if output_dir else SCRIPT_DIR / "eval_reports"
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"research_benchmark_{timestamp}.json"
    md_path = output / f"research_benchmark_{timestamp}.md"
    latest_json = output / "research_benchmark_latest.json"
    latest_md = output / "research_benchmark_latest.md"
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    json_path.write_text(serialized, encoding="utf-8")
    latest_json.write_text(serialized, encoding="utf-8")
    _write_markdown(report, md_path)
    _write_markdown(report, latest_md)

    print("\nSummary")
    for mode in modes:
        summary = report["summary"][mode]
        print(
            f"  {mode:16s} pass={summary['pass_count']}/{summary['evaluable']} "
            f"hit@5={summary['evidence_hit_at_5']:.1%} "
            f"MRR={summary['mean_mrr']:.3f} chars={summary['mean_candidate_chars']:.0f}"
        )
    digital = report["megadigital_vector_assessment"]
    print(f"  MEGAdigital vectors: {digital['recommendation']}")
    print(f"  report: {md_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", default=str(SCRIPT_DIR / "eval_queries.yaml"))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--modes",
        default=",".join(DEFAULT_MODES),
        help="Comma-separated: page_bm25, passage_bm25, hybrid_current",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail on hybrid retrieval failures")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--baseline", default=str(SCRIPT_DIR / "research_benchmark_baseline.json"))
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()

    try:
        modes = _parse_modes(args.modes)
    except ValueError as exc:
        parser.error(str(exc))
    report = run_eval(
        args.queries,
        top_k=args.top_k,
        output_dir=args.output_dir,
        modes=modes,
        verbose=args.verbose,
    )
    baseline = Path(args.baseline)
    regressions = _baseline_regressions(report, baseline)
    if args.write_baseline:
        _write_baseline(report, baseline)
        print(f"  baseline written: {baseline}")
    if regressions:
        print(f"  hybrid regressions: {', '.join(regressions)}")
    hybrid_failures = [
        record["id"] for record in report["per_query"]
        if record.get("modes", {}).get("hybrid_current", {}).get("status") == "FAIL_RETRIEVAL"
    ]
    if args.fail_on_regression and regressions:
        return 2
    if args.strict and hybrid_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
