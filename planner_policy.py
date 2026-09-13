#!/usr/bin/env python3
"""Deterministic policy for deciding when MEGA query planning needs an agent."""

from __future__ import annotations


POLICY_VERSION = "agent-first-policy-v1"


def _decision(required: list[str], recommended: list[str]) -> str:
    if required:
        return "refinement_required"
    if recommended:
        return "refinement_recommended"
    return "local"


def _payload(decision: str, required: list[str], recommended: list[str]) -> dict:
    reasons = list(dict.fromkeys(required + recommended))
    return {
        "policy_version": POLICY_VERSION,
        "decision": decision,
        "local_sufficient": decision == "local",
        "should_refine": decision != "local",
        "preferred_refiner": "calling_agent" if decision != "local" else None,
        "fallback_refiner": "internal_flash" if decision != "local" else None,
        "reason_codes": reasons,
        "required_reason_codes": list(dict.fromkeys(required)),
        "recommended_reason_codes": list(dict.fromkeys(recommended)),
        "refiner_payload_policy": (
            "Send compact plans, exact term counts, and register hints; "
            "do not send retrieved evidence text."
        ),
    }


def assess_query_plan(plan: dict) -> dict:
    """Classify whether a local QueryPlan is sufficient for retrieval."""
    required: list[str] = []
    recommended: list[str] = []
    status = plan.get("plan_status") or {}
    issues = set(status.get("issues") or [])

    if not status.get("valid_for_evidence", True):
        required.append("query_plan_not_evidence_valid")
    if "missing_discriminating_core" in issues:
        required.append("missing_discriminating_core")
    if "missing_qualification_groups" in issues:
        required.append("missing_qualification_groups")
    if status.get("legacy_ambiguous_terms"):
        required.append("legacy_glossary_requires_phrase_resolution")

    intent = str(plan.get("intent") or "")
    if intent in {"concept_relation", "claim_verification"}:
        if not plan.get("relation_pairs"):
            recommended.append("relation_query_needs_branch_planning")
    if str(plan.get("claim_strength") or "ordinary") != "ordinary":
        recommended.append("strong_claim_needs_counter_search")
    if plan.get("related_non_equivalent") and not plan.get("core_terms"):
        required.append("related_terms_without_core")
    registry = list(plan.get("term_registry") or [])
    lexical_entries = [
        item for item in registry
        if item.get("role") in {"core", "historical"}
    ]
    lexical_only = bool(lexical_entries) and all(
        set(item.get("source") or []).issubset({"lexical_fallback", "query_profile"})
        for item in lexical_entries
    )
    qualification_groups = [
        group for group in (plan.get("qualification_groups") or [])
        if group.get("alternatives")
    ]
    qualification_sources = {
        str(group.get("source_term") or group.get("id") or "")
        for group in qualification_groups
        if str(group.get("source_term") or group.get("id") or "")
    }
    flat_lexical_group = any(
        str(group.get("source") or "") == "lexical_fallback"
        and len(group.get("alternatives") or []) > 1
        for group in qualification_groups
    )
    if (
        not plan.get("focus_explicit")
        and (flat_lexical_group or len(qualification_sources) > 1)
        and not plan.get("matched_rule_ids")
    ):
        recommended.append("flat_multi_term_query_needs_focus")
    if (
        not plan.get("focus_explicit") and lexical_only
        and intent in {"author_argument", "concept_relation", "claim_verification"}
        and not plan.get("related_non_equivalent")
        and not plan.get("supporting_terms")
    ):
        recommended.append("unmapped_lexical_concept_needs_semantic_refinement")

    decision = _decision(required, recommended)
    return _payload(decision, required, recommended)


def assess_research_plan(plan: dict) -> dict:
    """Classify whether a local ResearchPlan needs semantic decomposition."""
    required: list[str] = []
    recommended: list[str] = []

    if plan.get("unmapped_concepts"):
        required.append("meaningful_unmapped_concepts")
    if plan.get("coverage_status") == "incomplete":
        required.append("incomplete_question_coverage")

    subquestions = plan.get("subquestions") or []
    invalid_subplans = [
        item.get("id")
        for item in subquestions
        if not (item.get("query_plan") or {}).get("plan_status", {}).get(
            "valid_for_evidence", True
        )
        and "mega" in (item.get("required_corpus") or [])
    ]
    if invalid_subplans:
        required.append("invalid_mega_subquery_plan")

    question_type = str(plan.get("question_type") or "")
    agent_decomposition_complete = bool(subquestions) and bool(
        str(plan.get("mode") or "") in {"agent_supplied", "hybrid"}
        and not plan.get("unmapped_concepts")
        and not invalid_subplans
    )
    if question_type == "diachronic_comparison" and not agent_decomposition_complete:
        recommended.append("diachronic_branch_selection")
    if (
        question_type in {"multi_part", "modern_application", "concept_relation"}
        and not agent_decomposition_complete
    ):
        recommended.append("compound_question_decomposition")
    if plan.get("external_evidence_required"):
        recommended.append("external_evidence_boundary")
    if len(subquestions) > 3:
        recommended.append("many_research_branches")

    decision = _decision(required, recommended)
    payload = _payload(decision, required, recommended)
    payload["invalid_subquery_ids"] = [value for value in invalid_subplans if value]
    payload["unmapped_concepts"] = list(plan.get("unmapped_concepts") or [])
    return payload
