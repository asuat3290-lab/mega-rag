#!/usr/bin/env python3
"""Stable, token-aware service functions for external MEGA research agents."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from claim_audit import audit_claim, write_claim_audit
from claim_schema import AGENT_SCHEMA_VERSION, CLAIM_AUDIT_SCHEMA_VERSION
from glossary_loader import load_glossary
from index_version import get_current_version
from query_plan import build_query_plan, compact_plan
from sachregister import register_status, search_sachregister
from term_probe import probe_query_plan
from research_export import (
    EXPORT_SCHEMA_VERSION,
    build_research_package,
    retrieve_research_evidence,
    write_research_package,
)


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((SCRIPT_DIR / "config.yaml").read_text(encoding="utf-8"))
DETAIL_LEVELS = {"index", "snippet", "full"}
AGENT_PROTOCOL = "mega-research-workbench/v1"


def _validate_detail(detail: str) -> str:
    value = str(detail or "index").strip().casefold()
    if value not in DETAIL_LEVELS:
        raise ValueError(f"unsupported detail level: {detail}")
    return value


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _rough_json_tokens(payload: Any) -> int:
    """Estimate tokens for the JSON payload actually returned to an agent."""
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, round(len(serialized) / 4.2)) if serialized else 0


def _compact_evidence(item: dict, detail: str) -> dict:
    detail = _validate_detail(detail)
    if detail == "full":
        return item
    source = item.get("source", {})
    locator = item.get("locator", {})
    provenance = item.get("provenance", {})
    evidence = item.get("evidence", {})
    retrieval = item.get("retrieval", {})
    output = {
        "evidence_id": item.get("evidence_id"),
        "citation": locator.get("citation_stub") or source.get("display_label"),
        "source_collection": source.get("collection"),
        "source_quality": source.get("quality"),
        "text_layer": provenance.get("text_layer"),
        "reliability_class": provenance.get("reliability_class"),
        "verified_author_text": bool(provenance.get("verified_author_text")),
        "evidence_eligible": bool(provenance.get("evidence_eligible", False)),
        "candidate_class": provenance.get("candidate_class"),
        "locator_verified": bool(locator.get("locator_verified")),
        "matched_term": evidence.get("matched_term"),
        "matched_priority_terms": evidence.get("matched_priority_terms", []),
        "preview": _clip(evidence.get("preview") or evidence.get("german_context"), 360),
        "rough_token_estimate": evidence.get("rough_token_estimate"),
        "warnings": item.get("warnings", []),
        "retrieval_sources": retrieval.get("sources", []),
        "matched_variants": retrieval.get("matched_variants", []),
        "concept_groups": retrieval.get("concept_groups", []),
    }
    if detail == "snippet":
        output["german_context"] = _clip(evidence.get("german_context"), 1400)
        output["locator"] = locator
        output["source"] = source
    return output


def _envelope(operation: str, payload: dict) -> dict:
    return {
        "ok": True,
        "protocol": AGENT_PROTOCOL,
        "schema_version": AGENT_SCHEMA_VERSION,
        "operation": operation,
        **payload,
    }


def capabilities() -> dict:
    """Describe the stable external interface without touching the indexes."""
    return _envelope(
        "capabilities",
        {
            "service": "MEGA research workbench",
            "operations": {
                "plan": {
                    "purpose": "build an inspectable German retrieval plan",
                    "modes": ["local", "hybrid", "agent_supplied"],
                    "api_models_used": False,
                },
                "term_probe": {
                    "purpose": "count exact term coverage before expensive retrieval",
                    "api_models_used": False,
                },
                "register": {
                    "purpose": "query MEGA Sachregister as navigation, never as author evidence",
                    "api_models_used": False,
                },
                "search": {
                    "purpose": "retrieve source-backed German evidence",
                    "detail": sorted(DETAIL_LEVELS),
                    "default_detail": "index",
                    "api_models_used": False,
                },
                "verify": {
                    "purpose": "audit an interpretation against retrieved evidence",
                    "budgets": ["brief", "standard", "deep"],
                    "default_budget": "brief",
                    "api_models_optional": True,
                },
                "evidence": {
                    "purpose": "expand selected evidence IDs from a saved JSON package",
                    "api_models_used": False,
                },
                "status": {
                    "purpose": "report index readiness and corpus counts",
                    "api_models_used": False,
                },
            },
            "schemas": {
                "agent": AGENT_SCHEMA_VERSION,
                "research_package": EXPORT_SCHEMA_VERSION,
                "claim_audit": CLAIM_AUDIT_SCHEMA_VERSION,
            },
            "recommended_agent_flow": [
                "plan(query) and inspect historical/related/generic roles",
                "optionally submit a refinement JSON when domain terms are missing",
                "search(detail=index, save=true)",
                "inspect citations and warnings",
                "evidence(ids=[...], detail=snippet|full)",
                "verify only when a semantic claim judgment is needed",
            ],
        },
    )


def plan_agent(query: str, *, refinement: dict | None = None,
               planner_mode: str = "local", include_probe: bool = True,
               include_register: bool = True) -> dict:
    """Return a local, hybrid, or externally refined QueryPlan."""
    if refinement:
        planner_mode = "agent_supplied"
    if planner_mode not in {"local", "hybrid", "agent_supplied"}:
        raise ValueError(f"unsupported planner_mode: {planner_mode}")
    local_plan = build_query_plan(query, glossary=load_glossary())
    planner_diagnostics = {
        "mode": planner_mode, "model": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "fallback": False,
    }
    if planner_mode == "hybrid":
        from query_plan_model import build_hybrid_plan
        plan, planner_diagnostics = build_hybrid_plan(query, local_plan=local_plan)
    elif planner_mode == "agent_supplied":
        if not refinement:
            raise ValueError("agent_supplied planner mode requires refinement")
        plan = build_query_plan(
            query, glossary=load_glossary(), mode=planner_mode, refinement=refinement
        )
    else:
        plan = local_plan
    probe = probe_query_plan(plan, sample_limit=0) if include_probe else None
    register_payload = None
    if include_register:
        terms = (
            plan.get("core_terms", []) + plan.get("historical_variants", [])
            + plan.get("related_non_equivalent", [])
        )[:15]
        register_payload = search_sachregister(
            terms, top_k=8, target_volumes=plan.get("target_volumes", [])
        )
    return _envelope(
        "plan",
        {
            "query": query,
            "plan": compact_plan(plan),
            "matched_rule_ids": plan.get("matched_rule_ids", []),
            "term_probe": probe,
            "register": register_payload,
            "planner_diagnostics": planner_diagnostics,
            "usage": {
                "api_tokens": int(planner_diagnostics.get("usage", {}).get("total_tokens", 0))
            },
        },
    )


def term_probe_agent(query: str, *, refinement: dict | None = None) -> dict:
    plan = build_query_plan(
        query, glossary=load_glossary(),
        mode="agent_supplied" if refinement else "local",
        refinement=refinement,
    )
    return _envelope(
        "term_probe",
        {"query": query, "plan": compact_plan(plan),
         "probe": probe_query_plan(plan), "usage": {"api_tokens": 0}},
    )


def register_agent(query: str, *, refinement: dict | None = None,
                   top_k: int = 8) -> dict:
    plan = build_query_plan(
        query, glossary=load_glossary(),
        mode="agent_supplied" if refinement else "local",
        refinement=refinement,
    )
    terms = (
        plan.get("core_terms", []) + plan.get("historical_variants", [])
        + plan.get("related_non_equivalent", [])
    )[:15]
    payload = search_sachregister(
        terms, top_k=top_k, target_volumes=plan.get("target_volumes", [])
    )
    return _envelope(
        "register",
        {"query": query, "plan": compact_plan(plan), "navigation": payload,
         "usage": {"api_tokens": 0},
         "warning": "Sachregister entries navigate to evidence; they are not author evidence."},
    )

def search_agent(
    query: str,
    *,
    route: str = "all",
    top_k: int = 8,
    retrieval_mode: str = "balanced",
    rerank_method: str = "rule",
    detail: str = "index",
    save: bool = False,
    output_dir: str | Path | None = None,
    planner_mode: str = "local",
    plan_refinement: dict | None = None,
) -> dict:
    """Retrieve evidence and return a compact, deterministic agent response."""
    detail = _validate_detail(detail)
    retrieval = retrieve_research_evidence(
        query,
        route=route,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
        planner_mode=planner_mode,
        plan_refinement=plan_refinement,
    )
    package = build_research_package(query, retrieval)
    paths = (
        write_research_package(package, output_dir=output_dir, output_format="json")
        if save
        else {"json": None, "markdown": None}
    )
    returned_evidence = [
        _compact_evidence(item, detail) for item in package.get("evidence", [])
    ]
    return _envelope(
        "search",
        {
            "index_version": package["index_version"],
            "query": {
                "original": query,
                "expanded": package["query"].get("expanded_query"),
                "intent": package["query"].get("query_profile", {}).get("intent"),
                "target_volume": {
                    "abteilung": package["query"].get("query_profile", {}).get("target_abteilung"),
                    "band": package["query"].get("query_profile", {}).get("target_band"),
                },
                "priority_terms": package["query"].get("priority_terms", [])[:20],
                "planner_mode": package["query"].get("query_plan", {}).get("mode", planner_mode),
                "planner_version": package["query"].get("query_plan", {}).get("planner_version"),
            },
            "detail": detail,
            "summary": {
                **package["summary"],
                "retrieval_adequacy": package.get("retrieval", {}).get("debug", {}).get("adequacy"),
            },
            "evidence": returned_evidence,
            "artifact_paths": paths,
            "usage": {
                "api_tokens": int(
                    package["query"].get("planner_diagnostics", {})
                    .get("usage", {}).get("total_tokens", 0)
                ),
                "planner": package["query"].get("planner_diagnostics", {}),
                "rough_returned_evidence_tokens": _rough_json_tokens(returned_evidence),
            },
            "warnings": package.get("usage_constraints", []),
        },
    )


def verify_agent(
    idea: str,
    *,
    budget: str = "brief",
    route: str = "main_text",
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    detail: str = "index",
    use_flash: bool = True,
    use_pro: bool = False,
    use_cache: bool = True,
    save: bool = False,
    output_dir: str | Path | None = None,
    progress=None,
) -> dict:
    """Audit a claim, preserving verdict calibration and evidence provenance."""
    detail = _validate_detail(detail)
    audit = audit_claim(
        idea,
        budget=budget,
        route=route,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
        use_flash=use_flash,
        use_pro=use_pro,
        use_cache=use_cache,
        progress=progress,
    )
    paths = (
        write_claim_audit(audit, output_dir=output_dir, output_format="json")
        if save
        else {"json": None, "markdown": None}
    )
    return _envelope(
        "verify",
        {
            "index_version": audit["index_version"],
            "audit_id": audit["audit_id"],
            "idea": audit["idea"],
            "budget": audit["budget"],
            "mode": audit["mode"],
            "status": audit.get("status", {}),
            "summary": audit["summary"],
            "overall": audit["overall"],
            "claims": audit["claims"],
            "academic_synthesis": audit.get("academic_synthesis", ""),
            "detail": detail,
            "evidence": [
                _compact_evidence(item, detail) for item in audit.get("evidence", [])
            ],
            "usage": audit.get("usage", {}),
            "runtime": audit.get("runtime", {}),
            "warnings": audit.get("warnings", []),
            "artifact_paths": paths,
        },
    )


def evidence_from_package(
    package_path: str | Path,
    evidence_ids: list[str] | None = None,
    *,
    detail: str = "full",
) -> dict:
    """Load selected evidence from a saved research-package or claim-audit JSON file."""
    detail = _validate_detail(detail)
    path = Path(package_path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("evidence")
    if not isinstance(items, list):
        raise ValueError("package does not contain an evidence list")
    wanted = {str(value).strip().casefold() for value in evidence_ids or [] if str(value).strip()}
    selected = [
        item
        for item in items
        if not wanted or str(item.get("evidence_id") or "").casefold() in wanted
    ]
    found = {str(item.get("evidence_id") or "").casefold() for item in selected}
    missing = sorted(wanted - found)
    return _envelope(
        "evidence",
        {
            "package_path": str(path),
            "detail": detail,
            "requested_ids": sorted(wanted),
            "missing_ids": missing,
            "evidence": [_compact_evidence(item, detail) for item in selected],
        },
    )


def status_agent() -> dict:
    """Return a fast read-only status snapshot suitable for orchestration checks."""
    db_path = Path(CONFIG["paths"]["metadata_db"])
    counts = {"chunks": 0, "passages": 0, "fts": 0, "passage_fts": 0}
    sources: dict[str, int] = {}
    if db_path.exists():
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            table_names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
            }
            for table, key in (
                ("chunks", "chunks"),
                ("passages", "passages"),
                ("chunks_fts", "fts"),
                ("passages_fts", "passage_fts"),
            ):
                if table in table_names:
                    counts[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if "chunks" in table_names:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
                if "source_collection" in columns:
                    sources = {
                        str(row[0] or "ocr"): int(row[1])
                        for row in conn.execute(
                            "SELECT COALESCE(source_collection, 'ocr'), COUNT(*) FROM chunks GROUP BY 1"
                        )
                    }
        finally:
            conn.close()
    ready = bool(counts["chunks"] and counts["fts"])
    return _envelope(
        "status",
        {
            "ready": ready,
            "index_version": get_current_version(),
            "metadata_db": str(db_path),
            "sachregister": register_status(),
            "counts": counts,
            "sources": sources,
            "vector_db_exists": Path(CONFIG["paths"]["vector_db"]).exists(),
        },
    )
