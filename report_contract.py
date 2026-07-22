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
    if plan_status and not plan_status.get("valid_for_evidence", True):
        errors.append("query_plan_invalid")
    if axes.get("semantic", {}).get("status") == "insufficient" or adequacy.get("status") in {
        "retrieval_gap", "coverage_gap", "insufficient", "error"
    }:
        errors.append("semantic_evidence_insufficient")
    if qualified < 1:
        errors.append("no_qualified_evidence")
    if citation_ready < 1:
        warnings.append("no_citation_ready_evidence")
    provenance = axes.get("provenance", {}).get("status")
    if provenance in {"provisional", "out_of_scope_or_unverified"}:
        warnings.append("provenance_requires_verification")
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    allowed = not errors
    return {
        "protocol": REPORT_CONTRACT_PROTOCOL,
        "synthesis_allowed": allowed,
        "artifact_type": "research_evidence_package" if allowed else "diagnostic_candidate_package",
        "answer_scope": "qualified" if allowed and warnings else ("full" if allowed else "diagnostic_only"),
        "errors": errors,
        "warnings": warnings,
        "required_next_action": (
            "synthesize_with_explicit_limits" if allowed and warnings
            else "synthesize" if allowed
            else "repair_plan_or_retrieval"
        ),
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
    allowed = not errors and overall in {"adequate", "partial"}
    return {
        "protocol": REPORT_CONTRACT_PROTOCOL,
        "synthesis_allowed": allowed,
        "artifact_type": "research_run",
        "answer_scope": "qualified" if missing or overall == "partial" else "full",
        "errors": errors,
        "warnings": warnings,
        "missing_requirements": missing,
        "required_next_action": (
            "synthesize_with_explicit_limits" if allowed and (missing or overall == "partial")
            else "synthesize" if allowed
            else "repair_plan_or_retrieval"
        ),
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
                bool(item.get("provenance", {}).get("evidence_eligible"))
                for item in resolved
            ):
                errors.append(f"textual_claim_without_qualified_evidence:{claim_id}")
            elif any(
                not bool(item.get("provenance", {}).get("evidence_eligible"))
                for item in resolved
            ):
                errors.append(f"textual_claim_uses_unqualified_evidence:{claim_id}")
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
