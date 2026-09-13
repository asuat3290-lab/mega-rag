#!/usr/bin/env python3
"""Core-concept recall and sense-coverage helpers for MEGA retrieval."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List

from passage_index import search_passages


_LATIN_TERM_RE = re.compile(r"[A-Za-zÄÖÜäöüẞß]+(?:[-'][A-Za-zÄÖÜäöüẞß]+)*")


def _dedupe(values: Iterable[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def build_core_recall_terms(query_profile: Dict, priority_terms: List[str],
                            max_terms: int = 15) -> List[str]:
    """Return only concept-bearing terms, excluding work and author fallbacks."""
    candidates = list(query_profile.get("core_expansions", []))
    candidates.extend(query_profile.get("lexical_core", []))
    if not candidates and query_profile.get("core_terms"):
        candidates.extend(priority_terms)
    usable = []
    for value in _dedupe(candidates):
        if _LATIN_TERM_RE.search(value):
            usable.append(value)
        if len(usable) >= max_terms:
            break
    return usable


def _exact_fts_query(term: str) -> str:
    words = _LATIN_TERM_RE.findall(term)
    if not words:
        return ""
    phrase = " ".join(words).replace('"', '""')
    return f'"{phrase}"'


def _fallback_page_search(conn: sqlite3.Connection, term: str, route: str,
                          limit: int) -> List[Dict]:
    conditions = ["c.chunk_text LIKE ?"]
    params: List[object] = [f"%{term}%"]
    if route == "main_text":
        conditions.append("c.is_main_text=1")
    elif route == "apparat":
        conditions.append("c.is_editorial_comment=1")
    rows = conn.execute(
        f"""
        SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
               c.page, c.is_main_text, c.chunk_text,
               COALESCE(c.source_collection, 'ocr'),
               COALESCE(c.source_quality, 'ocr'),
               COALESCE(c.text_layer, 'unclassified'),
               COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''),
               COALESCE(c.source_url, ''), COALESCE(c.ocr_quality, 'medium')
        FROM chunks c
        WHERE {' AND '.join(conditions)}
        ORDER BY c.is_main_text DESC, c.char_count DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    ).fetchall()
    return [
        {
            "id": row[0], "record_type": "page", "source_path": row[1],
            "abteilung": row[2], "band": row[3], "type": row[4],
            "page": row[5], "is_main_text": bool(row[6]), "text": row[7],
            "source_collection": row[8], "source_quality": row[9],
            "text_layer": row[10], "page_kind": row[11],
            "page_label": row[12], "source_url": row[13],
            "ocr_quality": row[14], "rank": 99, "_bm25_rank": 99,
        }
        for row in rows
    ]


