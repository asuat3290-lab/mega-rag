#!/usr/bin/env python3
"""Cheap exact-term coverage probes over the local MEGA metadata index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
DEFAULT_DB = Path(CONFIG["paths"]["metadata_db"])


def _ro_connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    return sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)


def _fts_phrase(term: str) -> str:
    return '"' + str(term).strip().replace('"', '""') + '"'


MATCH_TYPE_ORDER = {
    "exact_phrase": 0,
    "lexical_variant": 1,
    "semantic_related": 2,
}
_HYPHENS = "-‐‑‒–—−"
_UNKNOWN_LANGUAGE = "unknown"


def _normalize_for_search(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact_with_spans(value: Any) -> tuple[str, list[int]]:
    """Make a search-only key while retaining offsets into the original text."""
    text = str(value or "")
    compact: list[str] = []
    spans: list[int] = []
    for index, char in enumerate(text):
        if char == "\u00ad" or char in _HYPHENS or char.isspace():
            continue
        normalized = unicodedata.normalize("NFKC", char).casefold()
        for piece in normalized:
            compact.append(piece)
            spans.append(index)
    return "".join(compact), spans


def _find_match_span(text: str, term: str) -> tuple[int, int] | None:
    needle, _ = _compact_with_spans(term)
    if not needle:
        return None
    haystack, spans = _compact_with_spans(text)
    position = haystack.find(needle)
    if position < 0 or position + len(needle) > len(spans):
        return None
    start = spans[position]
    end = spans[position + len(needle) - 1] + 1
    return start, end


def _centered_snippet(
    text: str,
    term: str,
    before: int = 120,
    after: int = 300,
) -> str:
    span = _find_match_span(text, term)
    if span is None:
        return str(text or "")[: before + after].strip()
    start, end = span
    return str(text or "")[max(0, start - before): min(len(text), end + after)].strip()


def _language(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else _UNKNOWN_LANGUAGE


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _normalize_for_search(text)
        if len(text) > 1 and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _term_entries(
    terms: Iterable[str],
    term_groups: dict[str, Iterable[str]] | None,
) -> list[dict[str, str]]:
    if not term_groups:
        return [
            {"term": term, "match_type": "exact_phrase"}
            for term in _dedupe(terms)
        ]
    entries: list[dict[str, str]] = []
    for match_type in ("exact_phrase", "lexical_variant", "semantic_related"):
        for term in _dedupe(term_groups.get(match_type, [])):
            entries.append({"term": term, "match_type": match_type})
    return entries


def _scope_sql(
    target_volumes: list[dict] | None,
    alias: str = "c",
    *,
    languages: Iterable[str] | None = None,
    works: Iterable[str] | None = None,
    versions: Iterable[str] | None = None,
    text_type: str | None = None,
    catalog_tables: set[str] | None = None,
) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    volume_clauses = []
    for volume in target_volumes or []:
        abteilung = volume.get("abteilung")
        band = volume.get("band")
        if not abteilung:
            continue
        if band:
            band = str(band)
            if "." in band:
                volume_clauses.append(f"({alias}.mega_abteilung=? AND {alias}.band=?)")
                params.extend([str(abteilung), band])
            else:
                volume_clauses.append(
                    f"({alias}.mega_abteilung=? AND ({alias}.band=? OR {alias}.band LIKE ?))"
                )
                params.extend([str(abteilung), band, band + ".%"])
        else:
            volume_clauses.append(f"{alias}.mega_abteilung=?")
            params.append(str(abteilung))
    if volume_clauses:
        clauses.append("(" + " OR ".join(volume_clauses) + ")")

    language_clauses = []
    for value in _dedupe(languages or []):
        if _normalize_for_search(value) == _UNKNOWN_LANGUAGE:
            language_clauses.append(
                f"(COALESCE({alias}.language, '')='' OR LOWER({alias}.language)='unknown')"
            )
        else:
            language_clauses.append(f"LOWER({alias}.language)=LOWER(?)")
            params.append(value)
    if language_clauses:
        clauses.append("(" + " OR ".join(language_clauses) + ")")

    if text_type:
        clauses.append(f"UPPER(COALESCE({alias}.source_type, ''))=UPPER(?)")
        params.append(text_type)

    def _catalog_exists(value: str, fields: list[str]) -> tuple[str, list[Any]]:
        pattern = f"%{value}%"
        direct = [
            f"LOWER(COALESCE({alias}.{field}, '')) LIKE LOWER(?)"
            for field in fields
        ]
        direct_params: list[Any] = [pattern] * len(fields)
        if catalog_tables and {
            "source_catalog_record_map", "source_catalog_documents"
        }.issubset(catalog_tables):
            direct.append(
                f"EXISTS (SELECT 1 FROM source_catalog_record_map cm "
                f"JOIN source_catalog_documents cd ON cd.source_id=cm.source_id "
                f"WHERE cm.record_id={alias}.id AND cd.active=1 AND ("
                "LOWER(COALESCE(cd.title, '')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(cd.source_doc, '')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(cd.edition_status, '')) LIKE LOWER(?) OR "
                "LOWER(COALESCE(cd.metadata_json, '')) LIKE LOWER(?)"
                "))"
            )
            direct_params.extend([pattern] * 4)
        return "(" + " OR ".join(direct) + ")", direct_params

    for value in _dedupe(works or []):
        sql, values = _catalog_exists(value, ["source_title", "source_doc", "source_file"])
        clauses.append(sql)
        params.extend(values)
    for value in _dedupe(versions or []):
        sql, values = _catalog_exists(value, ["source_title", "source_doc"])
        clauses.append(sql)
        params.extend(values)
    return (" AND ".join(clauses), params)


def _candidate_rows(
    connection: sqlite3.Connection,
    term: str,
    filter_sql: str,
    filter_params: list[Any],
    table_names: set[str],
) -> list[sqlite3.Row]:
    rows_by_id: dict[str, sqlite3.Row] = {}
    if "chunks_fts" in table_names:
        try:
            for row in connection.execute(
                f"""
                SELECT c.* FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid
                WHERE chunks_fts MATCH ? {('AND ' + filter_sql) if filter_sql else ''}
                ORDER BY c.mega_abteilung, c.band, c.source_type, c.page, c.id
                """,
                [_fts_phrase(term), *filter_params],
            ):
                rows_by_id[str(row["id"])] = row
        except sqlite3.Error:
            pass

    # FTS tokenization does not reliably cover Chinese or a line-break hyphen.
    # A narrow anchor fallback keeps this a probe, while _find_match_span remains
    # the final per-record exact/normalized check.
    normalized = _normalize_for_search(term)
    anchors = [str(term).strip()]
    if normalized and normalized not in anchors:
        anchors.append(normalized)
    compact_anchor = re.sub(r"[\s%s]" % re.escape(_HYPHENS), "", normalized)
    if len(compact_anchor) > 8:
        anchors.append(compact_anchor[:8])
    for anchor in _dedupe(anchors):
        try:
            for row in connection.execute(
                f"""
                SELECT c.* FROM chunks c
                WHERE {filter_sql or '1=1'} AND COALESCE(c.chunk_text, '') LIKE ?
                ORDER BY c.mega_abteilung, c.band, c.source_type, c.page, c.id
                """,
                [*filter_params, f"%{anchor}%"],
            ):
                rows_by_id[str(row["id"])] = row
        except sqlite3.Error:
            break
    return list(rows_by_id.values())


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _match_id(record_id: str, term: str, match_type: str, start: int) -> str:
    stable = f"{record_id}|{match_type}|{_normalize_for_search(term)}|{start}"
    return "tm_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _serialize_match(entry: dict[str, Any]) -> dict[str, Any]:
    record = entry["record"]
    text = str(record.get("chunk_text") or "")
    start, end = entry["span"]
    source_identity = record.get("_source_catalog") or {}
    groups = source_identity.get("groups", [])
    version_label = (
        record.get("source_title") or record.get("source_doc")
        or record.get("source_file") or _UNKNOWN_LANGUAGE
    )
    context_start = max(0, start - 220)
    context_end = min(len(text), end + 520)
    context = text[context_start:context_end]
    return {
        "match_id": _match_id(
            str(record.get("id") or ""), entry["term"], entry["match_type"], start
        ),
        "record_id": record.get("id"),
        "source_record_id": record.get("source_record_id") or record.get("id"),
        "term": entry["term"],
        "original_term": entry["term"],
        "normalized_term": _normalize_for_search(entry["term"]),
        "matched_form": text[start:end],
        "match_type": entry["match_type"],
        "match_basis": "normalized_lexical_text_in_one_indexed_record",
        "language": _language(record.get("language")),
        "source_id": source_identity.get("source_id"),
        "version": {
            "label": version_label,
            "edition_status": source_identity.get("edition_status"),
            "source_doc": record.get("source_doc"),
            "source_part": record.get("source_part"),
            "groups": groups,
        },
        "locator": {
            "abteilung": record.get("mega_abteilung"),
            "band": record.get("band"),
            "text_type": record.get("source_type"),
            "page": record.get("page"),
            "page_label": record.get("page_label"),
            "passage_no": record.get("passage_no"),
            "source_file": record.get("source_file"),
            "source_doc": record.get("source_doc"),
            "source_part": record.get("source_part"),
        },
        "context": context,
        "snippet": context,
        "context_start": context_start,
        "context_end": context_end,
        "context_truncated": context_start > 0 or context_end < len(text),
    }


def _source_group_key(match: dict[str, Any]) -> str:
    if match.get("source_id"):
        return str(match["source_id"])
    locator = match.get("locator", {})
    return "|".join(
        str(locator.get(key) or "")
        for key in ("source_file", "source_doc", "source_part", "abteilung", "band")
    )


def _probe_entry_groups(plan: dict) -> dict[str, list[str]]:
    return {
        "exact_phrase": list(plan.get("focus_terms") or plan.get("core_terms", [])),
        "lexical_variant": list(plan.get("historical_variants", [])),
        "semantic_related": list(plan.get("related_non_equivalent", []))
        + list(plan.get("supporting_terms", [])),
    }


def probe_terms(
    terms: Iterable[str],
    *,
    target_volumes: list[dict] | None = None,
    route: str = "all",
    sample_limit: int = 2,
    db_path: str | Path = DEFAULT_DB,
    term_groups: dict[str, Iterable[str]] | None = None,
    language: str | Iterable[str] | None = None,
    work: str | Iterable[str] | None = None,
    version: str | Iterable[str] | None = None,
    text_type: str | None = None,
    page: int = 1,
    page_size: int = 25,
    collect_matches: bool = True,
) -> dict:
    """Probe indexed records while keeping exact, lexical, and related hits apart."""
    groups = term_groups or {}
    entries = _term_entries(terms, groups or None)
    conn = _ro_connect(db_path)
    conn.row_factory = sqlite3.Row
    table_names = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    output = []
    all_entries: list[dict[str, Any]] = []
    unique_records: set[str] = set()
    unique_text_records: set[str] = set()
    unique_apparat_records: set[str] = set()
    try:
        for entry in entries:
            term = entry["term"]
            filter_sql, filter_params = _scope_sql(
                target_volumes,
                languages=[language] if isinstance(language, str) else language,
                works=[work] if isinstance(work, str) else work,
                versions=[version] if isinstance(version, str) else version,
                text_type=text_type,
                catalog_tables=table_names,
            )
            route_sql = filter_sql
            route_params = list(filter_params)
            if route == "main_text":
                route_sql = (route_sql + " AND " if route_sql else "") + "c.source_type='TEXT'"
            elif route == "apparat":
                route_sql = (route_sql + " AND " if route_sql else "") + "c.source_type='APPARAT'"
            rows = _candidate_rows(
                conn, term, route_sql, route_params, table_names
            )
            scope_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
            matched: list[dict[str, Any]] = []
            matched_forms: list[str] = []
            matched_ids: set[str] = set()
            for row in rows:
                record = _row_dict(row)
                text = str(record.get("chunk_text") or "")
                span = _find_match_span(text, term)
                if span is None:
                    continue
                record_id = str(record.get("id") or "")
                if not record_id or record_id in matched_ids:
                    continue
                matched_ids.add(record_id)
                source_type = str(record.get("source_type") or _UNKNOWN_LANGUAGE)
                scope_key = (
                    str(record.get("mega_abteilung") or _UNKNOWN_LANGUAGE),
                    str(record.get("band") or _UNKNOWN_LANGUAGE),
                    source_type,
                    str(record.get("text_layer") or "unclassified"),
                    str(record.get("source_collection") or "ocr"),
                )
                scope_counts[scope_key] += 1
                unique_records.add(record_id)
                if source_type == "TEXT":
                    unique_text_records.add(record_id)
                elif source_type == "APPARAT":
                    unique_apparat_records.add(record_id)
                matched_form = text[span[0]:span[1]]
                if matched_form not in matched_forms:
                    matched_forms.append(matched_form)
                item = {
                    "term": term,
                    "match_type": entry["match_type"],
                    "record": record,
                    "span": span,
                }
                if collect_matches:
                    matched.append(item)
                    all_entries.append(item)
                elif len(matched) < max(0, int(sample_limit)):
                    matched.append(item)

            total = len(matched_ids)
            text_hits = sum(
                count for key, count in scope_counts.items() if key[2] == "TEXT"
            )
            samples = []
            for item in matched[: max(0, int(sample_limit))]:
                item_record = item["record"]
                match = _serialize_match(item)
                samples.append({
                    "id": item_record.get("id"),
                    "abteilung": item_record.get("mega_abteilung"),
                    "band": item_record.get("band"),
                    "text_type": item_record.get("source_type"),
                    "page": item_record.get("page"),
                    "page_label": item_record.get("page_label"),
                    "source_file": item_record.get("source_file"),
                    "text_layer": item_record.get("text_layer") or "unclassified",
                    "source_collection": item_record.get("source_collection") or "ocr",
                    "language": _language(item_record.get("language")),
                    "matched_form": match["matched_form"],
                    "match_type": match["match_type"],
                    "snippet": match["snippet"],
                })
            output.append({
                "term": term,
                "original_term": term,
                "normalized_term": _normalize_for_search(term),
                "match_type": entry["match_type"],
                "total_pages": total,
                "text_pages": text_hits,
                "apparat_pages": total - text_hits,
                "by_scope": [
                    {
                        "mega_abteilung": key[0], "band": key[1],
                        "source_type": key[2], "text_layer": key[3],
                        "source_collection": key[4], "hits": count,
                    }
                    for key, count in sorted(scope_counts.items(), key=lambda item: (-item[1], item[0]))
                ],
                "matched_forms": matched_forms,
                "samples": samples,
                "result_status": "hit" if total else "no_match_in_indexed_scope",
                "absence_claim": None,
            })
    finally:
        conn.close()

    serialized_matches: list[dict[str, Any]] = []
    if collect_matches and all_entries:
        try:
            from source_catalog import hydrate_records_with_source_catalog
            records = []
            seen_record_ids: set[str] = set()
            for entry in all_entries:
                record = entry["record"]
                record_id = str(record.get("id") or "")
                if record_id in seen_record_ids:
                    continue
                seen_record_ids.add(record_id)
                records.append({"id": record_id, "page_id": record_id})
            by_id = {str(item["id"]): item for item in records}
            # Hydration mutates the small record objects; copy the identity back
            # to the raw rows used by each match without changing source text.
            hydrate_records_with_source_catalog(records, db_path)
            identities = {
                record_id: item.get("_source_catalog")
                for record_id, item in by_id.items()
                if item.get("_source_catalog")
            }
            for entry in all_entries:
                identity = identities.get(str(entry["record"].get("id") or ""))
                if identity:
                    entry["record"]["_source_catalog"] = identity
                serialized_matches.append(_serialize_match(entry))
        except Exception:
            serialized_matches = [_serialize_match(entry) for entry in all_entries]

    serialized_matches.sort(
        key=lambda item: (
            MATCH_TYPE_ORDER.get(item.get("match_type", ""), 99),
            str(item.get("locator", {}).get("abteilung") or ""),
            str(item.get("locator", {}).get("band") or ""),
            str(item.get("locator", {}).get("text_type") or ""),
            int(item.get("locator", {}).get("page") or 0),
            str(item.get("record_id") or ""),
            str(item.get("term") or ""),
        )
    )
    bounded_page = max(1, int(page))
    bounded_page_size = max(1, min(int(page_size), 200))
    total_matches = len(serialized_matches)
    offset = (bounded_page - 1) * bounded_page_size
    page_matches = serialized_matches[offset: offset + bounded_page_size]
    source_groups: dict[str, dict[str, Any]] = {}
    for match in serialized_matches:
        key = _source_group_key(match)
        group = source_groups.setdefault(key, {
            "source_id": match.get("source_id"),
            "label": match.get("version", {}).get("label"),
            "match_count": 0,
            "record_ids": [],
            "match_types": [],
            "versions": [],
        })
        group["match_count"] += 1
        if match.get("record_id") not in group["record_ids"]:
            group["record_ids"].append(match.get("record_id"))
        if match.get("match_type") not in group["match_types"]:
            group["match_types"].append(match.get("match_type"))
        version = match.get("version", {})
        version_key = (
            version.get("source_doc"), version.get("source_part"),
            version.get("edition_status"),
        )
        if version_key not in [
            (item.get("source_doc"), item.get("source_part"), item.get("edition_status"))
            for item in group["versions"]
        ]:
            group["versions"].append({
                "label": version.get("label"),
                "source_doc": version.get("source_doc"),
                "source_part": version.get("source_part"),
                "edition_status": version.get("edition_status"),
                "groups": version.get("groups", []),
            })

    summed_term_hits = sum(item["total_pages"] for item in output)
    unique_hits = len(unique_records)
    result = {
        "terms": output,
        "matches": page_matches,
        "source_groups": list(source_groups.values()),
        "summary": {
            "probed_terms": len(output),
            "terms_with_hits": sum(1 for item in output if item["total_pages"]),
            "terms_with_text_hits": sum(1 for item in output if item["text_pages"]),
            "total_page_hits": unique_hits,
            "unique_page_hits": unique_hits,
            "unique_text_page_hits": len(unique_text_records),
            "unique_apparat_page_hits": len(unique_apparat_records),
            "summed_term_page_hits": summed_term_hits,
            "overlap_page_hits": max(0, summed_term_hits - unique_hits),
            "count_unit": "indexed_page_record",
            "historical_work_count": None,
        },
        "target_volumes": target_volumes or [],
        "route": route,
        "filters": {
            "language": [language] if isinstance(language, str) else list(language or []),
            "work": [work] if isinstance(work, str) else list(work or []),
            "version": [version] if isinstance(version, str) else list(version or []),
            "text_type": text_type,
        },
        "pagination": {
            "available": bool(collect_matches),
            "page": bounded_page,
            "page_size": bounded_page_size,
            "offset": offset,
            "total_matches": total_matches if collect_matches else None,
            "returned_matches": len(page_matches),
            "has_previous": bounded_page > 1 and bool(collect_matches),
            "has_next": offset + len(page_matches) < total_matches if collect_matches else False,
            "total_pages": (
                (total_matches + bounded_page_size - 1) // bounded_page_size
                if collect_matches else None
            ),
        },
        "coverage_limitations": [
            "Counts are indexed page/chunk records, not historical works or complete editions.",
            "No match is reported only as no_match_in_indexed_scope; it is not a corpus-absence claim.",
            "Blank language metadata remains unknown; mixed source units are not collapsed to one language.",
            "Matching is normalized for search only; returned matched_form and context preserve source text.",
            "Cross-record or cross-page matches are not joined; each match is within one indexed record.",
            "semantic_related is a configured related-term probe, not a model-generated equivalence claim.",
        ],
    }
    if collect_matches and total_matches > len(page_matches):
        result["coverage_limitations"].append(
            "Pagination limits the returned match detail; use the next page without changing filters."
        )
    return result


def probe_query_plan(
    plan: dict,
    *,
    db_path: str | Path = DEFAULT_DB,
    sample_limit: int = 2,
    page: int = 1,
    page_size: int = 25,
    language: str | Iterable[str] | None = None,
    work: str | Iterable[str] | None = None,
    version: str | Iterable[str] | None = None,
    text_type: str | None = None,
    collect_matches: bool = True,
) -> dict:
    term_groups = _probe_entry_groups(plan)
    return probe_terms(
        [term for values in term_groups.values() for term in values],
        term_groups=term_groups,
        target_volumes=plan.get("target_volumes", []),
        route="main_text" if plan.get("routing_intent") == "author_argument" else "all",
        sample_limit=sample_limit,
        page=page,
        page_size=page_size,
        language=language,
        work=work if work is not None else plan.get("target_works", []),
        version=version,
        text_type=text_type,
        collect_matches=collect_matches,
        db_path=db_path,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="+")
    parser.add_argument("--route", choices=("all", "main_text", "apparat"), default="all")
    parser.add_argument("--volume", action="append", default=[])
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--work", action="append", default=[])
    parser.add_argument("--version", action="append", default=[])
    parser.add_argument("--text-type")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--export")
    args = parser.parse_args()
    volumes = []
    for value in args.volume:
        match = re.fullmatch(r"([IVX]+)(?:/([0-9]+(?:\.[0-9]+)?))?", value, re.I)
        if not match:
            parser.error(f"invalid volume: {value}")
        volumes.append({"label": value, "abteilung": match.group(1).upper(),
                        "band": match.group(2)})
    payload = probe_terms(
        args.terms,
        target_volumes=volumes,
        route=args.route,
        sample_limit=args.samples,
        language=args.language,
        work=args.work,
        version=args.version,
        text_type=args.text_type,
        page=args.page,
        page_size=args.page_size,
    )
    if args.export:
        output_path = Path(args.export).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload["artifact_paths"] = {"json": str(output_path)}
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
