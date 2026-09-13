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
    planning_memory_list_agent,
    planning_memory_review_agent,
    planning_memory_status_agent,
    planning_memory_validate_agent,
    protocol_report_agent,
    report_check_agent,
    research_plan_agent,
    research_run_agent,
    research_session_continue_agent,
    research_session_expand_agent,
    research_session_finalize_agent,
    research_session_start_agent,
    research_session_status_agent,
    register_agent,
    search_agent,
    source_catalog_build_agent,
    source_catalog_coverage_agent,
    source_catalog_list_agent,
    source_catalog_show_agent,
    source_catalog_status_agent,
    status_agent,
    term_probe_agent,
    verify_agent,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="describe supported operations")
    subparsers.add_parser("status", help="show a fast index status snapshot")
    subparsers.add_parser("memory-status", help="show planning memory counts")
    subparsers.add_parser(
        "source-catalog-status", help="show source identity and relation coverage"
    )
    protocol_report = subparsers.add_parser(
        "protocol-report", help="audit one advisory agent research trace"
    )
    protocol_report.add_argument("trace_id")

    source_build = subparsers.add_parser(
        "source-catalog-build", help="build derived source catalog tables"
    )
    source_build.add_argument("--manifest")

    source_list = subparsers.add_parser(
        "source-catalog-list", help="list bounded source catalog entries"
    )
    source_list.add_argument("--abteilung")
    source_list.add_argument("--band")
    source_list.add_argument("--collection")
    source_list.add_argument("--text-type")
    source_list.add_argument("--language")
    source_list.add_argument("--work")
    source_list.add_argument("--version")
    source_list.add_argument("--limit", type=int, default=50)
    source_list.add_argument("--page", type=int, default=1)
    source_list.add_argument("--page-size", type=int)

    source_coverage = subparsers.add_parser(
        "source-catalog-coverage", help="show bounded source coverage evidence"
    )
    source_coverage.add_argument("--abteilung")
    source_coverage.add_argument("--band")
    source_coverage.add_argument("--collection")
    source_coverage.add_argument("--text-type")
    source_coverage.add_argument("--language")
    source_coverage.add_argument("--work")
    source_coverage.add_argument("--version")
    source_coverage.add_argument("--page", type=int, default=1)
    source_coverage.add_argument("--page-size", type=int, default=50)

    source_show = subparsers.add_parser(
        "source-catalog-show", help="inspect one source and its version relations"
    )
    source_show.add_argument("source_id")

    memory_list = subparsers.add_parser(
        "memory-list", help="list planning memory candidates"
    )
    memory_list.add_argument("--kind", choices=("query_plan", "research_plan"))
    memory_list.add_argument(
        "--status",
        choices=("proposed", "corpus_validated", "promoted", "rejected"),
    )
    memory_list.add_argument("--limit", type=int, default=50)

    memory_validate = subparsers.add_parser(
        "memory-validate", help="check candidate terms against MEGA TEXT"
    )
    memory_validate.add_argument("memory_id")

    memory_promote = subparsers.add_parser(
        "memory-promote", help="explicitly promote a corpus-validated candidate"
    )
    memory_promote.add_argument("memory_id")
    memory_promote.add_argument("--note", required=True)
    memory_promote.add_argument("--reviewer", default="user")

    memory_reject = subparsers.add_parser(
        "memory-reject", help="reject a planning candidate"
    )
    memory_reject.add_argument("memory_id")
    memory_reject.add_argument("--note", required=True)
    memory_reject.add_argument("--reviewer", default="user")

    plan = subparsers.add_parser("plan", help="build a zero-token inspectable query plan")
    plan.add_argument("query")
    plan.add_argument("--refinement-file")
    plan_mode = plan.add_mutually_exclusive_group()
    plan_mode.add_argument("--auto", action="store_true", help="refine only when policy requires it")
    plan_mode.add_argument("--hybrid", action="store_true", help="use bounded Flash plan refinement")
    plan.add_argument("--no-probe", action="store_true")
    plan.add_argument("--no-register", action="store_true")
    _add_query_hint_arguments(plan)
    _add_trace_argument(plan)

    research_plan = subparsers.add_parser(
        "research-plan", help="decompose a compound question by evidence requirement"
    )
    research_plan.add_argument("query")
    research_plan.add_argument("--refinement-file")
    research_plan_mode = research_plan.add_mutually_exclusive_group()
    research_plan_mode.add_argument("--auto", action="store_true")
    research_plan_mode.add_argument("--hybrid", action="store_true")
    _add_trace_argument(research_plan)

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
    research_run_mode = research_run.add_mutually_exclusive_group()
    research_run_mode.add_argument("--auto", action="store_true")
    research_run_mode.add_argument("--hybrid", action="store_true")
    _add_trace_argument(research_run)

    probe = subparsers.add_parser("term-probe", help="count exact planned terms")
    probe.add_argument("query")
    probe.add_argument("--refinement-file")
    probe.add_argument("--language")
    probe.add_argument("--work")
    probe.add_argument("--version")
    probe.add_argument("--text-type")
    probe.add_argument("--page", type=int, default=1)
    probe.add_argument("--page-size", type=int, default=25)
    probe.add_argument("--export")

    register = subparsers.add_parser("register", help="query Sachregister navigation")
    register.add_argument("query")
    register.add_argument("--top-k", type=int, default=8)
    register.add_argument("--refinement-file")
    _add_trace_argument(register)

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
    _add_query_hint_arguments(search)
    _add_trace_argument(search)

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
    _add_trace_argument(evidence)

    session = subparsers.add_parser(
        "research-session",
        help="run a fail-closed formal research workflow",
    )
    session_commands = session.add_subparsers(
        dest="session_command", required=True
    )
    session_start = session_commands.add_parser(
        "start", help="create and plan a formal research session"
    )
    session_start.add_argument("query")
    session_start.add_argument("--refinement-file")
    session_start_mode = session_start.add_mutually_exclusive_group()
    session_start_mode.add_argument("--auto", action="store_true")
    session_start_mode.add_argument("--hybrid", action="store_true")
    _add_trace_argument(session_start)

    session_continue = session_commands.add_parser(
        "continue", help="retrieve or repair evidence inside a session"
    )
    session_continue.add_argument("session_id")
    session_continue.add_argument("--refinement-file")
    session_continue.add_argument("--top-k-per-branch", type=int, default=5)
    session_continue.add_argument("--max-evidence", type=int, default=12)
    session_continue.add_argument(
        "--retrieval-mode",
        choices=("original_first", "apparat_first", "balanced", "philology"),
        default="original_first",
    )
    session_continue.add_argument(
        "--rerank", choices=("none", "rule", "bge"), default="rule"
    )
    session_continue.add_argument("--output-dir")
    session_continue_mode = session_continue.add_mutually_exclusive_group()
    session_continue_mode.add_argument("--auto", action="store_true")
    session_continue_mode.add_argument("--hybrid", action="store_true")
    _add_query_hint_arguments(session_continue)

    session_expand = session_commands.add_parser(
        "expand", help="expand selected qualified evidence"
    )
    session_expand.add_argument("session_id")
    session_expand.add_argument("ids", nargs="+")
    session_expand.add_argument(
        "--detail", choices=("snippet", "full"), default="full"
    )

    session_finalize = session_commands.add_parser(
        "finalize", help="validate a structured report and issue a receipt"
    )
    session_finalize.add_argument("session_id")
    session_finalize.add_argument("--report", required=True)

    session_status = session_commands.add_parser(
        "status", help="show state, events, and required next action"
    )
    session_status.add_argument("session_id")
    session_status.add_argument("--no-events", action="store_true")
    return parser

