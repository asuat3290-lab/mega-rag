#!/usr/bin/env python3
"""Optional stdio MCP server for the MEGA research workbench."""
from __future__ import annotations

import json
import sys

from agent_service import (
    capabilities, plan_agent, register_agent, report_check_agent, search_agent,
    status_agent, research_plan_agent, research_run_agent,
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
              include_probe: bool = True, include_register: bool = True) -> dict:
    """Build a zero-token QueryPlan; agents may supply a strict JSON refinement."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return plan_agent(
        query, refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
        include_probe=include_probe,
        include_register=include_register,
    )


@mcp.tool()
def mega_research_plan(
    query: str,
    refinement_json: str = "",
    planner_mode: str = "local",
) -> dict:
    """Decompose a compound question into evidence-bounded research branches."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return research_plan_agent(
        query,
        refinement=refinement,
        planner_mode="agent_supplied" if refinement else planner_mode,
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
    )


@mcp.tool()
def mega_term_probe(query: str, refinement_json: str = "") -> dict:
    """Count planned German terms by MEGA volume and text layer."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return term_probe_agent(query, refinement=refinement)


@mcp.tool()
def mega_register(query: str, top_k: int = 8,
                  refinement_json: str = "") -> dict:
    """Search Sachregister navigation; returned entries are not author evidence."""
    refinement = json.loads(refinement_json) if refinement_json else None
    return register_agent(query, refinement=refinement, top_k=top_k)


@mcp.tool()
def mega_search(
    query: str,
    route: str = "all",
    top_k: int = 8,
    detail: str = "index",
    save: bool = False,
    planner_mode: str = "local",
    refinement_json: str = "",
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
    )


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
