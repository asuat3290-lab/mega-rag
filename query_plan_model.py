#!/usr/bin/env python3
"""Optional bounded Flash refinement for a local QueryPlan.

The model may propose retrieval vocabulary and scope only.  It cannot emit an
answer, label evidence, or bypass the deterministic refinement validator.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from model_gateway import call_json, empty_usage
from query_plan import build_query_plan, compact_plan, merge_query_plan
from sachregister import search_sachregister
from term_probe import probe_query_plan


BASE_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8"))
HYBRID_PROMPT_VERSION = "query-plan-refiner-v1"


def _prompt(question: str, local_plan: dict, probe: dict, register: dict) -> str:
    register_context = [
        {
            "volume": f"{hit.get('abteilung')}/{hit.get('band')}",
            "matched_term": hit.get("matched_term"),
            "snippet": str(hit.get("snippet") or "")[:420],
        }
        for hit in register.get("hits", [])[:5]
    ]
    term_counts = [
        {key: item.get(key) for key in ("term", "text_pages", "apparat_pages")}
        for item in probe.get("terms", [])[:20]
    ]
    payload = {
        "question": question,
        "local_plan": compact_plan(local_plan),
        "term_counts": term_counts,
        "sachregister_navigation": register_context,
    }
    return """You refine German search vocabulary for the Marx-Engels-Gesamtausgabe (MEGA).
Return one JSON object only. Do not answer the research question and do not claim that
evidence exists. Add only terms that improve retrieval. Keep historical spellings
separate from modern spellings. Put related but non-equivalent concepts in
related_non_equivalent, never in core_terms. Broad words such as Kapital, Arbeit,
Marx, Kritik and Philosophie belong in generic_terms when a narrower concept exists.
Target volumes are soft scope hints, not evidence.

Allowed JSON fields:
{
  "core_terms": [],
  "historical_variants": [],
  "related_non_equivalent": [],
  "supporting_terms": [],
  "generic_terms": [],
  "target_works": [],
  "target_volumes": [],
  "target_topics": [],
  "relation_pairs": [],
  "intent": ""
}

Input:\n""" + json.dumps(payload, ensure_ascii=False)


def build_hybrid_plan(question: str, *, local_plan: dict | None = None) -> tuple[dict, dict]:
    """Return a validated hybrid plan plus exact API usage diagnostics."""
    local_plan = local_plan or build_query_plan(question)
    terms = (
        local_plan.get("core_terms", []) + local_plan.get("historical_variants", [])
        + local_plan.get("related_non_equivalent", [])
    )[:15]
    probe = probe_query_plan(local_plan, sample_limit=0)
    register = search_sachregister(
        terms, top_k=8, target_volumes=local_plan.get("target_volumes", [])
    )
    try:
        raw, usage, model = call_json(
            CONFIG["models"]["flash"],
            _prompt(question, local_plan, probe, register),
            max_tokens=700,
            temperature=0.0,
        )
        refined = merge_query_plan(local_plan, raw, source="hybrid")
        return refined, {
            "mode": "hybrid", "model": model, "usage": usage,
            "prompt_version": HYBRID_PROMPT_VERSION, "fallback": False,
        }
    except Exception as exc:
        fallback = dict(local_plan)
        fallback["mode"] = "hybrid_fallback_local"
        fallback.setdefault("warnings", []).append(
            f"Hybrid planner unavailable; local plan used: {type(exc).__name__}"
        )
        return fallback, {
            "mode": "hybrid_fallback_local", "model": None,
            "usage": empty_usage(), "prompt_version": HYBRID_PROMPT_VERSION,
            "fallback": True, "error": f"{type(exc).__name__}: {exc}",
        }
