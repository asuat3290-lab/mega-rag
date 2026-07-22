#!/usr/bin/env python3
"""JSON-only CLI for token-efficient access to the MEGA research workbench."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent_service import (
    AGENT_PROTOCOL,
    capabilities,
    evidence_from_package,
    plan_agent,
    report_check_agent,
    research_plan_agent,
    research_run_agent,
    register_agent,
    search_agent,
    status_agent,
    term_probe_agent,
    verify_agent,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="describe supported operations")
    subparsers.add_parser("status", help="show a fast index status snapshot")

    plan = subparsers.add_parser("plan", help="build a zero-token inspectable query plan")
    plan.add_argument("query")
    plan.add_argument("--refinement-file")
    plan.add_argument("--hybrid", action="store_true", help="use bounded Flash plan refinement")
    plan.add_argument("--no-probe", action="store_true")
    plan.add_argument("--no-register", action="store_true")

    research_plan = subparsers.add_parser(
        "research-plan", help="decompose a compound question by evidence requirement"
    )
    research_plan.add_argument("query")
    research_plan.add_argument("--refinement-file")
    research_plan.add_argument("--hybrid", action="store_true")

    research_run = subparsers.add_parser(
        "research-run", help="retrieve all required MEGA branches"
    )
    research_run.add_argument("query")
    research_run.add_argument("--top-k-per-branch", type=int, default=5)
    research_run.add_argument("--max-evidence", type=int, default=12)
    research_run.add_argument(
        "--retrieval-mode",
        choices=("original_first", "apparat_first", "balanced", "philology"),
        default="original_first",
    )
    research_run.add_argument("--rerank", choices=("none", "rule", "bge"), default="rule")
    research_run.add_argument("--detail", choices=("index", "snippet", "full"), default="index")
    research_run.add_argument("--save", action="store_true")
    research_run.add_argument("--output-dir")
    research_run.add_argument("--refinement-file")
    research_run.add_argument("--hybrid", action="store_true")

    probe = subparsers.add_parser("term-probe", help="count exact planned terms")
    probe.add_argument("query")
    probe.add_argument("--refinement-file")

    register = subparsers.add_parser("register", help="query Sachregister navigation")
    register.add_argument("query")
    register.add_argument("--top-k", type=int, default=8)
    register.add_argument("--refinement-file")

    search = subparsers.add_parser("search", help="retrieve evidence without API models")
    search.add_argument("query")
    search.add_argument("--route", choices=("all", "main_text", "apparat"), default="all")
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument(
        "--retrieval-mode",
        choices=("original_first", "apparat_first", "balanced", "philology"),
        default="balanced",
    )
    search.add_argument("--rerank", choices=("none", "rule", "bge"), default="rule")
    search.add_argument("--detail", choices=("index", "snippet", "full"), default="index")
    search.add_argument("--save", action="store_true")
    search.add_argument("--output-dir")
    search.add_argument("--refinement-file")
    search.add_argument("--hybrid", action="store_true", help="use bounded Flash plan refinement")

    verify = subparsers.add_parser("verify", help="audit an idea against MEGA evidence")
    verify.add_argument("idea")
    verify.add_argument("--budget", choices=("brief", "standard", "deep"), default="brief")
    verify.add_argument("--route", choices=("all", "main_text", "apparat"), default="main_text")
    verify.add_argument(
        "--retrieval-mode",
        choices=("original_first", "apparat_first", "balanced", "philology"),
        default="original_first",
    )
    verify.add_argument("--rerank", choices=("none", "rule", "bge"), default="rule")
    verify.add_argument("--detail", choices=("index", "snippet", "full"), default="index")
    verify.add_argument("--local-only", action="store_true", help="skip Flash and return unjudged candidates")
    verify.add_argument("--pro", action="store_true")
    verify.add_argument("--no-cache", action="store_true")
    verify.add_argument("--save", action="store_true")
    verify.add_argument("--output-dir")

    report_check = subparsers.add_parser(
        "report-check", help="validate structured report claims against saved evidence"
    )
    report_check.add_argument("report")
    report_check.add_argument("source")

    evidence = subparsers.add_parser("evidence", help="expand evidence from a saved JSON package")
    evidence.add_argument("package")
    evidence.add_argument("ids", nargs="*")
    evidence.add_argument("--detail", choices=("index", "snippet", "full"), default="full")
    return parser


def _load_refinement(path: str | None) -> dict | None:
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("refinement file must contain one JSON object")
    return payload

def _dispatch(args: argparse.Namespace) -> dict:
    if args.command == "capabilities":
        return capabilities()
    if args.command == "status":
        return status_agent()
    if args.command == "plan":
        refinement = _load_refinement(args.refinement_file)
        return plan_agent(
            args.query, refinement=refinement,
            planner_mode="agent_supplied" if refinement else ("hybrid" if args.hybrid else "local"),
            include_probe=not args.no_probe,
            include_register=not args.no_register,
        )
    if args.command == "research-plan":
        refinement = _load_refinement(args.refinement_file)
        return research_plan_agent(
            args.query,
            refinement=refinement,
            planner_mode=(
                "agent_supplied" if refinement
                else ("hybrid" if args.hybrid else "local")
            ),
        )
    if args.command == "research-run":
        refinement = _load_refinement(args.refinement_file)
        return research_run_agent(
            args.query,
            top_k_per_branch=args.top_k_per_branch,
            max_evidence=args.max_evidence,
            retrieval_mode=args.retrieval_mode,
            rerank_method=args.rerank,
            detail=args.detail,
            save=args.save,
            output_dir=args.output_dir,
            refinement=refinement,
            planner_mode=(
                "agent_supplied" if refinement
                else ("hybrid" if args.hybrid else "local")
            ),
        )

    if args.command == "term-probe":
        return term_probe_agent(
            args.query, refinement=_load_refinement(args.refinement_file)
        )
    if args.command == "register":
        return register_agent(
            args.query, refinement=_load_refinement(args.refinement_file),
            top_k=args.top_k,
        )
    if args.command == "search":
        return search_agent(
            args.query,
            route=args.route,
            top_k=args.top_k,
            retrieval_mode=args.retrieval_mode,
            rerank_method=args.rerank,
            detail=args.detail,
            save=args.save,
            output_dir=args.output_dir,
            planner_mode=("agent_supplied" if args.refinement_file
                          else ("hybrid" if args.hybrid else "local")),
            plan_refinement=_load_refinement(args.refinement_file),
        )
    if args.command == "verify":
        return verify_agent(
            args.idea,
            budget=args.budget,
            route=args.route,
            retrieval_mode=args.retrieval_mode,
            rerank_method=args.rerank,
            detail=args.detail,
            use_flash=not args.local_only,
            use_pro=args.pro,
            use_cache=not args.no_cache,
            save=args.save,
            output_dir=args.output_dir,
            progress=lambda message: print(message, file=sys.stderr),
        )
    if args.command == "report-check":
        return report_check_agent(args.report, args.source)
    if args.command == "evidence":
        return evidence_from_package(args.package, args.ids, detail=args.detail)
    raise ValueError(f"unsupported command: {args.command}")


def main() -> int:
    args = _parser().parse_args()
    try:
        # Existing retrieval helpers may log to stdout. Keep the machine contract clean.
        with contextlib.redirect_stdout(sys.stderr):
            output = _dispatch(args)
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        if output.get("operation") == "report_check":
            return 0 if output.get("validation", {}).get("valid") else 3
        return 0
    except Exception as exc:
        error = {
            "ok": False,
            "protocol": AGENT_PROTOCOL,
            "schema_version": "mega-agent-v1",
            "operation": getattr(args, "command", None),
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        print(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
