#!/usr/bin/env python3
"""Stable, token-aware service functions for external MEGA research agents."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from claim_audit import audit_claim, write_claim_audit
from claim_schema import AGENT_SCHEMA_VERSION, CLAIM_AUDIT_SCHEMA_VERSION
from glossary_loader import load_glossary
from index_version import get_current_version
from planner_policy import assess_query_plan, assess_research_plan
from protocol_trace import build_protocol_report, record_protocol_event
from planning_memory import (
    find_promoted,
    list_memory,
    memory_status as planning_memory_status,
    record_candidate,
    review_memory,
    validate_against_corpus,
)
from query_hints import merge_query_hints
from query_plan import build_query_plan, compact_plan
from report_contract import load_json_artifact, validate_report_claims
from research_orchestrator import run_research, write_research_run
from research_plan import build_research_plan, compact_research_plan
from research_session import (
    COMPLETE,
    EVIDENCE_EXPANDED,
    EVIDENCE_QUALIFIED,
    NEEDS_REFINEMENT,
    PLANNED,
    REPORT_VALIDATED,
    RETRIEVAL_GAP,
    RETRIEVED,
    append_session_event,
    create_session,
    get_session,
    public_session,
    session_events,
    transition_session,
)
from sachregister import register_status, search_sachregister
from source_catalog import (
    build_source_catalog,
    catalog_status as source_catalog_status,
    coverage_report as source_catalog_coverage,
    get_source_context,
    list_source_documents,
)
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
    source_identity = item.get("source_identity", {})
    source_quote_eligible = bool(provenance.get("source_quote_eligible", False))
    preview_only = detail == "index"
    warnings = list(item.get("warnings", []))
    if preview_only:
        warnings.append(
            "Index preview is for evidence selection only; request snippet or full detail before quoting."
        )
    output = {
        "evidence_id": item.get("evidence_id"),
        "evidence_uid": item.get("evidence_uid"),
        "package_evidence_ref": item.get("package_evidence_ref"),
        "evidence_role": item.get("evidence_role"),
        "citation": locator.get("citation_stub") or source.get("display_label"),
        "source_collection": source.get("collection"),
        "source_quality": source.get("quality"),
        "source_id": source_identity.get("source_id"),
        "source_catalog_version": source_identity.get("catalog_version"),
        "source_document_kind": source_identity.get("document_kind"),
        "source_volume_group": source_identity.get("volume_group"),
        "version_groups": [
            {
                "group_id": group.get("group_id"),
                "group_type": group.get("group_type"),
                "member_role": group.get("member_role"),
                "sequence_no": group.get("sequence_no"),
            }
            for group in source_identity.get("groups", [])
            if group.get("group_type") != "mega_volume"
        ],
        "source_relations": [
            {
                "direction": relation.get("direction"),
                "predicate": relation.get("predicate"),
                "related_source_id": relation.get("related_source_id"),
            }
            for relation in source_identity.get("relations", [])
        ],
        "authorship_status": provenance.get("authorship_status"),
        "edition_status": provenance.get("edition_status"),
        "attribution_note": provenance.get("attribution_note"),
        "source_quote_eligible": source_quote_eligible,
        "context_boundary_complete": bool(evidence.get("context_boundary_complete")),
        "preview_only": preview_only,
        "quote_eligible": bool(evidence.get("quote_eligible") and not preview_only),
        "text_type": locator.get("text_type"),
        "text_layer": provenance.get("text_layer"),
        "reliability_class": provenance.get("reliability_class"),
        "verified_author_text": bool(provenance.get("verified_author_text")),
        "evidence_eligible": bool(provenance.get("evidence_eligible", False)),
        "semantic_ready": bool(provenance.get("semantic_ready")),
        "scope_ready": bool(provenance.get("scope_ready")),
        "provenance_ready": bool(provenance.get("provenance_ready")),
        "claim_eligible": bool(
            provenance.get("claim_eligible", provenance.get("evidence_eligible", False))
        ),
        "claim_ready": bool(provenance.get("claim_ready")),
        "candidate_class": provenance.get("candidate_class"),
        "match_type": item.get("match_type") or retrieval.get("match_type"),
        "match_types": item.get("match_types") or retrieval.get("match_types", []),
        "match_type_details": item.get("match_type_details") or retrieval.get("match_type_details", {}),
        "locator_verified": bool(locator.get("locator_verified")),
        "matched_term": evidence.get("matched_term"),
        "matched_priority_terms": evidence.get("matched_priority_terms", []),
        "preview": _clip(evidence.get("preview") or evidence.get("german_context"), 360),
        "rough_token_estimate": evidence.get("rough_token_estimate"),
        "warnings": warnings,
        "retrieval_sources": retrieval.get("sources", []),
        "matched_variants": retrieval.get("matched_variants", []),
        "concept_groups": retrieval.get("concept_groups", []),
        "branch_ids": item.get("research", {}).get("branch_ids", []),
        "claim_types": item.get("research", {}).get("claim_types", []),
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
                    "modes": ["local", "auto", "hybrid", "agent_supplied"],
                    "api_models_optional": True,
                },
                "research_plan": {
                    "purpose": "decompose a compound research question by evidence requirement",
                    "modes": ["local", "auto", "hybrid", "agent_supplied"],
                    "api_models_optional": True,
                },
                "research_run": {
                    "purpose": "retrieve every required MEGA branch and expose unresolved external evidence",
                    "detail": sorted(DETAIL_LEVELS),
                    "default_detail": "index",
                    "api_models_optional": True,
                },
                "research_session": {
                    "purpose": "enforce plan, retrieval, expansion, report validation, and completion receipt",
                    "states": [
                        "PLANNED", "NEEDS_REFINEMENT", "RETRIEVED", "RETRIEVAL_GAP",
                        "EVIDENCE_QUALIFIED", "EVIDENCE_EXPANDED", "REPORT_VALIDATED",
                        "COMPLETE",
                    ],
                    "api_models_optional": True,
                },
                "planning_memory": {
                    "purpose": "reuse only corpus-validated and human-promoted plans",
                    "statuses": ["proposed", "corpus_validated", "promoted", "rejected"],
                    "automatic_promotion": False,
                    "api_models_used": False,
                },
                "source_catalog": {
                    "purpose": "inspect stable source identities, edition status, coverage, and source-level relations",
                    "automatic_inference_scope": "same MEGA volume and carrier relation only",
                    "curated_relations": True,
                    "api_models_used": False,
                },
                "term_probe": {
                    "purpose": "separate exact phrase, lexical variant, and semantic-related indexed hits",
                    "pagination": True,
                    "json_export": True,
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
                    "purpose": "expand selected local IDs, stable UIDs, or package references",
                    "api_models_used": False,
                },
                "report_check": {
                    "purpose": "validate structured claims, quotations, and evidence identities",
                    "api_models_used": False,
                },
                "protocol_report": {
                    "purpose": "audit an agent research trace without blocking retrieval",
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
                "research_plan": "mega-research-plan-v1",
                "research_run": "mega-research-run-v1",
                "research_session": "mega-research-session-v1",
                "completion_receipt": "mega-research-completion-receipt-v1",
            },
            "recommended_agent_flow": [
                "for formal research, use research-session start/continue/expand/finalize",
                "treat free search as exploratory unless a formal session reaches COMPLETE",
                "for compound questions, call research-plan before ordinary search",
                "call research-run to require adequacy across every MEGA branch",
                "plan(query) and inspect historical/related/generic roles",
                "optionally submit a refinement JSON when domain terms are missing",
                "search(detail=index, save=true) is diagnostic unless synthesis_gate allows use",
                "inspect synthesis_gate, citations, evidence_role, and warnings",
                "preserve source_id, edition_status, and version_groups during synthesis",
                "use source-catalog-show before making cross-version claims",
                "evidence(ids=[...], detail=snippet|full)",
                "write claims as structured JSON and run report-check before prose export",
                "verify only when a semantic claim judgment is needed",
            ],
        },
    )


def plan_agent(query: str, *, refinement: dict | None = None,
               planner_mode: str = "local", include_probe: bool = True,
               include_register: bool = True, focus_terms: list[str] | None = None,
               context_terms: list[str] | None = None,
               target_volumes: list[str] | None = None,
               intent: str | None = None,
               trace_id: str | None = None) -> dict:
    """Return a local, automatic, hybrid, or externally refined QueryPlan."""
    refinement = merge_query_hints(
        refinement,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent,
    )
    requested_mode = planner_mode
    if refinement:
        planner_mode = "agent_supplied"
    if planner_mode not in {"local", "auto", "hybrid", "agent_supplied"}:
        raise ValueError(f"unsupported planner_mode: {planner_mode}")
    local_plan = build_query_plan(query, glossary=load_glossary())
    local_planning_advice = assess_query_plan(local_plan)
    promoted = None
    memory_error = None
    if planner_mode == "auto" and local_planning_advice.get("should_refine"):
        try:
            promoted = find_promoted(
                "query_plan",
                query,
                planner_version=str(local_plan.get("planner_version") or ""),
            )
        except Exception as exc:
            memory_error = f"{type(exc).__name__}: {exc}"
    effective_mode = planner_mode
    if planner_mode == "auto":
        if not local_planning_advice.get("should_refine"):
            effective_mode = "local"
        elif promoted:
            effective_mode = "promoted_memory"
        else:
            effective_mode = "hybrid"
    planner_diagnostics = {
        "mode": effective_mode, "model": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "fallback": False, "cache_hit": False,
        "memory_hit": bool(promoted),
    }
    if effective_mode == "promoted_memory":
        plan = build_query_plan(
            query,
            glossary=load_glossary(),
            mode=effective_mode,
            refinement=promoted["payload"],
        )
        planner_diagnostics["memory_id"] = promoted["memory_id"]
    elif effective_mode == "hybrid":
        from query_plan_model import build_hybrid_plan
        plan, planner_diagnostics = build_hybrid_plan(query, local_plan=local_plan)
        planner_diagnostics["memory_hit"] = False
    elif effective_mode == "agent_supplied":
        if not refinement:
            raise ValueError("agent_supplied planner mode requires refinement")
        plan = build_query_plan(
            query, glossary=load_glossary(), mode=effective_mode,
            refinement=refinement,
        )
    else:
        plan = local_plan
    planner_diagnostics["requested_mode"] = requested_mode
    planner_diagnostics["effective_mode"] = effective_mode
    if memory_error:
        planner_diagnostics["memory_error"] = memory_error
    proposal = (plan.get("refinement") or {}).get("payload")
    if (
        proposal
        and effective_mode in {"hybrid", "agent_supplied"}
        and not planner_diagnostics.get("fallback")
    ):
        try:
            planner_diagnostics["memory_candidate_id"] = record_candidate(
                "query_plan",
                query,
                proposal,
                planner_version=str(local_plan.get("planner_version") or ""),
                index_version=str(get_current_version() or ""),
                source=str((plan.get("refinement") or {}).get("source") or effective_mode),
            )
        except Exception as exc:
            planner_diagnostics["memory_capture_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
    planning_advice = assess_query_plan(plan)
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
    record_protocol_event(
        trace_id,
        "plan",
        query=query,
        payload={
            "planning_advice": planning_advice,
            "focus_explicit": bool(plan.get("focus_explicit")),
            "focus_terms": list(plan.get("focus_terms", [])),
            "target_volumes": list(plan.get("target_volumes", [])),
            "register_consulted": bool(include_register),
            "register_hits": len((register_payload or {}).get("hits", [])),
            "refinement_used": bool(refinement),
        },
    )
    return _envelope(
        "plan",
        {
            "query": query,
            "plan": compact_plan(plan),
            "planning_advice": planning_advice,
            "matched_rule_ids": plan.get("matched_rule_ids", []),
            "term_probe": probe,
            "register": register_payload,
            "planner_diagnostics": planner_diagnostics,
            "usage": {
                "api_tokens": int(
                    planner_diagnostics.get("usage", {}).get("total_tokens", 0)
                )
            },
        },
    )

def _resolve_research_plan(
    query: str,
    *,
    refinement: dict | None = None,
    planner_mode: str = "local",
) -> tuple[dict, dict, dict]:
    requested_mode = planner_mode
    if refinement:
        planner_mode = "agent_supplied"
    if planner_mode not in {"local", "auto", "hybrid", "agent_supplied"}:
        raise ValueError(f"unsupported planner_mode: {planner_mode}")
    local_plan = build_research_plan(query)
    local_planning_advice = assess_research_plan(local_plan)
    promoted = None
    memory_error = None
    if planner_mode == "auto" and local_planning_advice.get("should_refine"):
        try:
            promoted = find_promoted(
                "research_plan",
                query,
                planner_version=str(local_plan.get("planner_version") or ""),
            )
        except Exception as exc:
            memory_error = f"{type(exc).__name__}: {exc}"
    effective_mode = planner_mode
    if planner_mode == "auto":
        if not local_planning_advice.get("should_refine"):
            effective_mode = "local"
        elif promoted:
            effective_mode = "promoted_memory"
        else:
            effective_mode = "hybrid"
    diagnostics = {
        "mode": effective_mode,
        "model": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "fallback": False,
        "cache_hit": False,
        "memory_hit": bool(promoted),
    }
    if effective_mode == "promoted_memory":
        plan = build_research_plan(
            query,
            mode=effective_mode,
            refinement=promoted["payload"],
        )
        diagnostics["memory_id"] = promoted["memory_id"]
    elif effective_mode == "hybrid":
        from research_plan_model import build_hybrid_research_plan
        plan, diagnostics = build_hybrid_research_plan(
            query, local_plan=local_plan
        )
        diagnostics["memory_hit"] = False
    elif effective_mode == "agent_supplied":
        if not refinement:
            raise ValueError("agent_supplied planner mode requires refinement")
        plan = build_research_plan(
            query, mode=effective_mode, refinement=refinement
        )
    else:
        plan = local_plan
    diagnostics["requested_mode"] = requested_mode
    diagnostics["effective_mode"] = effective_mode
    if memory_error:
        diagnostics["memory_error"] = memory_error
    proposal = (plan.get("refinement") or {}).get("payload")
    if (
        proposal
        and effective_mode in {"hybrid", "agent_supplied"}
        and not diagnostics.get("fallback")
    ):
        try:
            diagnostics["memory_candidate_id"] = record_candidate(
                "research_plan",
                query,
                proposal,
                planner_version=str(local_plan.get("planner_version") or ""),
                index_version=str(get_current_version() or ""),
                source=str((plan.get("refinement") or {}).get("source") or effective_mode),
            )
        except Exception as exc:
            diagnostics["memory_capture_error"] = f"{type(exc).__name__}: {exc}"
    planning_advice = assess_research_plan(plan)
    return plan, diagnostics, planning_advice

def research_plan_agent(
    query: str,
    *,
    refinement: dict | None = None,
    planner_mode: str = "local",
    trace_id: str | None = None,
) -> dict:
    """Return question coverage and evidence requirements before retrieval."""
    plan, diagnostics, planning_advice = _resolve_research_plan(
        query, refinement=refinement, planner_mode=planner_mode
    )
    record_protocol_event(
        trace_id,
        "research_plan",
        query=query,
        payload={
            "planning_advice": planning_advice,
            "refinement_used": bool(refinement),
            "branch_count": len(plan.get("subquestions", [])),
        },
    )
    return _envelope(
        "research_plan",
        {
            "query": query,
            "research_plan": compact_research_plan(plan),
            "planning_advice": planning_advice,
            "planner_diagnostics": diagnostics,
            "usage": {
                "api_tokens": int(diagnostics.get("usage", {}).get("total_tokens", 0))
            },
        },
    )


def research_run_agent(
    query: str,
    *,
    top_k_per_branch: int = 5,
    max_evidence: int = 12,
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    detail: str = "index",
    save: bool = False,
    output_dir: str | Path | None = None,
    refinement: dict | None = None,
    planner_mode: str = "local",
    trace_id: str | None = None,
) -> dict:
    """Run every MEGA branch and return explicit unresolved evidence requirements."""
    detail = _validate_detail(detail)
    plan, diagnostics, planning_advice = _resolve_research_plan(
        query, refinement=refinement, planner_mode=planner_mode
    )
    run = run_research(
        query,
        research_plan=plan,
        top_k_per_branch=top_k_per_branch,
        max_evidence=max_evidence,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
    )
    artifact_path = None
    if save:
        directory = (
            Path(output_dir)
            if output_dir
            else Path(
                CONFIG.get("paths", {}).get("research_exports")
                or SCRIPT_DIR / "research_exports"
            )
        )
        artifact_path = write_research_run(run, output_dir=directory)
    returned_evidence = [
        _compact_evidence(item, detail) for item in run.get("evidence", [])
    ]
    usage = dict(run.get("usage", {}))
    usage["workbench_api_tokens"] = int(
        diagnostics.get("usage", {}).get("total_tokens", 0)
    )
    usage["api_tokens"] = usage["workbench_api_tokens"]
    usage.setdefault("agent_model_tokens", None)
    usage["planner"] = diagnostics
    usage["rough_returned_evidence_tokens"] = _rough_json_tokens(returned_evidence)
    register_hits = sum(
        int((branch.get("retrieval_debug") or {}).get("navigation_hits", 0) or 0)
        for branch in run.get("branches", [])
    )
    record_protocol_event(
        trace_id,
        "research_run",
        query=query,
        payload={
            "planning_advice": planning_advice,
            "refinement_used": bool(refinement),
            "branch_count": len(run.get("branches", [])),
            "register_consulted": True,
            "register_hits": register_hits,
            "status": run.get("status", {}),
        },
    )
    return _envelope(
        "research_run",
        {
            "index_version": run.get("index_version"),
            "run_id": run.get("run_id"),
            "question": query,
            "research_plan": run.get("research_plan"),
            "planning_advice": planning_advice,
            "status": run.get("status"),
            "synthesis_gate": run.get("synthesis_gate", {}),
            "answer_allowed": bool(
                run.get("synthesis_gate", {}).get("synthesis_allowed")
            ),
            "completion_allowed": False,
            "required_next_action": (
                run.get("synthesis_gate", {}).get("required_next_action")
                or "expand_selected_evidence"
            ),
            "branches": run.get("branches", []),
            "claim_evidence_matrix": run.get("claim_evidence_matrix", []),
            "detail": detail,
            "evidence": returned_evidence,
            "usage": usage,
            "warnings": run.get("warnings", []),
            "artifact_paths": {"json": artifact_path},
        },
    )


def term_probe_agent(
    query: str,
    *,
    refinement: dict | None = None,
    page: int = 1,
    page_size: int = 25,
    language: str | list[str] | None = None,
    work: str | list[str] | None = None,
    version: str | list[str] | None = None,
    text_type: str | None = None,
    export_path: str | Path | None = None,
) -> dict:
    plan = build_query_plan(
        query, glossary=load_glossary(),
        mode="agent_supplied" if refinement else "local",
        refinement=refinement,
    )
    probe = probe_query_plan(
        plan,
        page=page,
        page_size=page_size,
        language=language,
        work=work,
        version=version,
        text_type=text_type,
    )
    payload = {
        "query": query,
        "plan": compact_plan(plan),
        "probe": probe,
        "usage": {"api_tokens": 0},
    }
    if export_path:
        output_path = Path(export_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload["artifact_paths"] = {"json": str(output_path)}
    response = _envelope("term_probe", payload)
    if export_path:
        output_path.write_text(
            json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return response


def register_agent(query: str, *, refinement: dict | None = None,
                   top_k: int = 8, trace_id: str | None = None) -> dict:
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
    record_protocol_event(
        trace_id,
        "register",
        query=query,
        payload={
            "register_consulted": True,
            "register_hits": len(payload.get("hits", [])),
            "refinement_used": bool(refinement),
        },
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
    focus_terms: list[str] | None = None,
    context_terms: list[str] | None = None,
    target_volumes: list[str] | None = None,
    intent: str | None = None,
    trace_id: str | None = None,
) -> dict:
    """Retrieve evidence and return a compact, deterministic agent response."""
    detail = _validate_detail(detail)
    plan_refinement = merge_query_hints(
        plan_refinement,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent,
    )
    if plan_refinement and planner_mode == "local":
        planner_mode = "agent_supplied"
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
    retrieval_debug = package.get("retrieval", {}).get("debug", {})
    query_plan = package.get("query", {}).get("query_plan", {})
    record_protocol_event(
        trace_id,
        "search",
        query=query,
        payload={
            "planning_advice": package.get("query", {}).get("planning_advice", {}),
            "focus_explicit": bool(query_plan.get("focus_explicit")),
            "focus_diagnostics": retrieval_debug.get("focus_diagnostics", {}),
            "refinement_used": bool(plan_refinement),
            "register_consulted": True,
            "register_hits": int(retrieval_debug.get("register_navigation_hits", 0) or 0),
            "qualified_evidence_count": int(
                package.get("summary", {}).get("qualified_evidence_count", 0) or 0
            ),
        },
    )
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
            "artifact_type": package.get("artifact_type"),
            "synthesis_gate": package.get("synthesis_gate", {}),
            "summary": {
                **package["summary"],
                "retrieval_adequacy": package.get("retrieval", {}).get("debug", {}).get("adequacy"),
            },
            "planning_advice": package["query"].get("planning_advice", {}),
            "focus_diagnostics": package.get("retrieval", {}).get("debug", {}).get(
                "focus_diagnostics", {}
            ),
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


def _resolve_evidence_package(reference: str | Path) -> Path:
    """Resolve a package path, research-run ID, or formal session ID."""
    text = str(reference or "").strip()
    if not text:
        raise ValueError("evidence package reference must not be empty")
    direct = Path(text).expanduser()
    if direct.exists():
        return direct.resolve()
    if text.startswith("mrs_"):
        session = get_session(text)
        artifact = str(session.get("source_artifact_path") or "").strip()
        if not artifact:
            raise FileNotFoundError(
                f"research session {text} has no saved evidence artifact"
            )
        resolved = Path(artifact).expanduser().resolve()
        if resolved.exists():
            return resolved
    export_dir = Path(
        CONFIG.get("paths", {}).get("research_exports")
        or SCRIPT_DIR / "research_exports"
    ).expanduser().resolve()
    run_candidate = export_dir / f"research_run_{text}.json"
    if run_candidate.exists():
        return run_candidate
    raise FileNotFoundError(
        f"evidence package not found: {text}; supply a JSON path, run_id, or session_id"
    )


def evidence_from_package(
    package_path: str | Path,
    evidence_ids: list[str] | None = None,
    *,
    detail: str = "full",
    trace_id: str | None = None,
) -> dict:
    """Load selected evidence from a saved research-package or claim-audit JSON file."""
    detail = _validate_detail(detail)
    path = _resolve_evidence_package(package_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("evidence")
    if not isinstance(items, list):
        raise ValueError("package does not contain an evidence list")
    wanted = {str(value).strip().casefold() for value in evidence_ids or [] if str(value).strip()}
    def identities(item: dict) -> set[str]:
        return {
            str(item.get(key) or "").casefold()
            for key in ("evidence_id", "evidence_uid", "package_evidence_ref")
            if str(item.get(key) or "").strip()
        }
    selected = [
        item for item in items
        if not wanted or identities(item).intersection(wanted)
    ]
    found = set().union(*(identities(item) for item in selected)) if selected else set()
    missing = sorted(wanted - found)
    record_protocol_event(
        trace_id,
        "evidence",
        payload={
            "package_path": str(path),
            "requested_count": len(wanted),
            "evidence_expanded": len(selected),
            "missing_count": len(missing),
            "detail": detail,
        },
    )
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


def report_check_agent(report_path: str | Path, source_path: str | Path) -> dict:
    """Validate an agent's structured claims against a saved package/run."""
    report_file = Path(report_path).expanduser().resolve()
    source_file = Path(source_path).expanduser().resolve()
    report = load_json_artifact(report_file)
    source = load_json_artifact(source_file)
    validation = validate_report_claims(report, source)
    return _envelope(
        "report_check",
        {
            "report_path": str(report_file),
            "source_path": str(source_file),
            "validation": validation,
            "usage": {"api_tokens": 0},
        },
    )



