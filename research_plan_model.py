#!/usr/bin/env python3
"""Optional bounded Flash refinement for a question-level ResearchPlan."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from model_gateway import call_json, empty_usage
from research_plan import (
    build_research_plan,
    compact_research_plan,
    merge_research_plan,
)


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
HYBRID_RESEARCH_PROMPT_VERSION = "research-plan-refiner-v1"


def _prompt(question: str, local_plan: dict) -> str:
    payload = {
        "question": question,
        "local_plan": compact_research_plan(local_plan),
    }
    return """You decompose a complex research question for a MEGA text-retrieval system.
Return one JSON object only. Do not answer the question. Do not judge whether Marx is
right. Preserve separate layers: MEGA textual reconstruction, concept relations,
counter-evidence, philology, corpus-level quantitative claims, and contemporary
empirical claims. Contemporary facts require external_empirical evidence and cannot
be proven from MEGA. A subquestion query_refinement may contain only the validated
QueryPlan vocabulary/scope fields.

Allowed object:
{
  "question_type": "",
  "resolved_concepts": [],
  "external_concepts": [],
  "subquestions": [
    {
      "id": "",
      "question": "",
      "type": "textual_reconstruction|concept_relation|concept_bridge|diachronic_comparison|philology|counter_evidence|quantitative|modern_application|external_empirical|general_research",
      "covered_concepts": [],
      "external_concepts": [],
      "required_corpus": ["mega|external_empirical"],
      "required_evidence": "",
      "query_refinement": {
        "core_terms": [], "historical_variants": [],
        "related_non_equivalent": [], "supporting_terms": [],
        "generic_terms": [], "target_works": [], "target_volumes": [],
        "target_topics": [], "relation_pairs": [], "intent": ""
      }
    }
  ]
}

Input:\n""" + json.dumps(payload, ensure_ascii=False)


def build_hybrid_research_plan(
    question: str,
    *,
    local_plan: dict | None = None,
) -> tuple[dict, dict]:
    local_plan = local_plan or build_research_plan(question)
    try:
        raw, usage, model = call_json(
            CONFIG["models"]["flash"],
            _prompt(question, local_plan),
            max_tokens=900,
            temperature=0.0,
        )
        refined = merge_research_plan(local_plan, raw, source="hybrid")
        return refined, {
            "mode": "hybrid",
            "model": model,
            "usage": usage,
            "prompt_version": HYBRID_RESEARCH_PROMPT_VERSION,
            "fallback": False,
        }
    except Exception as exc:
        fallback = dict(local_plan)
        fallback["mode"] = "hybrid_fallback_local"
        fallback.setdefault("warnings", []).append(
            f"Hybrid research planner unavailable; local plan used: {type(exc).__name__}"
        )
        return fallback, {
            "mode": "hybrid_fallback_local",
            "model": None,
            "usage": empty_usage(),
            "prompt_version": HYBRID_RESEARCH_PROMPT_VERSION,
            "fallback": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
