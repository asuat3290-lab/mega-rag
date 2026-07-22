#!/usr/bin/env python3
"""Candidate qualification and retrieval-adequacy diagnostics.

This module uses ordinal evidence classes instead of adding more global score
weights.  It is activated by a validated QueryPlan and leaves legacy queries
untouched when no discriminating plan rule applies.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable


PARATEXT_LAYERS = {
    "apparatus", "editorial_intro", "editorial_note", "table_of_contents",
    "front_matter", "register", "illustration_list",
}


def _matches(text: str, terms: Iterable[str]) -> list[str]:
    lowered = str(text or "").casefold()
    output = []
    seen = set()
    for term in terms:
        value = str(term or "").strip()
        key = value.casefold()
        if len(value) > 1 and key in lowered and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _volume_match(result: dict, target_volumes: list[dict]) -> bool:
    for volume in target_volumes:
        if not volume.get("abteilung") or result.get("abteilung") != volume["abteilung"]:
            continue
        if not volume.get("band") or str(result.get("band")) == str(volume["band"]):
            return True
    return False


def qualify_candidate(result: dict, plan: dict) -> dict:
    text = str(result.get("text") or result.get("display_snippet") or "")
    direct_terms = list(plan.get("core_terms", [])) + list(plan.get("historical_variants", []))
    direct_hits = _matches(text, direct_terms)
    related_hits = _matches(text, plan.get("related_non_equivalent", []))
    support_hits = _matches(text, plan.get("supporting_terms", []))
    generic_hits = _matches(text, plan.get("generic_terms", []))
    relation_pair_hits = []
    for pair in plan.get("relation_pairs", []):
        hits = _matches(text, pair)
        if len(hits) >= 2:
            relation_pair_hits.append(hits)

    text_type = str(result.get("type") or result.get("source_type") or "")
    layer = str(result.get("text_layer") or "unclassified")
    is_text = text_type == "TEXT"
    author_eligible = is_text and layer not in PARATEXT_LAYERS
    target_match = _volume_match(result, plan.get("target_volumes", []))
    structural = bool(relation_pair_hits or (len(support_hits) >= 2))

    if direct_hits and author_eligible:
        candidate_class = "direct_author_text"
        rank_bucket = 0 if target_match else 1
    elif structural and author_eligible:
        candidate_class = "structural_author_text"
        rank_bucket = 2 if target_match else 3
    elif related_hits and author_eligible:
        candidate_class = "related_concept_context"
        rank_bucket = 4
    elif direct_hits or related_hits or structural:
        candidate_class = "editorial_or_paratext_context"
        rank_bucket = 5
    elif generic_hits:
        candidate_class = "generic_only"
        rank_bucket = 7
    else:
        candidate_class = "no_required_signal"
        rank_bucket = 8

    evidence_eligible = bool(author_eligible and (direct_hits or structural))
    debug = {
        "candidate_class": candidate_class,
        "qualification_bucket": rank_bucket,
        "direct_core_hits": direct_hits,
        "historical_or_core_hit_count": len(direct_hits),
        "related_non_equivalent_hits": related_hits,
        "supporting_hits": support_hits,
        "relation_pair_hits": relation_pair_hits,
        "generic_hits": generic_hits,
        "target_scope_match": target_match,
        "author_evidence_eligible": evidence_eligible,
    }
    result["_qualification"] = debug
    result.setdefault("_debug", {}).update(debug)
    result["evidence_eligible"] = evidence_eligible
    return result


def apply_candidate_qualification(results: list[dict], plan: dict) -> list[dict]:
    """Stable-partition planned queries by evidence class, not score inflation."""
    for index, result in enumerate(results):
        qualify_candidate(result, plan)
        result["_qualification"]["rank_before"] = index + 1
    # Apply only where the planner has a validated special rule or a claim audit.
    active = bool(plan.get("matched_rule_ids")) or plan.get("intent") == "claim_verification"
    if not active:
        return results
    ordered = sorted(
        enumerate(results),
        key=lambda item: (item[1]["_qualification"]["qualification_bucket"], item[0]),
    )
    output = [result for _, result in ordered]
    for index, result in enumerate(output):
        result["_qualification"]["rank_after"] = index + 1
        result["_debug"]["qualification_rank_after"] = index + 1
    return output


def assess_retrieval_adequacy(results: list[dict], plan: dict, *,
                              term_probe: dict | None = None,
                              register_hits: list[dict] | None = None) -> dict:
    qualifications = [result.get("_qualification", {}) for result in results]
    classes = Counter(item.get("candidate_class", "unclassified") for item in qualifications)
    eligible = [result for result in results if result.get("evidence_eligible")]
    direct = [result for result in eligible
              if result.get("_qualification", {}).get("candidate_class") == "direct_author_text"]
    verified_direct = [result for result in direct if result.get("text_layer") == "author_text"]
    target_direct = [result for result in direct
                     if result.get("_qualification", {}).get("target_scope_match")]
    probe_summary = (term_probe or {}).get("summary", {})
    probe_text_hits = sum(
        int(item.get("text_pages", 0)) for item in (term_probe or {}).get("terms", [])
    )

    if direct:
        status = "adequate" if len(direct) >= 2 else "partial"
        reason = "direct author-text candidates retrieved"
    elif eligible:
        status = "partial"
        reason = "only structural author-text candidates retrieved"
    elif probe_text_hits:
        status = "retrieval_gap"
        reason = "term probe found TEXT pages but no eligible candidate reached the result set"
    elif register_hits:
        status = "coverage_gap"
        reason = "Sachregister has navigation hits but indexed author text has no direct hit"
    else:
        status = "insufficient"
        reason = "no direct or structural author-text evidence was retrieved"

    quantifier_warning = None
    if plan.get("claim_strength") != "ordinary":
        quantifier_warning = (
            "A strong quantifier requires corpus-level comparison or negative sampling; "
            "matching passages alone cannot establish predominance, exclusivity, or absence."
        )
        if status == "adequate":
            status = "partial"

    return {
        "status": status,
        "reason": reason,
        "candidate_classes": dict(classes),
        "eligible_evidence_count": len(eligible),
        "direct_author_text_count": len(direct),
        "verified_direct_count": len(verified_direct),
        "target_scope_direct_count": len(target_direct),
        "term_probe_text_hits": probe_text_hits,
        "register_navigation_hits": len(register_hits or []),
        "strong_claim_warning": quantifier_warning,
        "probe_summary": probe_summary,
    }
