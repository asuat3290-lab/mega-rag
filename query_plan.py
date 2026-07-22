#!/usr/bin/env python3
"""Deterministic, inspectable query planning for MEGA retrieval.

The planner never calls an external model.  It combines the existing glossary
and lexical analyser with small, reviewable rules for historical spellings,
related-but-non-equivalent concepts, scope hints, and claim strength.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from glossary_loader import (
    expand_with_glossary,
    get_glossary_expansions,
    load_glossary,
    normalize_key,
)
from query_analyzer import analyze_query


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = BASE_DIR / "query_rules.yaml"
QUERY_PLAN_PROTOCOL = "mega-query-plan-v1"


def _dedupe(values: list[Any]) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            key = normalize_key(str(value))
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def load_query_rules(path: str | Path | None = None) -> dict:
    rule_path = Path(path) if path else DEFAULT_RULES_PATH
    if not rule_path.exists():
        return {"version": "missing", "rules": []}
    payload = yaml.safe_load(rule_path.read_text(encoding="utf-8")) or {}
    payload.setdefault("version", "unversioned")
    payload.setdefault("rules", [])
    payload.setdefault("contextual_generic_terms", [])
    payload.setdefault("strong_quantifiers", {})
    return payload


def compute_planner_version(rules: dict | None = None) -> str:
    rules = rules or load_query_rules()
    stable = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
    return f"{rules.get('version', 'unversioned')}:{digest}"


def _contains(text: str, candidate: str) -> bool:
    return normalize_key(candidate) in normalize_key(text)


def _matching_rules(question: str, rules: dict) -> list[dict]:
    return [
        deepcopy(rule)
        for rule in rules.get("rules", [])
        if any(_contains(question, trigger) for trigger in rule.get("triggers", []))
    ]


def _claim_strength(question: str, rules: dict) -> tuple[str, list[str]]:
    found: list[str] = []
    strength = "ordinary"
    rank = {"ordinary": 0, "predominant": 1, "universal": 2,
            "exclusive": 3, "negative_universal": 3}
    for category, terms in rules.get("strong_quantifiers", {}).items():
        hits = [term for term in terms if _contains(question, term)]
        if hits:
            found.extend(hits)
            if rank.get(category, 0) > rank.get(strength, 0):
                strength = category
    return strength, _dedupe(found)


def _detailed_intent(question: str, base_intent: str,
                     override: str | None, matched_rules: list[dict]) -> str:
    if override:
        return override
    lowered = normalize_key(question)
    if any(value in lowered for value in ("词频", "频次", "word frequency", "worthaeufigkeit")):
        return "frequency_analysis"
    if any(value in lowered for value in ("出处", "哪一页", "source", "fundstelle")):
        return "source_lookup"
    if matched_rules and any(rule.get("intent") == "concept_relation" for rule in matched_rules):
        return "concept_relation"
    return base_intent


def _routing_intent(detailed_intent: str, base_intent: str) -> str:
    if detailed_intent in {"claim_verification", "concept_relation", "frequency_analysis"}:
        return "author_argument"
    if detailed_intent in {"apparat_question", "source_question"}:
        return "apparat_question"
    return base_intent


def _parse_volume(value: Any) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"([IVX]+)(?:\s*/\s*([0-9]+(?:\.[0-9]+)?))?", text, re.I)
    if not match:
        return {"label": text, "abteilung": None, "band": None}
    return {
        "label": text,
        "abteilung": match.group(1).upper(),
        "band": match.group(2),
    }


def _add_term(registry: dict[str, dict], term: str, role: str,
              source: str, equivalent: bool = True) -> None:
    cleaned = str(term or "").strip()
    if len(cleaned) < 2:
        return
    key = normalize_key(cleaned)
    priority = {"core": 5, "historical": 4, "related": 3,
                "supporting": 2, "generic": 1}
    current = registry.get(key)
    if current is None:
        registry[key] = {
            "term": cleaned,
            "role": role,
            "source": [source],
            "equivalent": bool(equivalent),
        }
        return
    current["source"] = _dedupe(current.get("source", []) + [source])
    if priority.get(role, 0) > priority.get(current.get("role", ""), 0):
        current["role"] = role
        current["term"] = cleaned
    current["equivalent"] = bool(current.get("equivalent", True) and equivalent)


def _term_lists(registry: dict[str, dict]) -> dict[str, list[str]]:
    roles = {name: [] for name in ("core", "historical", "related", "supporting", "generic")}
    for entry in registry.values():
        roles.setdefault(entry["role"], []).append(entry["term"])
    return {key: _dedupe(value) for key, value in roles.items()}


def _build_branches(terms: dict, target_volumes: list[dict],
                    relation_pairs: list[list[str]], routing_intent: str) -> list[dict]:
    branches: list[dict] = []
    exact = _dedupe(terms["core"] + terms["historical"])
    if exact:
        branches.append({"id": "exact_core", "kind": "lexical", "terms": exact,
                         "route": "main_text" if routing_intent == "author_argument" else "all"})
    if terms["related"]:
        branches.append({"id": "related_concepts", "kind": "lexical",
                         "terms": terms["related"], "equivalent": False, "route": "all"})
    structural = _dedupe(terms["supporting"] + [term for pair in relation_pairs for term in pair])
    if structural:
        branches.append({"id": "structural_relation", "kind": "cooccurrence",
                         "terms": structural, "pairs": relation_pairs, "route": "main_text"})
    if target_volumes and exact:
        branches.append({"id": "target_scope", "kind": "scoped", "terms": exact,
                         "target_volumes": target_volumes,
                         "route": "main_text" if routing_intent == "author_argument" else "all"})
    branches.append({"id": "sachregister", "kind": "register_navigation",
                     "terms": _dedupe(exact + terms["related"]), "evidence_eligible": False})
    branches.append({"id": "global_semantic", "kind": "hybrid",
                     "terms": _dedupe(exact + terms["supporting"]), "route": "all"})
    return branches


def build_query_plan(question: str, *, glossary: dict | None = None,
                     rules: dict | None = None, intent_override: str | None = None,
                     mode: str = "local", refinement: dict | None = None) -> dict:
    """Build a stable local query plan without spending API tokens."""
    question = str(question or "").strip()
    if not question:
        raise ValueError("question must not be empty")
    glossary = glossary or load_glossary()
    rules = rules or load_query_rules()
    expanded, matched_terms, hints = expand_with_glossary(question, glossary)
    base_profile = analyze_query(question, expanded, matched_terms, glossary)
    matched_rules = _matching_rules(question, rules)
    detailed_intent = _detailed_intent(
        question, base_profile.get("intent", "general_search"), intent_override, matched_rules
    )
    routing_intent = _routing_intent(detailed_intent, base_profile.get("intent", "general_search"))

    registry: dict[str, dict] = {}
    for value in base_profile.get("core_expansions", []):
        _add_term(registry, value, "core", "glossary_expansion")
    for value in base_profile.get("lexical_core", []):
        _add_term(registry, value, "core", "lexical_fallback")
    for value in base_profile.get("core_terms", []):
        _add_term(registry, value, "core", "query_profile")
    for value in base_profile.get("generic_terms", []):
        _add_term(registry, value, "generic", "query_profile")

    relation_pairs: list[list[str]] = []
    rule_role_map: dict[str, str] = {}
    target_works: list[str] = list(base_profile.get("work_terms", []))
    target_volumes: list[dict] = []
    if base_profile.get("target_abteilung"):
        target_volumes.append({
            "label": "/".join(filter(None, [base_profile["target_abteilung"], base_profile.get("target_band")])),
            "abteilung": base_profile["target_abteilung"],
            "band": base_profile.get("target_band"),
        })
    target_topics: list[str] = []
    required_signals: dict[str, list] = {}
    negative_signals: dict[str, list] = {}
    for rule in matched_rules:
        declared_roles = (
            ("core_terms", "core"),
            ("historical_variants", "historical"),
            ("related_non_equivalent", "related"),
            ("supporting_terms", "supporting"),
            ("generic_terms", "generic"),
        )
        for field, role in declared_roles:
            for value in rule.get(field, []):
                _add_term(
                    registry, value, role, f"rule:{rule.get('id')}",
                    equivalent=role != "related",
                )
                rule_role_map.setdefault(normalize_key(value), role)
        relation_pairs.extend(rule.get("relation_pairs", []))
        target_works.extend(rule.get("target_works", []))
        target_topics.extend(rule.get("target_topics", []))
        for value in rule.get("target_volumes", []):
            parsed = _parse_volume(value)
            if parsed:
                target_volumes.append(parsed)
        for key, values in rule.get("required_signals", {}).items():
            required_signals.setdefault(key, []).extend(values or [])
        for key, values in rule.get("negative_signals", {}).items():
            negative_signals.setdefault(key, []).extend(values or [])

    # A matched rule may reclassify an existing glossary concept.  Apply the
    # role to the whole glossary expansion family, while retaining explicit
    # distinctions inside a family (for example Entfremdung vs Ent盲u脽erung).
    role_rank = {"core": 5, "historical": 4, "related": 3,
                 "supporting": 2, "generic": 1}
    for glossary_term in matched_terms:
        family = [glossary_term] + get_glossary_expansions(glossary_term, glossary)
        declared = [rule_role_map[normalize_key(value)] for value in family
                    if normalize_key(value) in rule_role_map]
        if not declared:
            continue
        family_role = max(declared, key=lambda role: role_rank[role])
        for value in family:
            key = normalize_key(value)
            if key not in registry:
                continue
            role = rule_role_map.get(key, family_role)
            registry[key]["role"] = role
            if role == "related":
                registry[key]["equivalent"] = False

    # Explicit rule roles override the legacy profile for exact terms.
    for key, role in rule_role_map.items():
        if key in registry:
            registry[key]["role"] = role
            if role == "related":
                registry[key]["equivalent"] = False

    # Generic terms are demoted only in multi-concept queries.  A query for
    # "Kapital" alone must remain searchable as a core concept.
    contextual_generic = {normalize_key(value) for value in rules.get("contextual_generic_terms", [])}
    discriminating = [entry for entry in registry.values()
                      if entry["role"] in {"core", "historical"}
                      and normalize_key(entry["term"]) not in contextual_generic]
    if discriminating:
        rule_generic = {
            normalize_key(value)
            for rule in matched_rules for value in rule.get("generic_terms", [])
        }
        generic_keys = contextual_generic | rule_generic
        inflection_suffixes = {"s", "es", "e", "en", "er", "em", "ern"}
        for key, entry in registry.items():
            is_generic = key in generic_keys or any(
                key.startswith(generic)
                and key[len(generic):] in inflection_suffixes
                for generic in generic_keys if len(generic) >= 4
            )
            if is_generic:
                entry["role"] = "generic"

    terms = _term_lists(registry)
    strength, quantifiers = _claim_strength(question, rules)
    target_volumes = _dedupe(target_volumes)
    warnings: list[str] = []
    if strength != "ordinary":
        warnings.append("Strong quantifier detected; absence of a hit is not evidence of absence.")
    if not terms["core"] and not terms["historical"]:
        warnings.append("No discriminating core term was found; retrieval may be broad.")

    plan = {
        "protocol": QUERY_PLAN_PROTOCOL,
        "planner_version": compute_planner_version(rules),
        "mode": mode,
        "question": question,
        "expanded_query": expanded,
        "matched_glossary_terms": matched_terms,
        "glossary_hints": hints,
        "intent": detailed_intent,
        "routing_intent": routing_intent,
        "claim_strength": strength,
        "quantifiers": quantifiers,
        "core_terms": terms["core"],
        "historical_variants": terms["historical"],
        "related_non_equivalent": terms["related"],
        "supporting_terms": terms["supporting"],
        "generic_terms": terms["generic"],
        "term_registry": list(registry.values()),
        "target_works": _dedupe(target_works),
        "target_volumes": target_volumes,
        "target_topics": _dedupe(target_topics),
        "relation_pairs": _dedupe(relation_pairs),
        "required_signals": {key: _dedupe(value) for key, value in required_signals.items()},
        "negative_signals": {key: _dedupe(value) for key, value in negative_signals.items()},
        "branches": _build_branches(terms, target_volumes, relation_pairs, routing_intent),
        "base_query_profile": base_profile,
        "matched_rule_ids": [rule.get("id") for rule in matched_rules],
        "warnings": warnings,
    }
    if refinement:
        return merge_query_plan(plan, refinement, source=mode)
    return plan


def _refinement_terms(value: Any, field: str, limit: int = 40) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"query-plan refinement field {field} must be a list")
    output = []
    for item in value[:limit]:
        text = str(item or "").strip()
        if len(text) < 2 or len(text) > 120:
            continue
        output.append(text)
    return _dedupe(output)


def merge_query_plan(plan: dict, refinement: dict, *, source: str = "agent_supplied") -> dict:
    """Merge a model/agent suggestion through a strict, auditable schema."""
    if not isinstance(refinement, dict):
        raise ValueError("query-plan refinement must be a JSON object")
    output = deepcopy(plan)
    role_fields = (
        "core_terms", "historical_variants", "related_non_equivalent",
        "supporting_terms", "generic_terms",
    )
    role_values = {
        field: _dedupe(list(output.get(field, [])) + _refinement_terms(
            refinement.get(field), field
        ))
        for field in role_fields
    }
    # The refinement's explicit role wins for newly supplied terms.
    supplied_role = {}
    for field in role_fields:
        for term in _refinement_terms(refinement.get(field), field):
            supplied_role[normalize_key(term)] = field
    for field in role_fields:
        role_values[field] = [
            term for term in role_values[field]
            if supplied_role.get(normalize_key(term), field) == field
        ]
        output[field] = role_values[field]

    output["target_works"] = _dedupe(
        list(output.get("target_works", []))
        + _refinement_terms(refinement.get("target_works"), "target_works", 20)
    )
    output["target_topics"] = _dedupe(
        list(output.get("target_topics", []))
        + _refinement_terms(refinement.get("target_topics"), "target_topics", 30)
    )
    volumes = list(output.get("target_volumes", []))
    raw_volumes = refinement.get("target_volumes") or []
    if not isinstance(raw_volumes, list):
        raise ValueError("query-plan refinement field target_volumes must be a list")
    for value in raw_volumes[:30]:
        if isinstance(value, dict):
            parsed = {
                "label": str(value.get("label") or "/".join(filter(None, [
                    str(value.get("abteilung") or ""), str(value.get("band") or "")
                ]))),
                "abteilung": str(value.get("abteilung") or "") or None,
                "band": str(value.get("band")) if value.get("band") not in (None, "") else None,
            }
        else:
            parsed = _parse_volume(value)
        if parsed:
            volumes.append(parsed)
    output["target_volumes"] = _dedupe(volumes)

    pairs = refinement.get("relation_pairs") or []
    if not isinstance(pairs, list):
        raise ValueError("query-plan refinement field relation_pairs must be a list")
    normalized_pairs = []
    for pair in pairs[:30]:
        if isinstance(pair, list):
            terms = _refinement_terms(pair, "relation_pair", 6)
            if len(terms) >= 2:
                normalized_pairs.append(terms)
    output["relation_pairs"] = _dedupe(
        list(output.get("relation_pairs", [])) + normalized_pairs
    )
    allowed_intents = {
        "author_argument", "apparat_question", "general_search",
        "concept_relation", "claim_verification", "frequency_analysis", "source_lookup",
    }
    refined_intent = str(refinement.get("intent") or "").strip()
    if refined_intent:
        if refined_intent not in allowed_intents:
            raise ValueError(f"unsupported refined intent: {refined_intent}")
        output["intent"] = refined_intent
        output["routing_intent"] = _routing_intent(refined_intent, output["routing_intent"])

    terms = {
        "core": output["core_terms"], "historical": output["historical_variants"],
        "related": output["related_non_equivalent"],
        "supporting": output["supporting_terms"], "generic": output["generic_terms"],
    }
    output["branches"] = _build_branches(
        terms, output["target_volumes"], output["relation_pairs"], output["routing_intent"]
    )
    digest = hashlib.sha256(
        json.dumps(refinement, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    output["mode"] = source
    output["planner_version"] = f"{output['planner_version']}+{source}:{digest}"
    output["refinement"] = {"source": source, "payload": refinement}
    return output


def compact_plan(plan: dict) -> dict:
    """Return the low-token subset intended for agent/API exchange."""
    keys = (
        "protocol", "planner_version", "intent", "routing_intent",
        "claim_strength", "core_terms", "historical_variants",
        "related_non_equivalent", "supporting_terms", "generic_terms",
        "target_works", "target_volumes", "target_topics", "branches", "warnings",
    )
    return {key: plan.get(key) for key in keys}
