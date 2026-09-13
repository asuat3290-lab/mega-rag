#!/usr/bin/env python3
"""Fail-closed gates for MEGA evidence packages and agent-authored reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPORT_CONTRACT_PROTOCOL = "mega-report-contract-v1"
TEXTUAL_CLAIM_TYPES = {
    "text_supported", "text_supported_qualification", "philological_finding",
}
INFERENCE_CLAIM_TYPES = {
    "theoretical_inference", "comparative_inference", "corpus_level_claim",
    "empirical_hypothesis", "research_claim",
}
ALLOWED_CLAIM_TYPES = TEXTUAL_CLAIM_TYPES | INFERENCE_CLAIM_TYPES
PLANNING_BLOCKERS = {
    "flat_multi_term_query_needs_focus",
    "unmapped_lexical_concept_needs_semantic_refinement",
    "relation_query_needs_branch_planning",
    "strong_claim_needs_counter_search",
}


def _qualified_text_count(payload: dict) -> int:
    """Count evidence-eligible TEXT records without requiring digital provenance."""
    summary = payload.get("summary", {})
    if "claim_eligible_text_count" in summary:
        return int(summary.get("claim_eligible_text_count", 0) or 0)
    if "qualified_text_evidence_count" in summary:
        return int(summary.get("qualified_text_evidence_count", 0) or 0)
    return sum(
        1
        for item in payload.get("evidence", [])
        if bool(
            item.get("provenance", {}).get(
                "claim_eligible",
                item.get("provenance", {}).get("evidence_eligible"),
            )
        )
        and str(item.get("locator", {}).get("text_type") or "").upper() == "TEXT"
    )


def _planning_errors(advice: dict) -> list[str]:
    if not isinstance(advice, dict):
        return []
    decision = str(advice.get("decision") or "")
    reasons = {
        str(value)
        for value in advice.get("reason_codes", [])
        if str(value).strip()
    }
    errors = []
    if decision == "refinement_required":
        errors.append("query_refinement_required")
    if reasons.intersection(PLANNING_BLOCKERS):
        errors.append("query_refinement_or_research_plan_required")
    return errors


def _next_action(errors: list[str], warnings: list[str]) -> dict:
    if "query_refinement_required" in errors or (
        "query_refinement_or_research_plan_required" in errors
    ):
        return {
            "operation": "refine_plan",
            "required": True,
            "reason": "The current semantic plan is not sufficient for formal synthesis.",
        }
    if "author_text_evidence_missing" in errors:
        return {
            "operation": "refine_and_retrieve_text",
            "required": True,
            "reason": "An author-argument question requires qualified TEXT evidence.",
        }
    if errors:
        return {
            "operation": "repair_plan_or_retrieval",
            "required": True,
            "reason": "The evidence package did not pass the synthesis gate.",
        }
    return {
        "operation": "expand_selected_evidence",
        "required": True,
        "reason": (
            "Expand the selected evidence before drafting claims."
            if warnings
            else "Expand the selected evidence before drafting claims or quotations."
        ),
    }


def evaluate_package_gate(package: dict) -> dict:
    """Decide whether a single-query package may enter synthesis."""
    summary = package.get("summary", {})
    retrieval = package.get("retrieval", {})
    adequacy = retrieval.get("debug", {}).get("adequacy", {})
    plan = package.get("query", {}).get("query_plan", {})
    plan_status = plan.get("plan_status") or adequacy.get("plan_status") or {}
    axes = adequacy.get("axes", {})
    qualified = int(
        summary.get("qualified_evidence_count", summary.get("evidence_eligible_count", 0))
        or 0
    )
    citation_ready = int(summary.get("citation_ready_count", 0) or 0)
    errors = []
    warnings = []
    errors.extend(_planning_errors(package.get("query", {}).get("planning_advice", {})))
    if plan_status and not plan_status.get("valid_for_evidence", True):
        errors.append("query_plan_invalid")
    if axes.get("semantic", {}).get("status") == "insufficient" or adequacy.get("status") in {
        "retrieval_gap", "coverage_gap", "insufficient", "error"
    }:
        errors.append("semantic_evidence_insufficient")
    if qualified < 1:
        errors.append("no_qualified_evidence")
    focus = retrieval.get("debug", {}).get("focus_diagnostics", {})
    if focus.get("explicit") and int(focus.get("qualified_final_hits", 0) or 0) < 1:
        errors.append("explicit_focus_not_qualified")
    warnings.extend(focus.get("warnings", []))
    if citation_ready < 1:
        warnings.append("no_citation_ready_evidence")
    provenance = axes.get("provenance", {}).get("status")
    if provenance in {"provisional", "out_of_scope_or_unverified"}:
        warnings.append("provenance_requires_verification")
    intent = str(
        package.get("query", {}).get("query_profile", {}).get("intent") or ""
    )
    if intent == "author_argument" and _qualified_text_count(package) < 1:
        errors.append("author_text_evidence_missing")
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    allowed = not errors
    next_action = _next_action(errors, warnings)
    return {
        "protocol": REPORT_CONTRACT_PROTOCOL,
        "synthesis_allowed": allowed,
        "artifact_type": "research_evidence_package" if allowed else "diagnostic_candidate_package",
        "answer_scope": "qualified" if allowed and warnings else ("full" if allowed else "diagnostic_only"),
        "errors": errors,
        "warnings": warnings,
        "workflow_state": "evidence_qualified" if allowed else "needs_repair",
        "completion_allowed": False,
        "required_next_action": next_action["operation"],
        "next_action": next_action,
    }


def evaluate_research_run_gate(run: dict) -> dict:
    """Preserve branch and external-evidence limits for compound research."""
    status = run.get("status", {})
    overall = status.get("overall")
    missing = list(status.get("missing_requirements", []))
    errors = []
    warnings = []
    if overall in {"retrieval_gap", "incomplete_plan", "error"}:
        errors.append(str(overall))
    if missing:
        warnings.append("unresolved_external_or_branch_requirements")
    author_argument_required = any(
        str(item.get("query_intent") or "") == "author_argument"
        and "mega" in (item.get("required_corpus") or [])
        for item in run.get("research_plan", {}).get("subquestions", [])
    )
    if author_argument_required and _qualified_text_count(run) < 1:
        errors.append("author_text_evidence_missing")
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in run.get("evidence", [])
    }
    matrix_by_branch = {
        str(item.get("subquestion_id") or ""): item
        for item in run.get("claim_evidence_matrix", [])
    }
    for subquestion in run.get("research_plan", {}).get("subquestions", []):
        branch_id = str(subquestion.get("id") or "")
        if (
            str(subquestion.get("query_intent") or "") != "author_argument"
            or "mega" not in (subquestion.get("required_corpus") or [])
        ):
            continue
        row = matrix_by_branch.get(branch_id, {})
        branch_evidence = [
            evidence_by_id.get(str(evidence_id))
            for evidence_id in row.get("evidence_ids", [])
        ]
        if not any(
            item
            and bool(
                item.get("provenance", {}).get(
                    "claim_eligible",
                    item.get("provenance", {}).get("evidence_eligible"),
                )
            )
            and str(item.get("locator", {}).get("text_type") or "").upper() == "TEXT"
            for item in branch_evidence
        ):
            errors.append(f"author_text_evidence_missing:{branch_id}")
    claim_evidence = [
        item
        for item in run.get("evidence", [])
        if bool(
            item.get("provenance", {}).get(
                "claim_eligible",
                item.get("provenance", {}).get("evidence_eligible"),
            )
        )
    ]
    if claim_evidence and not any(
        bool(item.get("provenance", {}).get("provenance_ready"))
        for item in claim_evidence
    ):
        warnings.append("provisional_source_layer_requires_disclosure")
    allowed = not errors and overall in {"adequate", "partial"}
    next_action = _next_action(errors, warnings)
    return {
        "protocol": REPORT_CONTRACT_PROTOCOL,
        "synthesis_allowed": allowed,
        "artifact_type": "research_run",
        "answer_scope": "qualified" if missing or overall == "partial" else "full",
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "missing_requirements": missing,
        "workflow_state": "evidence_qualified" if allowed else "needs_repair",
        "completion_allowed": False,
        "required_next_action": next_action["operation"],
        "next_action": next_action,
    }


def _source_evidence(source: dict) -> list[dict]:
    items = source.get("evidence", [])
    return items if isinstance(items, list) else []


def _evidence_indexes(source: dict) -> tuple[dict[str, dict], list[str]]:
    index: dict[str, dict] = {}
    local_mapping: dict[str, str] = {}
    errors = []
    for item in _source_evidence(source):
        local_id = str(item.get("evidence_id") or "").strip()
        uid = str(item.get("evidence_uid") or "").strip()
        package_ref = str(item.get("package_evidence_ref") or "").strip()
        identity = uid or package_ref or local_id
        if local_id:
            previous = local_mapping.get(local_id)
            if previous and previous != identity:
                errors.append(f"duplicate_local_evidence_id:{local_id}")
            local_mapping[local_id] = identity
        for key in (local_id, uid, package_ref):
            if key:
                index[key.casefold()] = item
    return index, errors


def validate_report_claims(report: dict, source: dict) -> dict:
    """Validate a structured agent report against one package or research run.

    The validator checks evidence identity, provenance, quote boundaries, and
    claim type. It intentionally does not ask a language model to judge truth.
    """
    errors = []
    warnings = []
    source_gate = source.get("synthesis_gate") or {}
    if source_gate and not source_gate.get("synthesis_allowed", False):
        errors.append("source_synthesis_gate_closed")
    index, identity_errors = _evidence_indexes(source)
    errors.extend(identity_errors)
    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("report_requires_nonempty_claims")
        claims = []
    seen_claim_ids = set()
    for position, claim in enumerate(claims, start=1):
        claim_id = str(claim.get("claim_id") or f"claim_{position}")
        if claim_id in seen_claim_ids:
            errors.append(f"duplicate_claim_id:{claim_id}")
        seen_claim_ids.add(claim_id)
        claim_type = str(claim.get("claim_type") or "")
        if claim_type not in ALLOWED_CLAIM_TYPES:
            errors.append(f"unsupported_claim_type:{claim_id}:{claim_type}")
        refs = [str(value).strip() for value in claim.get("evidence_refs", []) if str(value).strip()]
        resolved = []
        for ref in refs:
            item = index.get(ref.casefold())
            if item is None:
                errors.append(f"unknown_evidence_ref:{claim_id}:{ref}")
            else:
                resolved.append(item)
        if claim_type in TEXTUAL_CLAIM_TYPES:
            if not resolved:
                errors.append(f"textual_claim_without_evidence:{claim_id}")
            elif not any(
                bool(item.get("provenance", {}).get(
                    "claim_eligible",
                    item.get("provenance", {}).get("evidence_eligible"),
                ))
                for item in resolved
            ):
                errors.append(f"textual_claim_without_qualified_evidence:{claim_id}")
            elif any(
                not bool(item.get("provenance", {}).get(
                    "claim_eligible",
                    item.get("provenance", {}).get("evidence_eligible"),
                ))
                for item in resolved
            ):
                errors.append(f"textual_claim_uses_unqualified_evidence:{claim_id}")
            if resolved and any(
                not bool(item.get("provenance", {}).get("provenance_ready"))
                for item in resolved
            ):
                warnings.append(
                    f"textual_claim_uses_provisional_provenance:{claim_id}"
                )
        if claim_type == "empirical_hypothesis" and not claim.get("external_evidence_refs"):
            warnings.append(f"empirical_hypothesis_unverified:{claim_id}")
        for quote in claim.get("quotes", []) or []:
            ref = str(quote.get("evidence_ref") or "").strip()
            quoted = str(quote.get("text") or "").strip()
            item = index.get(ref.casefold())
            if item is None:
                errors.append(f"quote_unknown_evidence_ref:{claim_id}:{ref}")
                continue
            evidence = item.get("evidence", {})
            if not evidence.get("quote_eligible"):
                errors.append(f"quote_not_eligible:{claim_id}:{ref}")
            context = str(evidence.get("german_context") or "")
            if not quoted or quoted not in context:
                errors.append(f"quote_not_verbatim:{claim_id}:{ref}")
    return {
        "protocol": REPORT_CONTRACT_PROTOCOL,
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "claim_count": len(claims),
        "evidence_count": len(_source_evidence(source)),
    }


def load_json_artifact(path: str | Path) -> dict:
    """Load agent-authored JSON, accepting the UTF-8 BOM used by Windows tools."""
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("JSON artifact must contain an object at the top level")
    return payload

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_json")
    parser.add_argument("source_json")
    args = parser.parse_args()
    report = load_json_artifact(args.report_json)
    source = load_json_artifact(args.source_json)
    result = validate_report_claims(report, source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