def _add_query_hint_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--focus", action="append",
        help="core evidence term; repeat or separate values with commas",
    )
    parser.add_argument(
        "--context", action="append",
        help="recall-only context term; repeat or separate values with commas",
    )
    parser.add_argument(
        "--volumes", action="append",
        help="soft MEGA scope such as II/1 or II/5,II/6",
    )
    parser.add_argument(
        "--intent",
        choices=(
            "author_argument", "apparat_question", "general_search",
            "concept_relation", "claim_verification", "frequency_analysis",
            "source_lookup",
        ),
    )

def _add_trace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--trace-id",
        help="optional advisory research-session trace identifier",
    )


def _load_refinement(path: str | None) -> dict | None:
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("refinement file must contain one JSON object")
    return payload

def _planner_mode(args: argparse.Namespace, refinement: dict | None) -> str:
    if refinement:
        return "agent_supplied"
    if getattr(args, "auto", False):
        return "auto"
    if getattr(args, "hybrid", False):
        return "hybrid"
    return "local"


def _dispatch(args: argparse.Namespace) -> dict:
    if args.command == "capabilities":
        return capabilities()
    if args.command == "status":
        return status_agent()
    if args.command == "protocol-report":
        return protocol_report_agent(args.trace_id)
    if args.command == "memory-status":
        return planning_memory_status_agent()
    if args.command == "source-catalog-status":
        return source_catalog_status_agent()
    if args.command == "source-catalog-build":
        return source_catalog_build_agent(args.manifest)
    if args.command == "source-catalog-list":
        return source_catalog_list_agent(
            abteilung=args.abteilung,
            band=args.band,
            collection=args.collection,
            text_type=args.text_type,
            language=args.language,
            work=args.work,
            version=args.version,
            limit=args.limit,
            page=args.page,
            page_size=args.page_size,
        )
    if args.command == "source-catalog-coverage":
        return source_catalog_coverage_agent(
            abteilung=args.abteilung,
            band=args.band,
            collection=args.collection,
            text_type=args.text_type,
            language=args.language,
            work=args.work,
            version=args.version,
            page=args.page,
            page_size=args.page_size,
        )
    if args.command == "source-catalog-show":
        return source_catalog_show_agent(args.source_id)
    if args.command == "memory-list":
        return planning_memory_list_agent(
            kind=args.kind, status=args.status, limit=args.limit
        )
    if args.command == "memory-validate":
        return planning_memory_validate_agent(args.memory_id)
    if args.command == "memory-promote":
        return planning_memory_review_agent(
            args.memory_id, "promote", note=args.note, reviewer=args.reviewer
        )
    if args.command == "memory-reject":
        return planning_memory_review_agent(
            args.memory_id, "reject", note=args.note, reviewer=args.reviewer
        )
    if args.command == "plan":
        refinement = _load_refinement(args.refinement_file)
        return plan_agent(
            args.query, refinement=refinement,
            planner_mode=_planner_mode(args, refinement),
            include_probe=not args.no_probe,
            include_register=not args.no_register,
            focus_terms=args.focus,
            context_terms=args.context,
            target_volumes=args.volumes,
            intent=args.intent,
            trace_id=args.trace_id,
        )
    if args.command == "research-plan":
        refinement = _load_refinement(args.refinement_file)
        return research_plan_agent(
            args.query,
            refinement=refinement,
            planner_mode=_planner_mode(args, refinement),
            trace_id=args.trace_id,
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
            planner_mode=_planner_mode(args, refinement),
            trace_id=args.trace_id,
        )

    if args.command == "research-session":
        if args.session_command == "start":
            refinement = _load_refinement(args.refinement_file)
            return research_session_start_agent(
                args.query,
                refinement=refinement,
                planner_mode=_planner_mode(args, refinement),
                trace_id=args.trace_id,
            )
        if args.session_command == "continue":
            refinement = _load_refinement(args.refinement_file)
            return research_session_continue_agent(
                args.session_id,
                refinement=refinement,
                focus_terms=args.focus,
                context_terms=args.context,
                target_volumes=args.volumes,
                intent=args.intent,
                planner_mode=_planner_mode(args, refinement),
                top_k_per_branch=args.top_k_per_branch,
                max_evidence=args.max_evidence,
                retrieval_mode=args.retrieval_mode,
                rerank_method=args.rerank,
                output_dir=args.output_dir,
            )
        if args.session_command == "expand":
            return research_session_expand_agent(
                args.session_id, args.ids, detail=args.detail
            )
        if args.session_command == "finalize":
            return research_session_finalize_agent(
                args.session_id, args.report
            )
        if args.session_command == "status":
            return research_session_status_agent(
                args.session_id, include_events=not args.no_events
            )
        raise ValueError(f"unsupported research-session command: {args.session_command}")

    if args.command == "term-probe":
        return term_probe_agent(
            args.query,
            refinement=_load_refinement(args.refinement_file),
            page=args.page,
            page_size=args.page_size,
            language=args.language,
            work=args.work,
            version=args.version,
            text_type=args.text_type,
            export_path=args.export,
        )
    if args.command == "register":
        return register_agent(
            args.query, refinement=_load_refinement(args.refinement_file),
            top_k=args.top_k,
            trace_id=args.trace_id,
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
            focus_terms=args.focus,
            context_terms=args.context,
            target_volumes=args.volumes,
            intent=args.intent,
            trace_id=args.trace_id,
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
        return evidence_from_package(
            args.package, args.ids, detail=args.detail, trace_id=args.trace_id
        )
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
        if output.get("operation") == "research_run":
            return 0 if output.get("answer_allowed") else 5
        if output.get("operation") == "research_session_continue":
            return 0 if not output.get("blocked") else 5
        if output.get("operation") == "research_session_finalize":
            return 0 if output.get("completion_allowed") else 4
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
