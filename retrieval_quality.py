#!/usr/bin/env python3
"""Phrase-aware candidate qualification and multi-axis retrieval diagnostics."""

from __future__ import annotations

from collections import Counter
from typing import Iterable


PARATEXT_LAYERS = {
    "apparatus", "editorial_intro", "editorial_note", "table_of_contents",
    "front_matter", "register", "illustration_list",
}
EDITORIAL_LAYERS = {"apparatus", "editorial_intro", "editorial_note"}
NAVIGATION_LAYERS = {
    "table_of_contents", "front_matter", "register", "illustration_list",
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


def _term_positions(text: str, term: str) -> list[int]:
    haystack = str(text or "").casefold()
    needle = str(term or "").strip().casefold()
    if len(needle) < 2:
        return []
    positions = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return positions
        positions.append(index)
        start = index + max(1, len(needle))


def _near_match(text: str, terms: list[str], window_chars: int) -> tuple[bool, list[str]]:
    values = [str(term).strip() for term in terms if len(str(term).strip()) > 1]
    if not values:
        return False, []
    positions = [_term_positions(text, term) for term in values]
    if any(not item for item in positions):
        return False, []
    # The groups are deliberately small. A bounded cartesian scan is clearer
    # and more predictable than stemming or fuzzy model inference here.
    frontier = [item[0] for item in positions]
    indexes = [0] * len(positions)
    while True:
        if max(frontier) - min(frontier) <= window_chars:
            return True, values
        smallest = min(range(len(frontier)), key=frontier.__getitem__)
        indexes[smallest] += 1
        if indexes[smallest] >= len(positions[smallest]):
            return False, []
        frontier[smallest] = positions[smallest][indexes[smallest]]


def _match_qualification_groups(text: str, groups: list[dict]) -> list[dict]:
    matches = []
    for group in groups:
        alternatives = [
            str(value).strip() for value in group.get("alternatives", [])
            if len(str(value).strip()) > 1
        ]
        mode = str(group.get("match_mode") or "any")
        if mode == "all_near":
            matched, terms = _near_match(
                text, alternatives, int(group.get("window_chars") or 220)
            )
            strength = "proximity" if matched else None
        else:
            terms = _matches(text, alternatives)
            matched = bool(terms)
            strength = "exact_phrase" if matched else None
        if matched:
            matches.append({
                "id": group.get("id"),
                "label": group.get("label") or group.get("id"),
                "matched_terms": terms,
                "match_mode": mode,
                "strength": strength,
                "source": group.get("source"),
            })
    return matches


def _volume_match(result: dict, target_volumes: list[dict]) -> bool:
    for volume in target_volumes:
        if not volume.get("abteilung") or result.get("abteilung") != volume["abteilung"]:
            continue
        if not volume.get("band") or str(result.get("band")) == str(volume["band"]):
            return True
    return False


def _provenance_role(result: dict, plan: dict) -> tuple[bool, str]:
    text_type = str(result.get("type") or result.get("source_type") or "").upper()
    layer = str(result.get("text_layer") or "unclassified")
    routing = str(plan.get("routing_intent") or plan.get("intent") or "general_search")
    if routing == "author_argument":
        eligible = text_type == "TEXT" and layer not in PARATEXT_LAYERS
        return eligible, "author_text" if eligible else "not_author_text"
    if routing == "apparat_question":
        eligible = (
            (text_type == "APPARAT" or layer in EDITORIAL_LAYERS)
            and layer not in NAVIGATION_LAYERS
        )
        return eligible, "editorial_evidence" if eligible else "not_editorial_evidence"
    eligible = text_type in {"TEXT", "APPARAT"} and layer not in NAVIGATION_LAYERS
    return eligible, "source_evidence" if eligible else "paratext_or_unknown"


def qualify_candidate(result: dict, plan: dict) -> dict:
    text = str(result.get("text") or result.get("display_snippet") or "")
    groups = list(plan.get("qualification_groups", []))
    group_matches = _match_qualification_groups(text, groups)
    direct_terms = list(plan.get("core_terms", [])) + list(plan.get("historical_variants", []))
    direct_hits = [
        term for match in group_matches for term in match.get("matched_terms", [])
    ] if groups else _matches(text, direct_terms)
    related_hits = _matches(text, plan.get("related_non_equivalent", []))
    support_hits = _matches(text, plan.get("supporting_terms", []))
    generic_hits = _matches(text, plan.get("generic_terms", []))
    relation_pair_hits = []
    for pair in plan.get("relation_pairs", []):
        hits = _matches(text, pair)
        if len(hits) >= 2:
            relation_pair_hits.append(hits)

    provenance_eligible, provenance_role = _provenance_role(result, plan)
    target_match = _volume_match(result, plan.get("target_volumes", []))
    structural = bool(relation_pair_hits or (len(support_hits) >= 2))
    scope_only_match = bool(
        plan.get("plan_status", {}).get("scope_only_valid")
        and target_match and provenance_eligible
    )
    semantic_relevant = bool(direct_hits or structural or scope_only_match)
    routing = str(plan.get("routing_intent") or plan.get("intent") or "general_search")

    if direct_hits and provenance_eligible:
        if routing == "author_argument":
            candidate_class = "direct_author_text"
        elif routing == "apparat_question":
            candidate_class = "direct_apparat_evidence"
        else:
            candidate_class = "direct_source_evidence"
        rank_bucket = 0 if target_match else 1
    elif scope_only_match:
        candidate_class = "scoped_editorial_evidence"
        rank_bucket = 0
    elif structural and provenance_eligible:
        candidate_class = "structural_author_text" if routing == "author_argument" else "structural_source_evidence"
        rank_bucket = 2 if target_match else 3
    elif related_hits and provenance_eligible:
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

    evidence_eligible = bool(provenance_eligible and semantic_relevant)
    debug = {
        "candidate_class": candidate_class,
        "qualification_bucket": rank_bucket,
        "qualification_group_matches": group_matches,
        "direct_core_hits": list(dict.fromkeys(direct_hits)),
        "historical_or_core_hit_count": len(set(direct_hits)),
        "related_non_equivalent_hits": related_hits,
        "supporting_hits": support_hits,
        "relation_pair_hits": relation_pair_hits,
        "generic_hits": generic_hits,
        "target_scope_match": target_match,
        "semantic_relevant": semantic_relevant,
        "scope_only_match": scope_only_match,
        "provenance_eligible": provenance_eligible,
        "provenance_role": provenance_role,
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
    active = bool(plan.get("qualification_groups")) or bool(
        plan.get("matched_rule_ids")
    ) or plan.get("intent") == "claim_verification"
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


def _citation_ready(result: dict, plan: dict) -> bool:
    routing = str(plan.get("routing_intent") or plan.get("intent") or "general_search")
    layer = str(result.get("text_layer") or "")
    layer_ready = layer == "author_text" if routing == "author_argument" else (
        layer in EDITORIAL_LAYERS if routing == "apparat_question" else layer not in PARATEXT_LAYERS
    )
    return bool(
        result.get("evidence_eligible")
        and layer_ready
        and str(result.get("source_collection") or "").casefold() == "megadigital"
        and str(result.get("source_quality") or "").casefold() == "authoritative_digital"
        and str(result.get("page_label") or "").strip()
    )


def assess_retrieval_adequacy(results: list[dict], plan: dict, *,
                              term_probe: dict | None = None,
                              register_hits: list[dict] | None = None) -> dict:
    qualifications = [result.get("_qualification", {}) for result in results]
    classes = Counter(item.get("candidate_class", "unclassified") for item in qualifications)
    semantic = [result for result in results
                if result.get("_qualification", {}).get("semantic_relevant")]
    eligible = [result for result in results if result.get("evidence_eligible")]
    direct = [result for result in eligible
              if result.get("_qualification", {}).get("direct_core_hits")]
    verified_direct = [result for result in direct if result.get("text_layer") == "author_text"]
    target_direct = [result for result in direct
                     if result.get("_qualification", {}).get("target_scope_match")]
    citation_ready = [result for result in results if _citation_ready(result, plan)]
    probe_summary = (term_probe or {}).get("summary", {})
    probe_text_hits = sum(
        int(item.get("text_pages", 0)) for item in (term_probe or {}).get("terms", [])
    )

    if len(semantic) >= 2:
        semantic_status = "adequate"
    elif semantic:
        semantic_status = "partial"
    else:
        semantic_status = "insufficient"
    if verified_direct:
        provenance_status = "verified"
    elif eligible:
        provenance_status = "provisional"
    elif semantic:
        provenance_status = "out_of_scope_or_unverified"
    else:
        provenance_status = "insufficient"
    citation_status = "ready" if citation_ready else "not_ready"

    if direct:
        status = "adequate" if len(direct) >= 2 else "partial"
        reason = "direct evidence candidates retrieved"
    elif eligible:
        status = "partial"
        reason = "only structural or provisional evidence candidates retrieved"
    elif semantic:
        status = "partial"
        reason = "semantically relevant candidates require source-layer verification"
    elif probe_text_hits:
        status = "retrieval_gap"
        reason = "term probe found TEXT pages but no eligible candidate reached the result set"
    elif register_hits:
        status = "coverage_gap"
        reason = "Sachregister has navigation hits but indexed source text has no direct hit"
    else:
        status = "insufficient"
        reason = "no direct or structural evidence was retrieved"

    plan_status = plan.get("plan_status", {})
    if plan_status and not plan_status.get("valid_for_evidence", True):
        status = "insufficient"
        reason = "query plan is not valid for evidence qualification"

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
        "axes": {
            "semantic": {"status": semantic_status, "candidate_count": len(semantic)},
            "provenance": {"status": provenance_status, "eligible_count": len(eligible)},
            "citation": {"status": citation_status, "ready_count": len(citation_ready)},
        },
        "plan_status": plan_status,
        "candidate_classes": dict(classes),
        "eligible_evidence_count": len(eligible),
        "semantic_evidence_count": len(semantic),
        "direct_author_text_count": len(direct),
        "verified_direct_count": len(verified_direct),
        "citation_ready_count": len(citation_ready),
        "target_scope_direct_count": len(target_direct),
        "term_probe_text_hits": probe_text_hits,
        "register_navigation_hits": len(register_hits or []),
        "strong_claim_warning": quantifier_warning,
        "probe_summary": probe_summary,
    }