def search_core_variants(metadata_db: str | Path, query_profile: Dict,
                         priority_terms: List[str], route: str = "all",
                         top_k: int = 24) -> List[Dict]:
    """Recall a small round-robin bucket for each exact core expression."""
    terms = build_core_recall_terms(query_profile, priority_terms)
    if not terms:
        return []
    per_term = max(2, min(4, (top_k + len(terms) - 1) // len(terms) + 1))
    prefer_author_text = query_profile.get("intent") == "author_argument"
    fetch_limit = max(per_term * 4, 8) if prefer_author_text else per_term
    conn = sqlite3.connect(str(metadata_db))
    buckets: List[List[Dict]] = []
    try:
        for term in terms:
            fts_query = _exact_fts_query(term)
            rows: List[Dict] = []
            if fts_query:
                try:
                    rows = search_passages(
                        conn, fts_query, route=route, limit=fetch_limit,
                        max_per_page=1,
                    )
                except sqlite3.Error:
                    rows = []
            if not rows:
                rows = _fallback_page_search(conn, term, route, fetch_limit)
            if prefer_author_text:
                rows.sort(key=lambda row: (
                    str(row.get("text_layer")) != "author_text",
                    str(row.get("source_collection")) != "megadigital",
                ))
            rows = rows[:per_term]
            for row in rows:
                row["_retrieval_source"] = "core_variant"
                sources = row.setdefault("_retrieval_sources", [])
                if "core_variant" not in sources:
                    sources.append("core_variant")
                variants = row.setdefault("_matched_variants", [])
                if term not in variants:
                    variants.append(term)
            if rows:
                buckets.append(rows)
    finally:
        conn.close()

    output: List[Dict] = []
    seen = set()
    positions = [0] * len(buckets)
    while len(output) < top_k:
        progressed = False
        for index, bucket in enumerate(buckets):
            while positions[index] < len(bucket):
                item = bucket[positions[index]]
                positions[index] += 1
                record_id = item.get("id")
                if record_id in seen:
                    continue
                seen.add(record_id)
                output.append(item)
                progressed = True
                break
            if len(output) >= top_k:
                break
        if not progressed:
            break
    return output


def merge_core_candidates(results: List[Dict], candidates: List[Dict]) -> int:
    """Merge exact records while retaining provenance and matched variants."""
    by_id = {row.get("id"): row for row in results if row.get("id")}
    injected = 0
    for candidate in candidates:
        record_id = candidate.get("id")
        existing = by_id.get(record_id)
        if existing is not None:
            for source in candidate.get("_retrieval_sources", ["core_variant"]):
                sources = existing.setdefault("_retrieval_sources", [])
                if source not in sources:
                    sources.append(source)
            for variant in candidate.get("_matched_variants", []):
                variants = existing.setdefault("_matched_variants", [])
                if variant not in variants:
                    variants.append(variant)
            continue
        results.append(candidate)
        if record_id:
            by_id[record_id] = candidate
        injected += 1
    return injected


def annotate_concept_groups(results: List[Dict], query_profile: Dict) -> None:
    """Attach non-equivalent lexical sense labels to matching candidates."""
    groups = query_profile.get("concept_groups", [])
    if not groups:
        return
    for row in results:
        text = str(row.get("text") or "").casefold()
        matches = []
        for group in groups:
            hit_terms = [
                term for term in group.get("terms", [])
                if str(term).casefold() in text
            ]
            if hit_terms:
                matches.append({
                    "id": group.get("id", ""),
                    "label": group.get("label", group.get("id", "")),
                    "terms": hit_terms,
                })
        row["_matched_concept_groups"] = matches


def apply_concept_group_coverage(results: List[Dict], query_profile: Dict,
                                 max_promoted: int = 4) -> List[Dict]:
    """Promote one strong TEXT candidate per declared sense without filtering."""
    groups = query_profile.get("concept_groups", [])
    if len(groups) < 2 or query_profile.get("target_abteilung"):
        return results
    annotate_concept_groups(results, query_profile)
    before = {id(row): index + 1 for index, row in enumerate(results)}
    promoted: List[Dict] = []
    used = set()
    for group in groups[:max_promoted]:
        group_id = group.get("id", "")
        matching = [
            row for row in results
            if any(item.get("id") == group_id
                   for item in row.get("_matched_concept_groups", []))
            and id(row) not in used
        ]
        if not matching:
            continue
        group_terms = {
            str(term).casefold() for term in group.get("terms", [])
        }
        exact_recalled = [
            row for row in matching
            if any(
                str(variant).casefold() in group_terms
                for variant in row.get("_matched_variants", [])
            )
        ]
        if exact_recalled:
            matching = exact_recalled
        if query_profile.get("intent") == "author_argument":
            verified = [
                row for row in matching
                if str(row.get("text_layer")) == "author_text"
            ]
            if verified:
                matching = verified
            else:
                text_matches = [row for row in matching if row.get("is_main_text")]
                if text_matches:
                    matching = text_matches
        exclusive = [row for row in matching if len(row.get("_matched_concept_groups", [])) == 1]
        if exclusive:
            matching = exclusive
        chosen = matching[0]
        promoted.append(chosen)
        used.add(id(chosen))
    if len(promoted) < 2:
        return results
    reordered = promoted + [row for row in results if id(row) not in used]
    for index, row in enumerate(reordered):
        debug = row.setdefault("_debug", {})
        debug["concept_coverage_applied"] = True
        debug["concept_rank_before"] = before.get(id(row))
        debug["concept_rank_after"] = index + 1
    return reordered
