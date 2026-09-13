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
                "required_for_evidence": bool(group.get("required_for_evidence")),
            })
    return matches


def _band_matches(actual: object, target: object) -> bool:
    actual_text = str(actual or "")
    target_text = str(target or "")
    if not target_text:
        return True
    if actual_text == target_text:
        return True
    return "." not in target_text and actual_text.startswith(target_text + ".")


def _volume_match(result: dict, target_volumes: list[dict]) -> bool:
    for volume in target_volumes:
        if not volume.get("abteilung") or result.get("abteilung") != volume["abteilung"]:
            continue
        if _band_matches(result.get("band"), volume.get("band")):
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


def _provenance_ready(result: dict, plan: dict) -> bool:
    """Return strict source-layer readiness without hiding provisional OCR candidates."""
    layer = str(result.get("text_layer") or "unclassified")
    routing = str(plan.get("routing_intent") or plan.get("intent") or "general_search")
    if routing == "author_argument":
        return layer == "author_text"
    if routing == "apparat_question":
        return layer in EDITORIAL_LAYERS
    return layer in {
        "author_text", "apparatus", "editorial_intro", "editorial_note"
    }


def qualify_candidate(result: dict, plan: dict) -> dict:
    text = str(result.get("text") or result.get("display_snippet") or "")
    groups = list(plan.get("qualification_groups", []))
    group_matches = _match_qualification_groups(text, groups)
    direct_terms = list(plan.get("core_terms", [])) + list(plan.get("historical_variants", []))
    required_groups = [
        group for group in groups if group.get("required_for_evidence")
    ]
    if required_groups:
        required_ids = {str(group.get("id")) for group in required_groups}
        focus_matches = [
            match for match in group_matches
            if str(match.get("id")) in required_ids
        ]
        direct_hits = [
            term for match in focus_matches for term in match.get("matched_terms", [])
        ]
    else:
        focus_matches = []
        direct_hits = [
            term for match in group_matches for term in match.get("matched_terms", [])
        ] if groups else _matches(text, direct_terms)
    core_hits = _matches(text, plan.get("core_terms", []))
    historical_hits = _matches(text, plan.get("historical_variants", []))
    related_hits = _matches(text, plan.get("related_non_equivalent", []))
    support_hits = _matches(text, plan.get("supporting_terms", []))
    generic_hits = _matches(text, plan.get("generic_terms", []))
    relation_pair_hits = []
    for pair in plan.get("relation_pairs", []):
        hits = _matches(text, pair)
        if len(hits) >= 2:
            relation_pair_hits.append(hits)

    provenance_eligible, provenance_role = _provenance_role(result, plan)
    provenance_ready = _provenance_ready(result, plan)
    target_match = _volume_match(result, plan.get("target_volumes", []))
    scope_ready = not bool(plan.get("target_volumes")) or target_match
    structural = bool(relation_pair_hits or (len(support_hits) >= 2))
    scope_only_match = bool(
        plan.get("plan_status", {}).get("scope_only_valid")
        and target_match and provenance_eligible
    )
    structural_eligible = structural and not required_groups
    semantic_relevant = bool(direct_hits or structural_eligible or scope_only_match)
    match_type_details = {
        "exact_phrase": list(dict.fromkeys(core_hits)),
        "lexical_variant": list(dict.fromkeys(historical_hits)),
        "semantic_related": list(dict.fromkeys(
            related_hits + support_hits
            + [term for pair in relation_pair_hits for term in pair]
        )),
    }
    match_types = [
        match_type for match_type in (
            "exact_phrase", "lexical_variant", "semantic_related"
        ) if match_type_details[match_type]
    ]
    if structural_eligible and "semantic_related" not in match_types:
        match_types.append("semantic_related")
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
    elif structural_eligible and provenance_eligible:
        candidate_class = "structural_author_text" if routing == "author_argument" else "structural_source_evidence"
        rank_bucket = 2 if target_match else 3
    elif related_hits and provenance_eligible:
        candidate_class = "related_concept_context"
        rank_bucket = 4
    elif direct_hits or related_hits or structural:
        candidate_class = "editorial_or_paratext_context"
        rank_bucket = 5
    elif required_groups and (group_matches or support_hits or generic_hits):
        candidate_class = "context_only_without_focus"
        rank_bucket = 6
    elif generic_hits:
        candidate_class = "generic_only"
        rank_bucket = 7
    else:
        candidate_class = "no_required_signal"
        rank_bucket = 8

    evidence_eligible = bool(provenance_eligible and semantic_relevant)
    claim_eligible = bool(evidence_eligible and scope_ready)
    claim_ready = bool(claim_eligible and provenance_ready)
    provisional_claim_ready = bool(claim_eligible and not provenance_ready)
    debug = {
        "candidate_class": candidate_class,
        "qualification_bucket": rank_bucket,
        "qualification_group_matches": group_matches,
        "direct_core_hits": list(dict.fromkeys(direct_hits)),
        "exact_phrase_hits": match_type_details["exact_phrase"],
        "lexical_variant_hits": match_type_details["lexical_variant"],
        "semantic_related_hits": match_type_details["semantic_related"],
        "match_types": match_types,
        "match_type_details": match_type_details,
        "focus_required": bool(required_groups),
        "focus_terms": list(plan.get("focus_terms", [])),
        "focus_hits": list(dict.fromkeys(direct_hits)) if required_groups else [],
        "focus_missing": bool(required_groups and not direct_hits),
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
        "semantic_ready": semantic_relevant,
        "scope_ready": scope_ready,
        "provenance_ready": provenance_ready,
        "claim_eligible": claim_eligible,
        "claim_ready": claim_ready,
        "provisional_claim_ready": provisional_claim_ready,
        "author_evidence_eligible": claim_eligible,
    }
    result["_qualification"] = debug
    result.setdefault("_debug", {}).update(debug)
    result["evidence_eligible"] = evidence_eligible
    result["claim_eligible"] = claim_eligible
    result["claim_ready"] = claim_ready
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
    claim_eligible = [result for result in results
                      if result.get("_qualification", {}).get("claim_eligible")]
    claim_ready = [result for result in results
                   if result.get("_qualification", {}).get("claim_ready")]
    direct = [result for result in claim_eligible
              if result.get("_qualification", {}).get("direct_core_hits")]
    verified_direct = [result for result in direct
                       if result.get("_qualification", {}).get("claim_ready")]
    target_direct = [result for result in direct
                     if result.get("_qualification", {}).get("target_scope_match")]
    out_of_scope_direct = [
        result for result in eligible
        if result.get("_qualification", {}).get("direct_core_hits")
        and not result.get("_qualification", {}).get("scope_ready")
    ]
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
        if len(verified_direct) >= 2:
            status = "adequate"
            reason = "verified direct evidence candidates retrieved"
        else:
            status = "partial"
            reason = "direct evidence is in scope but source-layer verification remains"
    elif out_of_scope_direct and plan.get("target_volumes"):
        status = "retrieval_gap"
        reason = "direct matches exist only outside the requested work or volume scope"
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
            "scope": {
                "status": "ready" if claim_eligible else "insufficient",
                "claim_eligible_count": len(claim_eligible),
            },
        },
        "plan_status": plan_status,
        "candidate_classes": dict(classes),
        "eligible_evidence_count": len(eligible),
        "semantic_evidence_count": len(semantic),
        "direct_author_text_count": len(direct),
        "verified_direct_count": len(verified_direct),
        "claim_eligible_count": len(claim_eligible),
        "claim_ready_count": len(claim_ready),
        "provisional_claim_count": len(claim_eligible) - len(claim_ready),
        "citation_ready_count": len(citation_ready),
        "target_scope_direct_count": len(target_direct),
        "out_of_scope_direct_count": len(out_of_scope_direct),
        "term_probe_text_hits": probe_text_hits,
        "register_navigation_hits": len(register_hits or []),
        "strong_claim_warning": quantifier_warning,
        "probe_summary": probe_summary,
    }