def _session_workflow_kind(plan: dict) -> str:
    question_type = str(plan.get("question_type") or "")
    subquestions = list(plan.get("subquestions") or [])
    return (
        "single"
        if question_type == "textual_research" and len(subquestions) == 1
        else "compound"
    )


def _session_requires_author_text(plan: dict) -> bool:
    return any(
        str(item.get("query_intent") or "") == "author_argument"
        and "mega" in (item.get("required_corpus") or [])
        for item in plan.get("subquestions", [])
    )


def _qualified_text_item(item: dict) -> bool:
    provenance = item.get("provenance", {})
    text_type = item.get("text_type") or item.get("locator", {}).get("text_type")
    claim_eligible = provenance.get(
        "claim_eligible",
        item.get(
            "claim_eligible",
            provenance.get("evidence_eligible", item.get("evidence_eligible")),
        ),
    )
    return bool(claim_eligible) and str(text_type or "").upper() == "TEXT"


def _session_refinement_from_hints(
    session: dict,
    *,
    focus_terms: list[str] | None = None,
    context_terms: list[str] | None = None,
    target_volumes: list[str] | None = None,
    intent: str | None = None,
) -> dict | None:
    supplied = bool(focus_terms or context_terms or target_volumes or intent)
    if not supplied:
        return None
    plan = session.get("research_plan") or {}
    mega_subquestions = [
        item
        for item in plan.get("subquestions", [])
        if "mega" in (item.get("required_corpus") or [])
    ]
    if len(mega_subquestions) != 1:
        raise ValueError(
            "lightweight hints are only unambiguous for one MEGA branch; "
            "supply a research-plan refinement JSON for compound research"
        )
    item = mega_subquestions[0]
    query_refinement = merge_query_hints(
        item.get("query_refinement"),
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent,
    )
    if not query_refinement:
        return None
    normalized_focus = list(query_refinement.get("focus_terms", []))
    resolved = []
    if normalized_focus:
        # An explicit focus means the calling agent accepts responsibility for
        # mapping the previously unmapped phrase to this bounded query branch.
        resolved.extend(plan.get("unmapped_concepts", []))
        resolved.extend(normalized_focus)
    return {
        "question_type": plan.get("question_type") or "textual_research",
        "resolved_concepts": list(dict.fromkeys(resolved)),
        "external_concepts": list(plan.get("external_concepts", [])),
        "subquestions": [
            {
                "id": item.get("id"),
                "question": item.get("question") or session["question"],
                "type": item.get("type") or "textual_reconstruction",
                "required_corpus": item.get("required_corpus") or ["mega"],
                "required_evidence": item.get("required_evidence") or "author_text",
                "covered_concepts": list(item.get("covered_concepts", []))
                + normalized_focus,
                "external_concepts": list(item.get("external_concepts", [])),
                "query_refinement": query_refinement,
            }
        ],
    }


