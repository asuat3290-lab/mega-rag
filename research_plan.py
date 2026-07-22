#!/usr/bin/env python3
"""Question-level planning above the lexical MEGA QueryPlan.

QueryPlan answers "which terms and volumes should be searched?". ResearchPlan
answers "which parts of the research question must be answered, and what kind
of evidence does each part require?". The local planner is deterministic and
never calls an API model.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from glossary_loader import load_glossary, normalize_key
from query_plan import (
    build_query_plan,
    compact_plan,
    compute_planner_version,
    load_query_rules,
)


RESEARCH_PLAN_PROTOCOL = "mega-research-plan-v1"
RESEARCH_PLAN_VERSION = "research-question-planner-v1"

SUBQUESTION_TYPES = {
    "textual_reconstruction",
    "concept_relation",
    "concept_bridge",
    "diachronic_comparison",
    "philology",
    "counter_evidence",
    "quantitative",
    "modern_application",
    "external_empirical",
    "general_research",
}

CORPUS_TYPES = {"mega", "external_empirical"}

_MODERN_MARKERS = (
    "如今", "当代", "现代", "今天", "当前", "现实中", "人工智能", "大模型",
    "机器学习", "算法", "平台经济", "数字经济", "自动化", "ai", "llm",
)
_APPLICATION_MARKERS = (
    "用于", "应用于", "运用于", "联系", "结合", "如何用", "怎样用", "怎么用",
    "说明", "解释当代", "现实意义", "今天看来",
)
_DIACHRONIC_MARKERS = (
    "不同时期", "各时期", "变化", "演变", "发展过程", "早期", "晚期", "后来",
    "diachronic", "entwicklung", "wandel",
)
_PHILOLOGY_MARKERS = (
    "异文", "版本", "编者", "手稿关系", "出处", "apparat", "variant",
    "manuscript", "editorial",
)
_QUANTITATIVE_MARKERS = (
    "词频", "频次", "每万词", "分布", "统计", "word frequency", "häufigkeit",
)
_COUNTER_MARKERS = (
    "反作用", "反趋势", "抵消", "限制条件", "反例", "反证", "counter",
    "entgegenwirk",
)
_BRIDGE_MARKERS = (
    "关系", "联系", "结合", "如何用", "怎样用", "怎么用", "借助", "说明",
    "中介", "relation", "zusammenhang",
)

# These phrases express the form of a question rather than a research concept.
_QUESTION_FRAMES = (
    "马克思和恩格斯", "马克思", "恩格斯", "怎样讨论", "如何讨论", "怎么讨论",
    "怎样论述", "如何论述", "怎么论述", "怎样理解", "如何理解", "怎么理解",
    "是否会", "是否", "如何用", "怎样用", "怎么用", "可以用", "利用", "借助",
    "来说明", "说明", "解释", "分析", "研究", "讨论", "论述", "认为", "对于",
    "关于", "其中", "这个问题", "这一问题", "问题", "概念", "范畴", "理论",
    "会不会", "会", "怎样", "如何", "怎么", "为什么", "什么", "哪些", "何时",
    "如今的", "当代的", "现代的", "当前的", "如今", "当代", "现代", "当前",
    "降低", "提高", "影响", "发生", "形成", "表现", "来看", "中的", "中", "的",
)


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


def split_research_question(question: str) -> list[str]:
    """Split only at strong clause boundaries; do not split ordinary noun phrases."""
    normalized = re.sub(r"\s+", " ", str(question or "").strip())
    clauses = [
        value.strip(" -—:：")
        for value in re.split(r"[，,；;。！？?\n]+", normalized)
        if value.strip(" -—:：")
    ]
    if len(clauses) == 1 and len(normalized) > 28:
        secondary = re.split(r"(?:并且|同时|然后|以及还要)", normalized)
        secondary = [value.strip(" -—:：") for value in secondary if value.strip(" -—:：")]
        if len(secondary) > 1:
            clauses = secondary
    return clauses or [normalized]


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = normalize_key(text)
    return any(normalize_key(marker) in normalized for marker in markers)


def _question_type(question: str, clauses: list[str]) -> str:
    if _contains_any(question, _QUANTITATIVE_MARKERS):
        return "quantitative"
    if _contains_any(question, _PHILOLOGY_MARKERS):
        return "philology"
    if _contains_any(question, _DIACHRONIC_MARKERS):
        return "diachronic_comparison"
    if _contains_any(question, _MODERN_MARKERS):
        return "modern_application"
    if len(clauses) > 1:
        return "multi_part"
    if _contains_any(question, _BRIDGE_MARKERS):
        return "concept_relation"
    return "textual_research"


def _rule_map(rules: dict) -> dict[str, dict]:
    return {str(rule.get("id")): rule for rule in rules.get("rules", []) if rule.get("id")}


def _matched_rules_for_clause(clause: str, rules: dict) -> list[dict]:
    normalized = normalize_key(clause)
    return [
        rule for rule in rules.get("rules", [])
        if any(normalize_key(trigger) in normalized for trigger in rule.get("triggers", []))
    ]


def _source_concepts(clause: str, query_plan: dict, matched_rules: list[dict]) -> list[str]:
    concepts = list(query_plan.get("matched_glossary_terms", []))
    for rule in matched_rules:
        concepts.append(str(rule.get("label") or rule.get("id") or "").strip())
    normalized_clause = normalize_key(clause)
    for term in query_plan.get("core_terms", []):
        if normalize_key(term) in normalized_clause:
            concepts.append(str(term))
    return _dedupe([value for value in concepts if value])


def _external_concepts(clause: str) -> list[str]:
    normalized = normalize_key(clause)
    found = []
    for marker in _MODERN_MARKERS:
        if normalize_key(marker) in normalized and marker not in {
            "如今", "当代", "现代", "今天", "当前", "现实中"
        }:
            found.append(marker.upper() if marker in {"ai", "llm"} else marker)
    return _dedupe(found)


def _meaningful_residue(
    clause: str,
    source_concepts: list[str],
    query_plan: dict,
    external_concepts: list[str],
) -> list[str]:
    residue = str(clause)
    removable = list(source_concepts) + list(external_concepts)
    removable += [
        term for term in query_plan.get("core_terms", [])
        if normalize_key(term) in normalize_key(clause)
    ]
    for value in sorted(_dedupe(removable), key=lambda item: len(str(item)), reverse=True):
        residue = re.sub(re.escape(str(value)), " ", residue, flags=re.I)
    for phrase in sorted(_QUESTION_FRAMES, key=len, reverse=True):
        residue = residue.replace(phrase, " ")
    residue = re.sub(r"[\s\W_\d]+", " ", residue, flags=re.UNICODE).strip()

    output = []
    for chinese in re.findall(r"[\u3400-\u9fff]{2,}", residue):
        candidate = chinese.strip()
        if len(candidate) >= 2:
            output.append(candidate)
    # German/English lexical queries are already handled by QueryPlan. Only retain
    # longer unknown Latin phrases when no lexical core was found.
    if not query_plan.get("core_terms"):
        latin = re.findall(r"[A-Za-zÄÖÜäöüẞß][A-Za-zÄÖÜäöüẞß\-]{3,}", residue)
        output.extend(latin)
    return _dedupe(output)


def _subquestion_type(
    clause: str,
    overall_type: str,
    has_external: bool,
) -> str:
    if has_external:
        return "modern_application" if overall_type == "modern_application" else "external_empirical"
    if _contains_any(clause, _COUNTER_MARKERS):
        return "counter_evidence"
    if _contains_any(clause, _QUANTITATIVE_MARKERS):
        return "quantitative"
    if _contains_any(clause, _PHILOLOGY_MARKERS):
        return "philology"
    if _contains_any(clause, _DIACHRONIC_MARKERS):
        return "diachronic_comparison"
    if _contains_any(clause, _BRIDGE_MARKERS):
        return "concept_bridge"
    return "textual_reconstruction"


def _evidence_requirement(subquestion_type: str) -> str:
    return {
        "textual_reconstruction": "author_text",
        "concept_relation": "author_text_relation",
        "concept_bridge": "author_text_plus_explicit_inference",
        "diachronic_comparison": "dated_author_text_across_periods",
        "philology": "author_text_plus_apparatus",
        "counter_evidence": "author_text_countertendency",
        "quantitative": "corpus_level_counts",
        "modern_application": "mega_theory_plus_external_empirical",
        "external_empirical": "external_empirical",
    }.get(subquestion_type, "author_text")


def _required_corpora(has_mega: bool, has_external: bool) -> list[str]:
    corpora = []
    if has_mega:
        corpora.append("mega")
    if has_external:
        corpora.append("external_empirical")
    return corpora or ["mega"]


def _compact_subquery_plan(plan: dict) -> dict:
    """Keep research plans inspectable without embedding a second retrieval tree."""
    return {
        "protocol": plan.get("protocol"),
        "planner_version": plan.get("planner_version"),
        "intent": plan.get("intent"),
        "routing_intent": plan.get("routing_intent"),
        "claim_strength": plan.get("claim_strength"),
        "core_terms": list(plan.get("core_terms", []))[:20],
        "historical_variants": list(plan.get("historical_variants", []))[:12],
        "related_non_equivalent": list(plan.get("related_non_equivalent", []))[:12],
        "supporting_terms": list(plan.get("supporting_terms", []))[:20],
        "generic_terms": list(plan.get("generic_terms", []))[:12],
        "target_works": list(plan.get("target_works", []))[:8],
        "target_volumes": list(plan.get("target_volumes", []))[:12],
        "target_topics": list(plan.get("target_topics", []))[:8],
        "matched_rule_ids": list(plan.get("matched_rule_ids", [])),
        "warnings": list(plan.get("warnings", [])),
    }



def _auto_rule_subquestions(
    matched_rule_ids: list[str],
    rules: dict,
    existing_ids: set[str],
) -> list[dict]:
    output = []
    mapping = _rule_map(rules)
    for rule_id in matched_rule_ids:
        rule = mapping.get(str(rule_id), {})
        for index, branch in enumerate(rule.get("research_branches", [])[:6], start=1):
            branch_id = str(branch.get("id") or f"{rule_id}_required_{index}")
            if branch_id in existing_ids:
                continue
            question = str(branch.get("question") or "").strip()
            if not question:
                continue
            refinement = branch.get("query_refinement") or {}
            local_plan = build_query_plan(
                question,
                mode="agent_supplied" if refinement else "local",
                refinement=refinement or None,
            )
            sub_type = str(branch.get("type") or "textual_reconstruction")
            if sub_type not in SUBQUESTION_TYPES:
                sub_type = "general_research"
            required_corpus = [
                value for value in branch.get("required_corpus", ["mega"])
                if value in CORPUS_TYPES
            ] or ["mega"]
            output.append({
                "id": branch_id,
                "question": question,
                "type": sub_type,
                "origin": "required_by_rule",
                "source_clause": None,
                "covered_concepts": [str(rule.get("label") or rule_id)],
                "external_concepts": [],
                "unmapped_concepts": [],
                "required_corpus": required_corpus,
                "required_evidence": _evidence_requirement(sub_type),
                "query_intent": local_plan.get("routing_intent", "author_argument"),
                "query_refinement": refinement or None,
                "query_plan": _compact_subquery_plan(local_plan),
            })
            existing_ids.add(branch_id)
    return output


def _relations(subquestions: list[dict]) -> list[dict]:
    textual = [item["id"] for item in subquestions if item["type"] == "textual_reconstruction"]
    relations = []
    for item in subquestions:
        if item["type"] == "concept_bridge":
            for source in textual[:3]:
                relations.append({"from": source, "to": item["id"], "type": "conceptual_bridge"})
        elif item["type"] in {"modern_application", "external_empirical"}:
            for source in textual[:3]:
                relations.append({"from": source, "to": item["id"], "type": "application_boundary"})
        elif item["type"] == "counter_evidence":
            for source in textual[:1]:
                relations.append({"from": item["id"], "to": source, "type": "qualifies"})
    return _dedupe(relations)


def _coverage(covered: list[str], external: list[str], unmapped: list[str]) -> float:
    recognized = len(_dedupe(covered + external))
    total = recognized + len(_dedupe(unmapped))
    return round(recognized / total, 3) if total else 0.0


def build_research_plan(
    question: str,
    *,
    glossary: dict | None = None,
    rules: dict | None = None,
    mode: str = "local",
    refinement: dict | None = None,
) -> dict:
    """Build a deterministic question graph without retrieving evidence."""
    question = " ".join(str(question or "").strip().split())
    if not question:
        raise ValueError("question must not be empty")
    glossary = glossary or load_glossary()
    rules = rules or load_query_rules()
    clauses = split_research_question(question)
    overall_type = _question_type(question, clauses)
    subquestions = []
    covered_all: list[str] = []
    external_all: list[str] = []
    unmapped_all: list[str] = []
    matched_rule_ids: list[str] = []

    for index, clause in enumerate(clauses, start=1):
        query_plan = build_query_plan(clause, glossary=glossary, rules=rules)
        matched_rules = _matched_rules_for_clause(clause, rules)
        source_concepts = _source_concepts(clause, query_plan, matched_rules)
        external = _external_concepts(clause)
        residue = _meaningful_residue(clause, source_concepts, query_plan, external)
        sub_type = _subquestion_type(clause, overall_type, bool(external))
        has_mega = bool(source_concepts or query_plan.get("core_terms"))
        required_corpus = _required_corpora(has_mega, bool(external))
        sub_id = f"Q{index:02d}"
        subquestions.append({
            "id": sub_id,
            "question": clause,
            "type": sub_type,
            "origin": "user_clause",
            "source_clause": clause,
            "covered_concepts": source_concepts,
            "external_concepts": external,
            "unmapped_concepts": residue,
            "required_corpus": required_corpus,
            "required_evidence": _evidence_requirement(sub_type),
            "query_intent": query_plan.get("routing_intent", "author_argument"),
            "query_refinement": None,
            "query_plan": _compact_subquery_plan(query_plan),
        })
        covered_all.extend(source_concepts)
        external_all.extend(external)
        unmapped_all.extend(residue)
        matched_rule_ids.extend(query_plan.get("matched_rule_ids", []))

    subquestions.extend(_auto_rule_subquestions(
        _dedupe(matched_rule_ids), rules, {item["id"] for item in subquestions}
    ))
    covered_all = _dedupe(covered_all)
    external_all = _dedupe(external_all)
    unmapped_all = [
        value for value in _dedupe(unmapped_all)
        if normalize_key(value) not in {
            normalize_key(item) for item in covered_all + external_all
        }
    ]
    coverage = _coverage(covered_all, external_all, unmapped_all)
    external_required = any(
        "external_empirical" in item.get("required_corpus", []) for item in subquestions
    )
    warnings = []
    if unmapped_all:
        warnings.append(
            "Meaningful concepts remain unmapped; use agent refinement or bounded hybrid planning."
        )
    if external_required:
        warnings.append(
            "The contemporary/empirical part cannot be established from MEGA evidence alone."
        )
    plan = {
        "protocol": RESEARCH_PLAN_PROTOCOL,
        "planner_version": (
            f"{RESEARCH_PLAN_VERSION}:{compute_planner_version(rules)}"
        ),
        "mode": mode,
        "question": question,
        "question_type": overall_type,
        "clauses": clauses,
        "subquestions": subquestions,
        "relations": _relations(subquestions),
        "covered_concepts": covered_all,
        "external_concepts": external_all,
        "unmapped_concepts": unmapped_all,
        "question_coverage": coverage,
        "coverage_status": "complete" if not unmapped_all else "incomplete",
        "needs_refinement": bool(unmapped_all),
        "external_evidence_required": external_required,
        "matched_rule_ids": _dedupe(matched_rule_ids),
        "warnings": warnings,
    }
    if refinement:
        return merge_research_plan(plan, refinement, source=mode)
    return plan


def _validated_subquestion(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("research-plan subquestions must be JSON objects")
    question = str(raw.get("question") or "").strip()
    if not question or len(question) > 500:
        raise ValueError("research-plan subquestion requires a concise question")
    sub_type = str(raw.get("type") or "general_research").strip()
    if sub_type not in SUBQUESTION_TYPES:
        raise ValueError(f"unsupported research subquestion type: {sub_type}")
    required_corpus = [
        str(value) for value in raw.get("required_corpus", ["mega"])
        if str(value) in CORPUS_TYPES
    ] or ["mega"]
    query_refinement = raw.get("query_refinement")
    if query_refinement is not None and not isinstance(query_refinement, dict):
        raise ValueError("subquestion query_refinement must be an object")
    query_plan = build_query_plan(
        question,
        mode="agent_supplied" if query_refinement else "local",
        refinement=query_refinement,
    )
    return {
        "id": str(raw.get("id") or f"A{index:02d}")[:60],
        "question": question,
        "type": sub_type,
        "origin": "agent_refinement",
        "source_clause": raw.get("source_clause"),
        "covered_concepts": _dedupe([
            str(value) for value in raw.get("covered_concepts", []) if str(value).strip()
        ]),
        "external_concepts": _dedupe([
            str(value) for value in raw.get("external_concepts", []) if str(value).strip()
        ]),
        "unmapped_concepts": [],
        "required_corpus": required_corpus,
        "required_evidence": str(
            raw.get("required_evidence") or _evidence_requirement(sub_type)
        )[:120],
        "query_intent": query_plan.get("routing_intent", "author_argument"),
        "query_refinement": query_refinement,
        "query_plan": _compact_subquery_plan(query_plan),
    }


def merge_research_plan(plan: dict, refinement: dict, *, source: str = "agent_supplied") -> dict:
    """Merge a bounded agent/model question decomposition through a strict schema."""
    if not isinstance(refinement, dict):
        raise ValueError("research-plan refinement must be a JSON object")
    output = deepcopy(plan)
    supplied = refinement.get("subquestions") or []
    if not isinstance(supplied, list):
        raise ValueError("research-plan refinement field subquestions must be a list")
    existing = {item["id"]: item for item in output.get("subquestions", [])}
    for index, raw in enumerate(supplied[:12], start=1):
        item = _validated_subquestion(raw, index)
        existing[item["id"]] = item
    output["subquestions"] = list(existing.values())

    resolved = _dedupe([
        str(value) for value in refinement.get("resolved_concepts", []) if str(value).strip()
    ])
    external = _dedupe([
        str(value) for value in refinement.get("external_concepts", []) if str(value).strip()
    ])
    resolved_keys = {normalize_key(value) for value in resolved + external}
    output["covered_concepts"] = _dedupe(output.get("covered_concepts", []) + resolved)
    output["external_concepts"] = _dedupe(output.get("external_concepts", []) + external)
    output["unmapped_concepts"] = [
        value for value in output.get("unmapped_concepts", [])
        if normalize_key(value) not in resolved_keys
    ]
    refined_type = str(refinement.get("question_type") or "").strip()
    if refined_type:
        allowed_question_types = {
            "textual_research", "multi_part", "concept_relation", "modern_application",
            "diachronic_comparison", "philology", "quantitative",
        }
        if refined_type not in allowed_question_types:
            raise ValueError(f"unsupported research question type: {refined_type}")
        output["question_type"] = refined_type

    output["relations"] = _relations(output["subquestions"])
    output["question_coverage"] = _coverage(
        output.get("covered_concepts", []),
        output.get("external_concepts", []),
        output.get("unmapped_concepts", []),
    )
    output["coverage_status"] = (
        "complete" if not output.get("unmapped_concepts") else "incomplete"
    )
    output["needs_refinement"] = bool(output.get("unmapped_concepts"))
    output["external_evidence_required"] = any(
        "external_empirical" in item.get("required_corpus", [])
        for item in output["subquestions"]
    )
    digest = hashlib.sha256(
        json.dumps(refinement, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    output["mode"] = source
    output["planner_version"] = f"{output['planner_version']}+{source}:{digest}"
    output["refinement"] = {"source": source, "payload": refinement}
    return output


def compact_research_plan(plan: dict) -> dict:
    """Return the stable, token-conscious representation for agents."""
    return {
        key: plan.get(key)
        for key in (
            "protocol", "planner_version", "mode", "question", "question_type",
            "subquestions", "relations", "covered_concepts", "external_concepts",
            "unmapped_concepts", "question_coverage", "coverage_status",
            "needs_refinement", "external_evidence_required", "matched_rule_ids",
            "warnings",
        )
    }
