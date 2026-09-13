#!/usr/bin/env python3
"""Optional stdio MCP server for the MEGA research workbench."""
from __future__ import annotations

import json
import sys

from agent_service import (
    capabilities, evidence_from_package, plan_agent, planning_memory_list_agent,
    planning_memory_status_agent, planning_memory_validate_agent,
    protocol_report_agent, register_agent, report_check_agent, search_agent,
    source_catalog_coverage_agent, source_catalog_list_agent, source_catalog_show_agent,
    source_catalog_status_agent, status_agent,
    research_plan_agent, research_run_agent,
    research_session_continue_agent, research_session_expand_agent,
    research_session_finalize_agent, research_session_start_agent,
    research_session_status_agent,
    term_probe_agent, verify_agent,
)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional integration dependency
    print(
        "The optional 'mcp' package is not installed. Use mega_agent.py or install "
        "requirements-mcp.txt into the D: environment.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


mcp = FastMCP("MEGA Research Workbench")


@mcp.tool()
def mega_capabilities() -> dict:
    """Describe the local MEGA agent protocol and available operations."""
    return capabilities()


@mcp.tool()
def mega_status() -> dict:
    """Return a fast read-only snapshot of local index readiness."""
    return status_agent()


@mcp.tool()
def mega_plan(query: str, refinement_json: str = "", planner_mode: str = "local",
              include_probe: bool = True, include_register: bool = True,
              focus_terms: list[str] | None = None,
              context_terms: list[str] | None = None,
              target_volumes: list[str] | None = None,
              intent: str = "", trace_id: str = "") -> dict:
    """Build a zero-token QueryPlan; agents may supply a strict JSON refinement."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return plan_agent(
        query, refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
        include_probe=include_probe,
        include_register=include_register,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent or None,
        trace_id=trace_id or None,
    )


@mcp.tool()
def mega_research_plan(
    query: str,
    refinement_json: str = "",
    planner_mode: str = "local",
    trace_id: str = "",
) -> dict:
    """Decompose a compound question into evidence-bounded research branches."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return research_plan_agent(
        query,
        refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
        trace_id=trace_id or None,
    )


@mcp.tool()
def mega_research_run(
    query: str,
    top_k_per_branch: int = 5,
    max_evidence: int = 12,
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    detail: str = "index",
    save: bool = False,
    refinement_json: str = "",
    planner_mode: str = "local",
    trace_id: str = "",
) -> dict:
    """Retrieve all required MEGA branches and expose unresolved external evidence."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return research_run_agent(
        query,
        top_k_per_branch=top_k_per_branch,
        max_evidence=max_evidence,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
        detail=detail,
        save=save,
        refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
        trace_id=trace_id or None,
    )


@mcp.tool()
def mega_source_catalog_status() -> dict:
    """Return source identity coverage and relation counts."""
    return source_catalog_status_agent()


@mcp.tool()
def mega_source_catalog_list(
    abteilung: str = "",
    band: str = "",
    collection: str = "",
    text_type: str = "",
    limit: int = 30,
    language: str = "",
    work: str = "",
    version: str = "",
    page: int = 1,
    page_size: int = 0,
) -> dict:
    """List bounded source units; use exact filters to keep payloads small."""
    return source_catalog_list_agent(
        abteilung=abteilung or None,
        band=band or None,
        collection=collection or None,
        text_type=text_type or None,
        language=language or None,
        work=work or None,
        version=version or None,
        limit=limit,
        page=page,
        page_size=page_size or None,
    )


@mcp.tool()
def mega_source_catalog_coverage(
    abteilung: str = "",
    band: str = "",
    collection: str = "",
    text_type: str = "",
    language: str = "",
    work: str = "",
    version: str = "",
    page: int = 1,
    page_size: int = 30,
) -> dict:
    """Show bounded work/version/language/TEXT-APPARAT coverage evidence."""
    return source_catalog_coverage_agent(
        abteilung=abteilung or None,
        band=band or None,
        collection=collection or None,
        text_type=text_type or None,
        language=language or None,
        work=work or None,
        version=version or None,
        page=page,
        page_size=page_size,
    )


@mcp.tool()
def mega_source_catalog_show(source_id: str) -> dict:
    """Inspect one source and its source-level textual-history relations."""
    return source_catalog_show_agent(source_id)


@mcp.tool()
def mega_memory_status() -> dict:
    """Return review-state counts for persistent planning memory."""
    return planning_memory_status_agent()


@mcp.tool()
def mega_memory_list(
    kind: str = "",
    status: str = "",
    limit: int = 50,
) -> dict:
    """List candidates; promotion remains a human-only CLI operation."""
    return planning_memory_list_agent(
        kind=kind or None, status=status or None, limit=limit
    )


@mcp.tool()
def mega_memory_validate(memory_id: str) -> dict:
    """Check candidate terms against local MEGA TEXT before human review."""
    return planning_memory_validate_agent(memory_id)


@mcp.tool()
def mega_term_probe(
    query: str,
    refinement_json: str = "",
    page: int = 1,
    page_size: int = 25,
    language: str = "",
    work: str = "",
    version: str = "",
    text_type: str = "",
    export_path: str = "",
) -> dict:
    """Separate exact, lexical-variant, and related term matches with paging."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return term_probe_agent(
        query,
        refinement=refinement,
        page=page,
        page_size=page_size,
        language=language or None,
        work=work or None,
        version=version or None,
        text_type=text_type or None,
        export_path=export_path or None,
    )


