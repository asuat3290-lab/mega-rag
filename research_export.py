#!/usr/bin/env python3
"""Export local MEGA² retrieval evidence as Markdown and JSON research packages."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((SCRIPT_DIR / "config.yaml").read_text(encoding="utf-8"))
META_DB = Path(CONFIG["paths"]["metadata_db"])
EXPORT_SCHEMA_VERSION = "mega-research-package-v1"

from concept_retrieval import (
    annotate_concept_groups,
    apply_concept_group_coverage,
    merge_core_candidates,
    search_core_variants,
)
from glossary_loader import expand_with_glossary, load_glossary
from index_version import get_current_version
from query_analyzer import analyze_query, build_priority_terms
from query_plan import build_query_plan
from rerank import rerank
from retrieval_quality import apply_candidate_qualification, assess_retrieval_adequacy
from sachregister import resolve_register_targets, search_sachregister
from snippet_extractor import (
    build_snippet_for_display,
    format_source_label,
    is_verified_author_text,
    text_layer_label,
)
from webui import do_scoped_search, do_search, merge_scoped_candidates
from term_probe import probe_query_plan


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _default_output_dir() -> Path:
    configured = CONFIG.get("paths", {}).get("research_exports")
    return Path(configured) if configured else SCRIPT_DIR / "research_exports"


def _query_hash(question: str) -> str:
    normalized = " ".join(question.strip().split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _dedupe_terms(values: list[str]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if len(text) > 1 and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _prepare_query(question: str, glossary: dict, intent_override: str = None,
                   planner_mode: str = "local",
                   plan_refinement: dict | None = None) -> dict:
    if planner_mode not in {"local", "hybrid", "agent_supplied"}:
        raise ValueError(f"unsupported planner_mode: {planner_mode}")
    if planner_mode == "agent_supplied" and not plan_refinement:
        raise ValueError("agent_supplied planner mode requires plan_refinement")
    local_plan = build_query_plan(
        question, glossary=glossary, intent_override=intent_override
    )
    planner_diagnostics = {
        "mode": planner_mode, "model": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "fallback": False,
    }
    if planner_mode == "hybrid":
        from query_plan_model import build_hybrid_plan
        plan, planner_diagnostics = build_hybrid_plan(
            question, local_plan=local_plan
        )
    elif planner_mode == "agent_supplied":
        plan = build_query_plan(
            question, glossary=glossary, intent_override=intent_override,
            mode=planner_mode, refinement=plan_refinement,
        )
    else:
        plan = local_plan
    expanded, matched_terms, hints = expand_with_glossary(question, glossary)
    profile = copy.deepcopy(plan["base_query_profile"])
    profile["intent"] = plan["routing_intent"]
    profile["core_expansions"] = _dedupe_terms(
        profile.get("core_expansions", [])
        + plan["core_terms"] + plan["historical_variants"]
    )
    profile["lexical_core"] = _dedupe_terms(
        profile.get("lexical_core", []) + plan["core_terms"]
    )
    planned_query_terms = _dedupe_terms(
        plan["core_terms"] + plan["historical_variants"] + plan["supporting_terms"]
    )
    if planned_query_terms:
        expanded = question + " " + " ".join(planned_query_terms)
    legacy_priority = build_priority_terms(profile, glossary)
    priority_terms = _dedupe_terms(
        plan["core_terms"] + plan["historical_variants"]
        + plan["supporting_terms"] + plan["related_non_equivalent"]
        + legacy_priority
    )
    return {
        "question": question,
        "expanded_query": expanded,
        "matched_glossary_terms": matched_terms,
        "glossary_hints": hints,
        "query_profile": profile,
        "priority_terms": priority_terms,
        "query_plan": plan,
        "planner_diagnostics": planner_diagnostics,
    }


def _retag_candidates(candidates: list[dict], source: str) -> None:
    for candidate in candidates:
        candidate["_retrieval_source"] = source
        sources = candidate.setdefault("_retrieval_sources", [])
        if source not in sources:
            sources.append(source)


def _inject_planned_branches(results: list[dict], plan: dict, profile: dict,
                             route: str, top_k: int) -> dict:
    debug = {
        "planned_scope_candidates": 0,
        "planned_scope_injected": 0,
        "related_candidates": 0,
        "related_injected": 0,
        "structural_candidates": 0,
        "structural_injected": 0,
    }
    exact_terms = _dedupe_terms(
        plan.get("core_terms", []) + plan.get("historical_variants", [])
    )
    text_route = "main_text" if plan.get("routing_intent") == "author_argument" else route

    related = plan.get("related_non_equivalent", [])
    if related:
        related_profile = {
            "core_expansions": related,
            "lexical_core": [],
            "core_terms": related,
            "intent": profile.get("intent"),
        }
        candidates = search_core_variants(
            META_DB, related_profile, related, route="all", top_k=max(8, top_k)
        )
        _retag_candidates(candidates, "planned_related_non_equivalent")
        debug["related_candidates"] = len(candidates)
        debug["related_injected"] = merge_core_candidates(results, candidates)

    supporting = plan.get("supporting_terms", [])
    if supporting:
        structural_profile = {
            "core_expansions": supporting,
            "lexical_core": [],
            "core_terms": supporting,
            "intent": profile.get("intent"),
        }
        candidates = search_core_variants(
            META_DB, structural_profile, supporting,
            route=text_route, top_k=max(10, top_k)
        )
        _retag_candidates(candidates, "planned_structural")
        debug["structural_candidates"] = len(candidates)
        debug["structural_injected"] = merge_core_candidates(results, candidates)

    if exact_terms:
        for volume in plan.get("target_volumes", [])[:12]:
            if not volume.get("abteilung"):
                continue
            candidates = do_scoped_search(
                exact_terms,
                target_abt=volume["abteilung"],
                target_band=volume.get("band"),
                text_only=plan.get("routing_intent") == "author_argument",
                top_k=max(6, min(12, top_k)),
            )
            _retag_candidates(candidates, "planned_target_scope")
            debug["planned_scope_candidates"] += len(candidates)
            debug["planned_scope_injected"] += merge_scoped_candidates(results, candidates)
    return debug


def retrieve_research_evidence(
    question: str,
    route: str = "all",
    top_k: int = 12,
    retrieval_mode: str = "balanced",
    rerank_method: str = "rule",
    intent_override: str = None,
    planner_mode: str = "local",
    plan_refinement: dict | None = None,
) -> dict:
    """Run the local retrieval pipeline without calling Flash or Pro."""
    if not str(question or "").strip():
        raise ValueError("question must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")

    glossary = load_glossary()
    prepared = _prepare_query(
        question.strip(), glossary, intent_override=intent_override,
        planner_mode=planner_mode, plan_refinement=plan_refinement,
    )
    profile = prepared["query_profile"]
    plan = prepared["query_plan"]
    search_top_k = top_k * 3 if profile.get("target_abteilung") else top_k
    results = do_search(
        prepared["expanded_query"],
        route,
        max(search_top_k, 30),
        use_passage=True,
    )
    variant_route = route
    if route == "all" and profile.get("intent") == "author_argument":
        variant_route = "main_text"
    variant_candidates = search_core_variants(
        META_DB,
        profile,
        prepared["priority_terms"],
        route=variant_route,
        top_k=max(16, min(30, top_k * 2)),
    )
    variant_injected = merge_core_candidates(results, variant_candidates)
    annotate_concept_groups(results, profile)

    retrieval_debug = {
        "initial_candidates": len(results),
        "variant_candidates": len(variant_candidates),
        "variant_injected_count": variant_injected,
        "scoped_text_count": 0,
        "scoped_injected_count": 0,
        "authoritative_text_count": 0,
        "authoritative_injected_count": 0,
        "planner_version": plan["planner_version"],
        "matched_rule_ids": plan["matched_rule_ids"],
        "planner_diagnostics": prepared.get("planner_diagnostics", {}),
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
        retrieval_debug["scoped_text_count"] = len(scoped)
        retrieval_debug["scoped_injected_count"] = merge_scoped_candidates(
            results, scoped
        )
        authoritative = do_scoped_search(
            scoped_terms,
            target_abt=profile["target_abteilung"],
            target_band=profile.get("target_band"),
            text_only=True,
            top_k=12,
            source_collection="megadigital",
        )
        retrieval_debug["authoritative_text_count"] = len(authoritative)
        retrieval_debug["authoritative_injected_count"] = merge_scoped_candidates(
            results, authoritative
        )

    retrieval_debug.update(
        _inject_planned_branches(results, plan, profile, route, top_k)
    )
    register_terms = _dedupe_terms(
        plan.get("core_terms", []) + plan.get("historical_variants", [])
        + plan.get("related_non_equivalent", [])
    )[:15]
    try:
        register_payload = search_sachregister(
            register_terms, top_k=12,
            target_volumes=plan.get("target_volumes", []),
        )
    except Exception as exc:
        register_payload = {"hits": [], "error": f"{type(exc).__name__}: {exc}"}
    try:
        register_candidates = resolve_register_targets(
            register_payload.get("hits", []), metadata_db=META_DB,
            text_only=plan.get("routing_intent") == "author_argument",
            top_k=max(12, top_k * 2),
        )
        _retag_candidates(register_candidates, "sachregister_resolved")
        retrieval_debug["register_resolved_candidates"] = len(register_candidates)
        retrieval_debug["register_resolved_injected"] = merge_scoped_candidates(
            results, register_candidates
        )
    except Exception as exc:
        retrieval_debug["register_resolved_candidates"] = 0
        retrieval_debug["register_resolved_injected"] = 0
        retrieval_debug["register_resolution_error"] = f"{type(exc).__name__}: {exc}"
    try:
        term_probe_payload = probe_query_plan(plan, sample_limit=0)
    except Exception as exc:
        term_probe_payload = {
            "terms": [], "summary": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
    retrieval_debug["register_navigation_hits"] = len(register_payload.get("hits", []))
    retrieval_debug["term_probe_summary"] = term_probe_payload.get("summary", {})

    results = rerank(
        results,
        query=prepared["expanded_query"],
        mode=retrieval_mode,
        method=rerank_method,
        glossary_terms=prepared["matched_glossary_terms"],
        query_profile=profile,
    )
    results = apply_concept_group_coverage(results, profile)
    results = apply_candidate_qualification(results, plan)
    results = results[:top_k]
    build_snippet_for_display(results, prepared["priority_terms"])
    _hydrate_source_metadata(results)
    retrieval_debug["final_results"] = len(results)
    retrieval_debug["adequacy"] = assess_retrieval_adequacy(
        results, plan,
        term_probe=term_probe_payload,
        register_hits=register_payload.get("hits", []),
    )
    if results:
        scope_debug = results[0].get("_scope_debug", {})
        retrieval_debug["scope_constrained_applied"] = bool(
            scope_debug.get("scope_constrained_applied")
        )
        retrieval_debug["scope_reason"] = scope_debug.get("reason", "")
    return {
        "query": prepared,
        "retrieval": {
            "route": route,
            "top_k": top_k,
            "retrieval_mode": retrieval_mode,
            "rerank_method": rerank_method,
            "debug": retrieval_debug,
            "navigation": {
                "sachregister": register_payload.get("hits", []),
                "evidence_eligible": False,
            },
            "term_probe": {
                "summary": term_probe_payload.get("summary", {}),
                "terms": [
                    {key: item.get(key) for key in
                     ("term", "total_pages", "text_pages", "apparat_pages")}
                    for item in term_probe_payload.get("terms", [])
                ],
            },
        },
        "results": results,
    }


def _hydrate_source_metadata(results: list[dict]) -> None:
    page_ids = []
    for result in results:
        page_id = result.get("page_id") or result.get("id")
        if page_id and not str(page_id).startswith("passage:"):
            page_ids.append(str(page_id))
    page_ids = list(dict.fromkeys(page_ids))
    if not page_ids:
        return

    conn = sqlite3.connect(f"file:{META_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in page_ids)
        rows = conn.execute(
            f"""
            SELECT id, source_file, source_path, source_collection, source_quality,
                   source_record_id, source_doc, source_part, source_url,
                   source_title, page_kind, page_label, content_hash,
                   text_layer, text_layer_confidence, text_layer_provenance,
                   ocr_quality
            FROM chunks WHERE id IN ({placeholders})
            """,
            page_ids,
        ).fetchall()
        metadata = {row["id"]: dict(row) for row in rows}
        for result in results:
            page_id = str(result.get("page_id") or result.get("id") or "")
            for key, value in metadata.get(page_id, {}).items():
                if key != "id" and value not in (None, ""):
                    result[key] = value
            if (
                result.get("source_collection") == "megadigital"
                and not result.get("page_label")
                and result.get("source_doc")
            ):
                sibling = conn.execute(
                    """
                    SELECT page_label FROM chunks
                    WHERE source_collection='megadigital'
                      AND source_doc=?
                      AND COALESCE(source_part, '')=COALESCE(?, '')
                      AND page=? AND COALESCE(page_label, '')<>''
                    LIMIT 1
                    """,
                    (result.get("source_doc"), result.get("source_part"), result.get("page")),
                ).fetchone()
                if sibling:
                    result["page_label"] = sibling[0]
    finally:
        conn.close()


def _matched_priority_terms(text: str, priority_terms: list[str]) -> list[str]:
    lowered = str(text or "").casefold()
    matches = []
    seen = set()
    for term in priority_terms:
        normalized = str(term or "").strip().casefold()
        if normalized and normalized in lowered and normalized not in seen:
            seen.add(normalized)
            matches.append(str(term).strip())
    return matches[:20]


def _locator(record: dict) -> dict:
    abteilung = str(record.get("abteilung") or "?")
    band = str(record.get("band") or "?")
    text_type = str(record.get("type") or record.get("source_type") or "?")
    physical_page = record.get("page", record.get("page_no"))
    page_label = str(record.get("page_label") or "").strip()
    is_digital = record.get("source_collection") == "megadigital"

    if is_digital and page_label:
        citation_stub = f"MEGA² {abteilung}/{band}, {text_type}, S. {page_label}"
        locator_kind = "megadigital_text_page"
        locator_verified = True
        note = "MEGAdigital supplies a text page label; verify the final edition citation."
    elif is_digital:
        citation_stub = (
            f"MEGA² {abteilung}/{band}, {text_type}, "
            f"MEGAdigital source page {physical_page}"
        )
        locator_kind = "megadigital_source_page"
        locator_verified = False
        note = "No printed page label is attached; verify before formal citation."
    else:
        citation_stub = (
            f"MEGA² {abteilung}/{band}, {text_type}, PDF physical page {physical_page}"
        )
        locator_kind = "pdf_physical_page"
        locator_verified = False
        note = "PDF physical page is a retrieval locator, not a verified printed MEGA page."

    return {
        "abteilung": abteilung,
        "band": band,
        "text_type": text_type,
        "physical_or_source_page": physical_page,
        "printed_page_label": page_label or None,
        "passage_no": record.get("passage_no"),
        "char_start": record.get("char_start"),
        "char_end": record.get("char_end"),
        "locator_kind": locator_kind,
        "locator_verified": locator_verified,
        "citation_stub": citation_stub,
        "citation_note": note,
    }


def _evidence_warnings(record: dict, locator: dict) -> list[str]:
    warnings = []
    layer = str(record.get("text_layer") or "unclassified")
    if layer == "textband_unclassified":
        warnings.append(
            "Textband page is not automatically verified as Marx/Engels author text."
        )
    elif layer in {"editorial_intro", "editorial_note", "apparatus"}:
        warnings.append("Editorial material must not be attributed to Marx or Engels.")
    elif layer in {"table_of_contents", "front_matter", "register", "illustration_list"}:
        warnings.append("Paratext is normally unsuitable as direct author evidence.")
    if str(record.get("ocr_quality") or "").lower() in {"low", "failed"}:
        warnings.append("OCR quality is low; verify the scan before quoting.")
    if not locator["locator_verified"]:
        warnings.append(locator["citation_note"])
    return warnings


def _reliability_class(record: dict) -> str:
    layer = str(record.get("text_layer") or "unclassified")
    if is_verified_author_text(record):
        return "structured_author_text"
    if layer == "apparatus":
        return "editorial_apparatus"
    if layer in {"editorial_intro", "editorial_note"}:
        return "editorial_material"
    if layer == "textband_unclassified":
        return "unclassified_textband"
    return "unclassified_or_paratext"


def serialize_evidence(
    record: dict,
    rank: int,
    priority_terms: list[str],
) -> dict:
    snippet = str(record.get("display_snippet") or record.get("text") or "")
    locator = _locator(record)
    debug = record.get("_debug", {})
    return {
        "evidence_id": f"E{rank:03d}",
        "rank": rank,
        "record": {
            "record_type": record.get("record_type", "page"),
            "record_id": record.get("id"),
            "page_id": record.get("page_id") or record.get("id"),
            "passage_id": record.get("passage_id"),
            "content_hash": record.get("content_hash"),
        },
        "source": {
            "display_label": format_source_label(record),
            "title": record.get("source_title"),
            "collection": record.get("source_collection", "ocr"),
            "quality": record.get("source_quality", "ocr"),
            "source_file": record.get("source_file"),
            "source_doc": record.get("source_doc"),
            "source_part": record.get("source_part"),
            "source_url": record.get("source_url"),
            "local_path": record.get("source_path"),
        },
        "locator": locator,
        "provenance": {
            "text_layer": record.get("text_layer", "unclassified"),
            "text_layer_label": text_layer_label(record),
            "text_layer_confidence": record.get("text_layer_confidence"),
            "text_layer_provenance": record.get("text_layer_provenance"),
            "verified_author_text": is_verified_author_text(record),
            "evidence_eligible": bool(record.get("evidence_eligible", False)),
            "candidate_class": record.get("_qualification", {}).get("candidate_class"),
            "reliability_class": _reliability_class(record),
            "ocr_quality": record.get("ocr_quality"),
        },
        "evidence": {
            "german_context": snippet,
            "preview": record.get("display_preview"),
            "matched_term": record.get("matched_term"),
            "matched_priority_terms": _matched_priority_terms(snippet, priority_terms),
            "quote_sha256": hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
            "character_count": len(snippet),
            "rough_token_estimate": max(1, round(len(snippet) / 4.2)),
        },
        "retrieval": {
            "sources": record.get("_retrieval_sources", []),
            "matched_variants": record.get("_matched_variants", []),
            "concept_groups": record.get("_matched_concept_groups", []),
            "qualification": record.get("_qualification", {}),
            "register_references": record.get("_register_references", []),
            "rrf_score": record.get("rrf_score"),
            "final_score": record.get("final_score"),
            "debug": debug,
        },
        "warnings": _evidence_warnings(record, locator),
        "review": {
            "status": "unreviewed",
            "claim_supported": None,
            "literal_translation": None,
            "research_notes": None,
        },
    }


def build_research_package(question: str, retrieval_payload: dict) -> dict:
    query = retrieval_payload["query"]
    evidence = [
        serialize_evidence(record, rank, query["priority_terms"])
        for rank, record in enumerate(retrieval_payload.get("results", []), start=1)
    ]
    index_version = get_current_version()
    query_digest = _query_hash(question)
    generated_at = _now_iso()
    package_seed = f"{query_digest}|{index_version}|{generated_at}"
    package_id = hashlib.sha256(package_seed.encode("utf-8")).hexdigest()[:16]
    total_chars = sum(item["evidence"]["character_count"] for item in evidence)
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "package_id": package_id,
        "generated_at": generated_at,
        "index_version": index_version,
        "question": question,
        "query_hash": query_digest,
        "query": query,
        "retrieval": retrieval_payload["retrieval"],
        "summary": {
            "evidence_count": len(evidence),
            "authoritative_digital_count": sum(
                item["source"]["collection"] == "megadigital" for item in evidence
            ),
            "verified_author_text_count": sum(
                item["provenance"]["verified_author_text"] for item in evidence
            ),
            "evidence_eligible_count": sum(
                item["provenance"].get("evidence_eligible", False) for item in evidence
            ),
            "unverified_locator_count": sum(
                not item["locator"]["locator_verified"] for item in evidence
            ),
            "total_context_characters": total_chars,
            "rough_total_tokens": sum(
                item["evidence"]["rough_token_estimate"] for item in evidence
            ),
        },
        "usage_constraints": [
            "Use only the supplied German context as evidence; do not invent quotations.",
            "Only structured_author_text may be automatically described as Marx/Engels author text.",
            "Editorial and APPARAT material must be attributed to the editors.",
            "An unverified locator must be checked against the printed MEGA edition before formal citation.",
            "Retain evidence_id when drafting so every claim can be audited.",
            "Sachregister records are navigation hints only; cite the resolved TEXT passage.",
            "Related non-equivalent terms provide context and must not be reported as exact concept hits.",
        ],
        "evidence": evidence,
    }


def render_research_markdown(package: dict) -> str:
    summary = package["summary"]
    query = package["query"]
    lines = [
        "# MEGA² Research Evidence Package",
        "",
        f"- Package ID: `{package['package_id']}`",
        f"- Generated: `{package['generated_at']}`",
        f"- Index version: `{package['index_version']}`",
        f"- Evidence items: {summary['evidence_count']}",
        f"- Approximate evidence tokens: {summary['rough_total_tokens']}",
        "",
        "## Research question",
        "",
        package["question"],
        "",
        "## Query profile",
        "",
        f"- Intent: `{query['query_profile'].get('intent')}`",
        f"- Target volume: `{query['query_profile'].get('target_abteilung') or '-'}/{query['query_profile'].get('target_band') or '-'}`",
        f"- Matched glossary terms: {', '.join(query['matched_glossary_terms']) or '-'}",
        f"- Priority terms: {', '.join(query['priority_terms']) or '-'}",
        "",
        "## Citation boundary",
        "",
        "PDF physical pages are retrieval locators, not automatically verified MEGA printed pages. "
        "Only a MEGAdigital text page label is marked as a verified locator, and final bibliography "
        "still requires edition-level checking.",
        "",
        "## Evidence index",
        "",
        "| ID | Source | Layer | Matched terms | Locator verified |",
        "|---|---|---|---|---|",
    ]
    for item in package["evidence"]:
        matched = ", ".join(item["evidence"]["matched_priority_terms"]) or "-"
        verified = "yes" if item["locator"]["locator_verified"] else "no"
        lines.append(
            f"| {item['evidence_id']} | {item['locator']['citation_stub']} | "
            f"{item['provenance']['text_layer_label']} | {matched} | {verified} |"
        )

    for item in package["evidence"]:
        context = item["evidence"]["german_context"].replace("```", "`` `")
        lines.extend([
            "",
            f"## {item['evidence_id']} - {item['locator']['citation_stub']}",
            "",
            f"- Source: {item['source']['display_label']}",
            f"- Title: {item['source']['title'] or '-'}",
            f"- Collection: `{item['source']['collection']}`",
            f"- Text layer: {item['provenance']['text_layer_label']}",
            f"- Reliability: `{item['provenance']['reliability_class']}`",
            f"- Locator verified: `{str(item['locator']['locator_verified']).lower()}`",
            f"- Citation note: {item['locator']['citation_note']}",
            f"- Matched terms: {', '.join(item['evidence']['matched_priority_terms']) or '-'}",
        ])
        if item["source"].get("source_url"):
            lines.append(f"- Source URL: {item['source']['source_url']}")
        if item["warnings"]:
            lines.append(f"- Warnings: {'; '.join(item['warnings'])}")
        lines.extend([
            "",
            "### German evidence context",
            "",
            "```text",
            context,
            "```",
            "",
            "### Research review",
            "",
            "- Claim supported: _unreviewed_",
            "- Literal translation: _not generated_",
            "- Notes: _unreviewed_",
        ])

    lines.extend([
        "",
        "## Instructions for Codex or another analysis model",
        "",
        "1. Refer to evidence by its stable `E###` identifier.",
        "2. Distinguish author text, editorial material, and unclassified Textband pages.",
        "3. Do not turn a PDF physical page into a formal MEGA page citation without verification.",
        "4. State explicitly when the evidence package does not support a requested claim.",
        "5. Preserve the German wording when making a philological claim.",
        "",
    ])
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_research_package(
    package: dict,
    output_dir: str | Path | None = None,
    output_format: str = "both",
) -> dict:
    destination = Path(output_dir) if output_dir else _default_output_dir()
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"research_{timestamp}_{package['query_hash'][:8]}"
    paths: dict[str, str | None] = {"json": None, "markdown": None}
    if output_format in {"both", "json"}:
        json_path = destination / f"{base_name}.json"
        _atomic_write(json_path, json.dumps(package, ensure_ascii=False, indent=2))
        paths["json"] = str(json_path.resolve())
    if output_format in {"both", "markdown"}:
        markdown_path = destination / f"{base_name}.md"
        _atomic_write(markdown_path, render_research_markdown(package))
        paths["markdown"] = str(markdown_path.resolve())
    return paths


def export_research_package(
    question: str,
    route: str = "all",
    top_k: int = 12,
    retrieval_mode: str = "balanced",
    rerank_method: str = "rule",
    output_dir: str | Path | None = None,
    output_format: str = "both",
) -> dict:
    retrieval_payload = retrieve_research_evidence(
        question,
        route=route,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
    )
    package = build_research_package(question, retrieval_payload)
    paths = write_research_package(package, output_dir, output_format)
    return {"package": package, "paths": paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--route", choices=("all", "main_text", "apparat"), default="all")
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument(
        "--retrieval-mode",
        choices=("original_first", "apparat_first", "balanced", "philology"),
        default="balanced",
    )
    parser.add_argument("--rerank", choices=("none", "rule", "bge"), default="rule")
    parser.add_argument("--output-dir")
    parser.add_argument("--format", choices=("both", "json", "markdown"), default="both")
    args = parser.parse_args()

    try:
        exported = export_research_package(
            args.question,
            route=args.route,
            top_k=args.top_k,
            retrieval_mode=args.retrieval_mode,
            rerank_method=args.rerank,
            output_dir=args.output_dir,
            output_format=args.format,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    package = exported["package"]
    print(
        f"Exported {package['summary']['evidence_count']} evidence items; "
        f"approximately {package['summary']['rough_total_tokens']} tokens."
    )
    for name, path in exported["paths"].items():
        if path:
            print(f"{name}: {path}")
    return 0 if package["summary"]["evidence_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