def _session_evidence_identity(item: dict) -> list[str]:
    values = [
        str(item.get(key) or "").strip()
        for key in ("evidence_id", "evidence_uid", "package_evidence_ref")
    ]
    return [value for value in values if value]


def _report_evidence_refs(report: dict) -> set[str]:
    refs: set[str] = set()
    for claim in report.get("claims", []) or []:
        refs.update(
            str(value).strip()
            for value in claim.get("evidence_refs", []) or []
            if str(value).strip()
        )
        refs.update(
            str(quote.get("evidence_ref") or "").strip()
            for quote in claim.get("quotes", []) or []
            if str(quote.get("evidence_ref") or "").strip()
        )
    return refs


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_completion_receipt(
    session: dict,
    *,
    report_path: Path,
    source_path: Path,
    validation: dict,
) -> dict:
    expanded = list(session.get("expanded_evidence") or [])
    completed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    citation_ready = sum(bool(item.get("quote_eligible")) for item in expanded)
    receipt = {
        "protocol": "mega-research-completion-receipt-v1",
        "session_id": session["session_id"],
        "question": session["question"],
        "index_version": session.get("index_version"),
        "completed_at": completed_at,
        "source_artifact_path": str(source_path),
        "source_artifact_sha256": _sha256_file(source_path),
        "artifact_revision": session.get("active_revision"),
        "artifact_revision_count": len(session.get("artifact_revisions") or []),
        "report_path": str(report_path),
        "report_sha256": _sha256_file(report_path),
        "expanded_evidence": [
            {
                "evidence_id": item.get("evidence_id"),
                "evidence_uid": item.get("evidence_uid"),
                "package_evidence_ref": item.get("package_evidence_ref"),
            }
            for item in expanded
        ],
        "validation": validation,
        "completion_scope": (
            "citation_ready" if citation_ready else "qualified_evidence_unverified_locator"
        ),
        "warnings": (
            []
            if citation_ready
            else ["Formal quotations still require locator verification."]
        ),
    }
    canonical = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    receipt["receipt_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    path = source_path.parent / f"research_session_{session['session_id']}_receipt.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    receipt["receipt_path"] = str(path)
    return receipt


def research_session_start_agent(
    query: str,
    *,
    refinement: dict | None = None,
    planner_mode: str = "local",
    trace_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Create a formal research workflow; no evidence retrieval occurs yet."""
    planning = research_plan_agent(
        query,
        refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
        trace_id=trace_id,
    )
    plan = planning.get("research_plan") or {}
    advice = planning.get("planning_advice") or {}
    diagnostics = planning.get("planner_diagnostics") or {}
    resolved_by_planner = bool(
        refinement
        or diagnostics.get("effective_mode") in {"hybrid", "promoted_memory", "agent_supplied"}
    )
    needs_refinement = str(advice.get("decision") or "") == "refinement_required" or (
        bool(advice.get("should_refine")) and not resolved_by_planner
    )
    state = NEEDS_REFINEMENT if needs_refinement else PLANNED
    session = create_session(
        query,
        workflow_kind=_session_workflow_kind(plan),
        state=state,
        index_version=str(get_current_version() or ""),
        payload={
            "planner_mode": planner_mode,
            "research_plan": plan,
            "planning_advice": advice,
            "planner_diagnostics": diagnostics,
            "source_artifact_path": None,
            "retrieval_summary": None,
            "expanded_evidence": [],
            "artifact_revisions": [],
            "active_revision": 0,
            "completion_receipt": None,
        },
        db_path=db_path,
    )
    return _envelope(
        "research_session_start",
        {
            "session": public_session(session),
            "usage": planning.get("usage", {"api_tokens": 0}),
        },
    )


def research_session_continue_agent(
    session_id: str,
    *,
    refinement: dict | None = None,
    focus_terms: list[str] | None = None,
    context_terms: list[str] | None = None,
    target_volumes: list[str] | None = None,
    intent: str | None = None,
    planner_mode: str = "local",
    top_k_per_branch: int = 5,
    max_evidence: int = 12,
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Retrieve within a formal session and evaluate the evidence gate."""
    session = get_session(session_id, db_path=db_path)
    if session["state"] == COMPLETE:
        raise ValueError("completed research sessions are immutable")
    if session["state"] not in {
        PLANNED, NEEDS_REFINEMENT, RETRIEVAL_GAP,
        EVIDENCE_QUALIFIED, EVIDENCE_EXPANDED,
    }:
        raise ValueError(f"session cannot retrieve from state {session['state']}")
    effective_refinement = refinement or _session_refinement_from_hints(
        session,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent,
    )
    if (
        session["state"] == NEEDS_REFINEMENT
        and not effective_refinement
        and planner_mode == "local"
    ):
        return _envelope(
            "research_session_continue",
            {
                "blocked": True,
                "reason": "semantic_refinement_required_before_retrieval",
                "session": public_session(session),
                "usage": {"api_tokens": 0},
            },
        )
    run = research_run_agent(
        session["question"],
        top_k_per_branch=top_k_per_branch,
        max_evidence=max_evidence,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
        detail="index",
        save=True,
        output_dir=output_dir,
        refinement=effective_refinement,
        planner_mode=("agent_supplied" if effective_refinement else planner_mode),
        trace_id=session_id,
    )
    artifact_path = str((run.get("artifact_paths") or {}).get("json") or "")
    if not artifact_path:
        raise RuntimeError("formal research retrieval did not produce a saved artifact")
    evidence = list(run.get("evidence") or [])
    qualified = [item for item in evidence if item.get("evidence_eligible")]
    qualified_text = [item for item in evidence if _qualified_text_item(item)]
    claim_eligible = [
        item for item in evidence
        if item.get("claim_eligible", item.get("evidence_eligible"))
    ]
    claim_ready = [item for item in evidence if item.get("claim_ready")]
    advice = run.get("planning_advice") or {}
    gate = run.get("synthesis_gate") or {}
    refinement_resolved = bool(
        effective_refinement
        or (run.get("usage", {}).get("planner") or {}).get("effective_mode")
        in {"hybrid", "promoted_memory", "agent_supplied"}
    )
    plan_still_blocked = (
        str(advice.get("decision") or "") == "refinement_required"
        or (bool(advice.get("should_refine")) and not refinement_resolved)
    )
    revision_history = list(session.get("artifact_revisions") or [])
    revision_number = (
        max((int(item.get("revision") or 0) for item in revision_history), default=0)
        + 1
    )
    refinement_payload = effective_refinement or {}
    refinement_hash = (
        hashlib.sha256(
            json.dumps(
                refinement_payload, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()[:16]
        if refinement_payload else None
    )
    revision = {
        "revision": revision_number,
        "parent_revision": session.get("active_revision") or None,
        "run_id": run.get("run_id"),
        "artifact_path": artifact_path,
        "refinement_sha256": refinement_hash,
        "status": run.get("status"),
        "synthesis_gate": gate,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    retrieved = transition_session(
        session_id,
        RETRIEVED,
        event_type="retrieval_completed",
        updates={
            "research_plan": run.get("research_plan") or session.get("research_plan"),
            "planning_advice": advice,
            "source_artifact_path": artifact_path,
            "retrieval_summary": {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "synthesis_gate": gate,
                "evidence_count": len(evidence),
                "qualified_evidence_count": len(qualified),
                "qualified_text_evidence_count": len(qualified_text),
                "claim_eligible_count": len(claim_eligible),
                "claim_ready_count": len(claim_ready),
                "revision": revision_number,
            },
            "expanded_evidence": [],
            "artifact_revisions": revision_history + [revision],
            "active_revision": revision_number,
            "completion_receipt": None,
        },
        event_payload={
            "artifact_path": artifact_path,
            "revision": revision_number,
            "refinement_sha256": refinement_hash,
            "qualified_evidence_count": len(qualified),
            "qualified_text_evidence_count": len(qualified_text),
        },
        expected_states={session["state"]},
        db_path=db_path,
    )
    if plan_still_blocked:
        final_state = NEEDS_REFINEMENT
        reason = "planning_advice_unresolved"
    elif gate.get("synthesis_allowed") and claim_eligible:
        final_state = EVIDENCE_QUALIFIED
        reason = "evidence_gate_passed"
    else:
        final_state = RETRIEVAL_GAP
        reason = "evidence_gate_closed"
    session = transition_session(
        session_id,
        final_state,
        event_type="retrieval_gate_evaluated",
        event_payload={
            "reason": reason,
            "gate_errors": list(gate.get("errors", [])),
            "gate_warnings": list(gate.get("warnings", [])),
        },
        expected_states={retrieved["state"]},
        db_path=db_path,
    )
    return _envelope(
        "research_session_continue",
        {
            "blocked": final_state != EVIDENCE_QUALIFIED,
            "session": public_session(session),
            "candidate_evidence": evidence,
            "synthesis_gate": gate,
            "artifact_paths": run.get("artifact_paths"),
            "usage": run.get("usage", {"api_tokens": 0}),
        },
    )


def research_session_expand_agent(
    session_id: str,
    evidence_ids: list[str],
    *,
    detail: str = "full",
    db_path: str | Path | None = None,
) -> dict:
    """Expand selected qualified evidence and persist the selection boundary."""
    session = get_session(session_id, db_path=db_path)
    if session["state"] not in {EVIDENCE_QUALIFIED, EVIDENCE_EXPANDED}:
        raise ValueError(f"session cannot expand evidence from state {session['state']}")
    requested = [str(value).strip() for value in evidence_ids if str(value).strip()]
    if not requested:
        raise ValueError("formal evidence expansion requires at least one evidence ID")
    source_path = session.get("source_artifact_path")
    if not source_path:
        raise RuntimeError("research session has no saved evidence artifact")
    expanded = evidence_from_package(
        source_path, requested, detail=detail, trace_id=session_id
    )
    if expanded.get("missing_ids"):
        raise ValueError(
            "unknown evidence IDs: " + ", ".join(expanded["missing_ids"])
        )
    items = list(expanded.get("evidence") or [])
    if not items:
        raise ValueError("no evidence was expanded")
    if not any(
        bool(
            item.get("provenance", {}).get(
                "claim_eligible",
                item.get("claim_eligible", item.get("evidence_eligible")),
            )
        )
        for item in items
    ):
        raise ValueError("formal expansion requires at least one qualified evidence item")
    existing = {
        (item.get("evidence_uid") or item.get("package_evidence_ref") or item.get("evidence_id")): item
        for item in session.get("expanded_evidence", [])
    }
    for item in items:
        key = item.get("evidence_uid") or item.get("package_evidence_ref") or item.get("evidence_id")
        existing[key] = {
            "evidence_id": item.get("evidence_id"),
            "evidence_uid": item.get("evidence_uid"),
            "package_evidence_ref": item.get("package_evidence_ref"),
            "identities": _session_evidence_identity(item),
            "evidence_eligible": bool(
                item.get("provenance", {}).get("evidence_eligible", item.get("evidence_eligible"))
            ),
            "semantic_ready": bool(item.get("provenance", {}).get("semantic_ready")),
            "scope_ready": bool(item.get("provenance", {}).get("scope_ready")),
            "provenance_ready": bool(item.get("provenance", {}).get("provenance_ready")),
            "claim_eligible": bool(
                item.get("provenance", {}).get(
                    "claim_eligible", item.get("evidence_eligible")
                )
            ),
            "claim_ready": bool(item.get("provenance", {}).get("claim_ready")),
            "branch_ids": list(item.get("research", {}).get("branch_ids", [])),
            "claim_types": list(item.get("research", {}).get("claim_types", [])),
            "verified_author_text": bool(
                item.get("provenance", {}).get("verified_author_text", item.get("verified_author_text"))
            ),
            "text_type": item.get("locator", {}).get("text_type") or item.get("text_type"),
            "quote_eligible": bool(
                item.get("evidence", {}).get("quote_eligible", item.get("quote_eligible"))
            ),
        }
    session = transition_session(
        session_id,
        EVIDENCE_EXPANDED,
        event_type="evidence_expanded",
        updates={"expanded_evidence": list(existing.values())},
        event_payload={
            "requested_ids": requested,
            "expanded_count": len(items),
            "expanded_branch_ids": sorted({
                branch_id
                for item in items
                for branch_id in item.get("research", {}).get("branch_ids", [])
            }),
        },
        expected_states={session["state"]},
        db_path=db_path,
    )
    return _envelope(
        "research_session_expand",
        {
            "session": public_session(session, include_plan=False),
            "evidence": items,
            "usage": {"api_tokens": 0},
        },
    )


def research_session_finalize_agent(
    session_id: str,
    report_path: str | Path,
    *,
    db_path: str | Path | None = None,
) -> dict:
    """Validate a report against expanded evidence and issue a completion receipt."""
    session = get_session(session_id, db_path=db_path)
    if session["state"] != EVIDENCE_EXPANDED:
        raise ValueError(f"session cannot finalize from state {session['state']}")
    source_path = Path(str(session.get("source_artifact_path") or "")).expanduser().resolve()
    report_file = Path(report_path).expanduser().resolve()
    checked = report_check_agent(report_file, source_path)
    validation = dict(checked.get("validation") or {})
    errors = list(validation.get("errors", []))
    warnings = list(validation.get("warnings", []))
    source_payload = load_json_artifact(source_path)
    report = load_json_artifact(report_file)
    report_refs = _report_evidence_refs(report)
    for position, claim in enumerate(report.get("claims", []) or [], start=1):
        claim_id = str(claim.get("claim_id") or f"claim_{position}")
        claim_refs = {
            str(value).strip()
            for value in claim.get("evidence_refs", []) or []
            if str(value).strip()
        }
        claim_refs.update(
            str(quote.get("evidence_ref") or "").strip()
            for quote in claim.get("quotes", []) or []
            if str(quote.get("evidence_ref") or "").strip()
        )
        if (
            str(claim.get("claim_type") or "") != "empirical_hypothesis"
            and not claim_refs
        ):
            errors.append(f"formal_claim_without_evidence:{claim_id}")
    expanded = list(session.get("expanded_evidence") or [])
    expanded_refs = {
        identity.casefold()
        for item in expanded
        for identity in item.get("identities", [])
        if identity
    }
    source_evidence = {
        str(item.get("evidence_id") or ""): item
        for item in source_payload.get("evidence", [])
        if str(item.get("evidence_id") or "").strip()
    }
    required_branch_ids = []
    expanded_branch_gaps = []
    report_branch_gaps = []
    report_refs_folded = {ref.casefold() for ref in report_refs}
    for row in source_payload.get("claim_evidence_matrix", []) or []:
        branch_id = str(row.get("subquestion_id") or "").strip()
        if not branch_id or str(row.get("claim_type") or "") == "empirical_hypothesis":
            continue
        if str(row.get("status") or "") not in {"adequate", "partial"}:
            continue
        required_branch_ids.append(branch_id)
        branch_refs = set()
        for evidence_id in row.get("evidence_ids", []) or []:
            item = source_evidence.get(str(evidence_id))
            if item:
                branch_refs.update(
                    identity.casefold()
                    for identity in _session_evidence_identity(item)
                )
        if not branch_refs:
            errors.append(f"branch_without_evidence_candidates:{branch_id}")
            continue
        if not branch_refs.intersection(expanded_refs):
            expanded_branch_gaps.append(branch_id)
            errors.append(f"branch_without_expanded_evidence:{branch_id}")
        if not branch_refs.intersection(report_refs_folded):
            report_branch_gaps.append(branch_id)
            errors.append(f"report_omits_required_branch:{branch_id}")
    unexpanded = sorted(
        ref for ref in report_refs if ref.casefold() not in expanded_refs
    )
    if not report_refs:
        errors.append("report_requires_expanded_evidence_refs")
    if unexpanded:
        errors.extend(f"report_uses_unexpanded_evidence:{ref}" for ref in unexpanded)
    if _session_requires_author_text(session.get("research_plan") or {}) and not any(
        item.get("claim_eligible", item.get("evidence_eligible"))
        and str(item.get("text_type") or "").upper() == "TEXT"
        for item in expanded
    ):
        errors.append("author_argument_requires_expanded_text_evidence")
    validation.update(
        {
            "valid": not errors,
            "errors": list(dict.fromkeys(errors)),
            "warnings": list(dict.fromkeys(warnings)),
            "expanded_evidence_count": len(expanded),
            "report_evidence_ref_count": len(report_refs),
            "required_branch_count": len(set(required_branch_ids)),
            "expanded_branch_gaps": sorted(set(expanded_branch_gaps)),
            "report_branch_gaps": sorted(set(report_branch_gaps)),
        }
    )
    if not validation["valid"]:
        append_session_event(
            session_id,
            "report_validation_failed",
            payload={"validation": validation},
            db_path=db_path,
        )
        return _envelope(
            "research_session_finalize",
            {
                "completion_allowed": False,
                "validation": validation,
                "session": public_session(
                    get_session(session_id, db_path=db_path), include_plan=False
                ),
                "usage": {"api_tokens": 0},
            },
        )
    session = transition_session(
        session_id,
        REPORT_VALIDATED,
        event_type="report_validated",
        updates={"report_validation": validation, "report_path": str(report_file)},
        event_payload={"report_path": str(report_file)},
        expected_states={EVIDENCE_EXPANDED},
        db_path=db_path,
    )
    receipt = _write_completion_receipt(
        session,
        report_path=report_file,
        source_path=source_path,
        validation=validation,
    )
    session = transition_session(
        session_id,
        COMPLETE,
        event_type="completion_receipt_issued",
        updates={"completion_receipt": receipt},
        event_payload={"receipt_id": receipt["receipt_id"]},
        expected_states={REPORT_VALIDATED},
        db_path=db_path,
    )
    return _envelope(
        "research_session_finalize",
        {
            "completion_allowed": True,
            "validation": validation,
            "completion_receipt": receipt,
            "session": public_session(session, include_plan=False),
            "usage": {"api_tokens": 0},
        },
    )


def research_session_status_agent(
    session_id: str,
    *,
    include_events: bool = True,
    db_path: str | Path | None = None,
) -> dict:
    """Return current formal workflow state and its required next action."""
    session = get_session(session_id, db_path=db_path)
    return _envelope(
        "research_session_status",
        {
            "session": public_session(session),
            "events": (
                session_events(session_id, db_path=db_path) if include_events else []
            ),
            "usage": {"api_tokens": 0},
        },
    )

def protocol_report_agent(trace_id: str) -> dict:
    """Return a non-blocking audit of one agent research trace."""
    return _envelope(
        "protocol_report",
        {
            "report": build_protocol_report(trace_id),
            "usage": {"api_tokens": 0},
        },
    )


def source_catalog_build_agent(manifest_path: str | None = None) -> dict:
    """Build derived catalog tables without changing chunks, FTS, or vectors."""
    return _envelope(
        "source_catalog_build",
        {
            "source_catalog": build_source_catalog(
                CONFIG["paths"]["metadata_db"], manifest_path
            ),
            "usage": {"api_tokens": 0},
        },
    )


def source_catalog_status_agent() -> dict:
    """Return source identity coverage and relation counts."""
    return _envelope(
        "source_catalog_status",
        {
            "source_catalog": source_catalog_status(
                CONFIG["paths"]["metadata_db"]
            ),
            "usage": {"api_tokens": 0},
        },
    )


def source_catalog_list_agent(
    *,
    abteilung: str | None = None,
    band: str | None = None,
    collection: str | None = None,
    text_type: str | None = None,
    language: str | list[str] | None = None,
    work: str | list[str] | None = None,
    version: str | list[str] | None = None,
    limit: int = 50,
    page: int = 1,
    page_size: int | None = None,
) -> dict:
    """List bounded source units for an agent or operator."""
    return _envelope(
        "source_catalog_list",
        {
            "source_catalog": list_source_documents(
                CONFIG["paths"]["metadata_db"],
                abteilung=abteilung,
                band=band,
                collection=collection,
                text_type=text_type,
                language=language,
                work=work,
                version=version,
                limit=limit,
                page=page,
                page_size=page_size,
            ),
            "usage": {"api_tokens": 0},
        },
    )


def source_catalog_coverage_agent(
    *,
    abteilung: str | None = None,
    band: str | None = None,
    collection: str | None = None,
    text_type: str | None = None,
    language: str | list[str] | None = None,
    work: str | list[str] | None = None,
    version: str | list[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """Return evidence-labelled coverage without inferring completeness."""
    return _envelope(
        "source_catalog_coverage",
        {
            "coverage": source_catalog_coverage(
                CONFIG["paths"]["metadata_db"],
                abteilung=abteilung,
                band=band,
                collection=collection,
                text_type=text_type,
                language=language,
                work=work,
                version=version,
                page=page,
                page_size=page_size,
            ),
            "usage": {"api_tokens": 0},
        },
    )


def source_catalog_show_agent(source_id: str) -> dict:
    """Inspect one source and its edition-level navigation relations."""
    context = get_source_context(source_id, CONFIG["paths"]["metadata_db"])
    if context is None:
        raise KeyError(f"source catalog entry not found: {source_id}")
    return _envelope(
        "source_catalog_show",
        {"source_catalog": context, "usage": {"api_tokens": 0}},
    )


def planning_memory_status_agent() -> dict:
    """Return counts without exposing or mutating planning payloads."""
    return _envelope(
        "planning_memory_status",
        {"memory": planning_memory_status(), "usage": {"api_tokens": 0}},
    )


def planning_memory_list_agent(
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    """List reviewable planning candidates; no status is changed."""
    return _envelope(
        "planning_memory_list",
        {
            "memory": list_memory(kind=kind, status=status, limit=limit),
            "usage": {"api_tokens": 0},
        },
    )


def planning_memory_validate_agent(memory_id: str) -> dict:
    """Validate proposed exact terms against indexed MEGA TEXT records."""
    return _envelope(
        "planning_memory_validate",
        {
            "memory": validate_against_corpus(memory_id),
            "usage": {"api_tokens": 0},
        },
    )


def planning_memory_review_agent(
    memory_id: str,
    action: str,
    *,
    note: str,
    reviewer: str = "user",
) -> dict:
    """Perform an explicit promotion or rejection after review."""
    return _envelope(
        "planning_memory_review",
        {
            "memory": review_memory(
                memory_id, action, note=note, reviewer=reviewer
            ),
            "usage": {"api_tokens": 0},
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
            "source_catalog": source_catalog_status(db_path),
            "counts": counts,
            "sources": sources,
            "vector_db_exists": Path(CONFIG["paths"]["vector_db"]).exists(),
        },
    )