@mcp.tool()
def mega_register(query: str, top_k: int = 8,
                  refinement_json: str = "", trace_id: str = "") -> dict:
    """Search Sachregister navigation; returned entries are not author evidence."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return register_agent(
        query, refinement=refinement, top_k=top_k, trace_id=trace_id or None
    )


@mcp.tool()
def mega_search(
    query: str,
    route: str = "all",
    top_k: int = 8,
    detail: str = "index",
    save: bool = False,
    planner_mode: str = "local",
    refinement_json: str = "",
    focus_terms: list[str] | None = None,
    context_terms: list[str] | None = None,
    target_volumes: list[str] | None = None,
    intent: str = "",
    trace_id: str = "",
) -> dict:
    """Retrieve MEGA evidence locally; no external language model is called."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return search_agent(
        query,
        route=route,
        top_k=top_k,
        detail=detail,
        save=save,
        planner_mode="agent_supplied" if refinement else planner_mode,
        plan_refinement=refinement,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent or None,
        trace_id=trace_id or None,
    )


@mcp.tool()
def mega_evidence(
    package_path: str,
    evidence_ids: list[str] | None = None,
    detail: str = "full",
    trace_id: str = "",
) -> dict:
    """Expand selected evidence only after compact index-level selection."""
    return evidence_from_package(
        package_path,
        evidence_ids or [],
        detail=detail,
        trace_id=trace_id or None,
    )


@mcp.tool()
def mega_research_session_start(
    query: str,
    refinement_json: str = "",
    planner_mode: str = "local",
) -> dict:
    """Start a persistent formal workflow; returns the required next action."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return research_session_start_agent(
        query,
        refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
    )


@mcp.tool()
def mega_research_session_continue(
    session_id: str,
    refinement_json: str = "",
    focus_terms: list[str] | None = None,
    context_terms: list[str] | None = None,
    target_volumes: list[str] | None = None,
    intent: str = "",
    planner_mode: str = "local",
    top_k_per_branch: int = 5,
    max_evidence: int = 12,
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
) -> dict:
    """Retrieve or repair evidence inside a formal research session."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return research_session_continue_agent(
        session_id,
        refinement=refinement,
        focus_terms=focus_terms,
        context_terms=context_terms,
        target_volumes=target_volumes,
        intent=intent or None,
        planner_mode="agent_supplied" if refinement else planner_mode,
        top_k_per_branch=top_k_per_branch,
        max_evidence=max_evidence,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
    )


@mcp.tool()
def mega_research_session_expand(
    session_id: str,
    evidence_ids: list[str],
    detail: str = "full",
) -> dict:
    """Expand evidence selected for formal claims and record that boundary."""
    return research_session_expand_agent(
        session_id, evidence_ids, detail=detail
    )


@mcp.tool()
def mega_research_session_finalize(
    session_id: str,
    report_path: str,
) -> dict:
    """Validate expanded-evidence claims and issue a completion receipt."""
    return research_session_finalize_agent(session_id, report_path)


@mcp.tool()
def mega_research_session_status(
    session_id: str,
    include_events: bool = True,
) -> dict:
    """Return formal workflow state, event history, and required next action."""
    return research_session_status_agent(
        session_id, include_events=include_events
    )


@mcp.tool()
def mega_protocol_report(trace_id: str) -> dict:
    """Audit a research trace and return advisory process warnings."""
    return protocol_report_agent(trace_id)


@mcp.tool()
def mega_report_check(report_path: str, source_path: str) -> dict:
    """Validate structured claims and quotations against a saved evidence artifact."""
    return report_check_agent(report_path, source_path)


@mcp.tool()
def mega_verify(
    idea: str,
    budget: str = "brief",
    detail: str = "index",
    local_only: bool = False,
    use_pro: bool = False,
    save: bool = False,
) -> dict:
    """Audit an interpretation with calibrated MEGA evidence and optional models."""
    return verify_agent(
        idea,
        budget=budget,
        detail=detail,
        use_flash=not local_only,
        use_pro=use_pro,
        save=save,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
