#!/usr/bin/env python3
"""Stable schemas and resource budgets for MEGA claim auditing."""
from __future__ import annotations

from copy import deepcopy


CLAIM_AUDIT_SCHEMA_VERSION = "mega-claim-audit-v1"
AGENT_SCHEMA_VERSION = "mega-agent-v1"
PLANNER_PROMPT_VERSION = "claim-planner-v1"
CLASSIFIER_PROMPT_VERSION = "claim-classifier-v1"
SYNTHESIS_PROMPT_VERSION = "claim-synthesis-v1"

VERDICTS = {
    "strong_support",
    "partial_support",
    "qualified",
    "unsupported",
    "contradicted",
    "insufficient",
}

RELATIONS = {
    "supports",
    "partial_support",
    "qualifies",
    "contradicts",
    "context",
    "irrelevant",
    "uncertain",
}

STRENGTHS = {"direct", "indirect", "weak", "unverified"}

BUDGETS = {
    "brief": {
        "max_claims": 1,
        "retrieval_queries_per_claim": 1,
        "retrieval_top_k": 6,
        "evidence_per_claim": 5,
        "total_evidence": 6,
        "context_chars": 700,
        "planner_max_tokens": 900,
        "classifier_max_tokens": 1400,
        "pro_max_tokens": 0,
    },
    "standard": {
        "max_claims": 3,
        "retrieval_queries_per_claim": 2,
        "retrieval_top_k": 8,
        "evidence_per_claim": 6,
        "total_evidence": 14,
        "context_chars": 950,
        "planner_max_tokens": 1300,
        "classifier_max_tokens": 2200,
        "pro_max_tokens": 2200,
    },
    "deep": {
        "max_claims": 5,
        "retrieval_queries_per_claim": 3,
        "retrieval_top_k": 12,
        "evidence_per_claim": 8,
        "total_evidence": 24,
        "context_chars": 1250,
        "planner_max_tokens": 1900,
        "classifier_max_tokens": 3200,
        "pro_max_tokens": 3400,
    },
}


def get_budget(name: str) -> dict:
    normalized = str(name or "standard").strip().casefold()
    if normalized not in BUDGETS:
        raise ValueError(f"unknown budget: {name}")
    output = deepcopy(BUDGETS[normalized])
    output["name"] = normalized
    return output


def normalize_verdict(value: str) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in VERDICTS else "insufficient"


def normalize_relation(value: str) -> str:
    candidate = str(value or "").strip().casefold()
    aliases = {
        "support": "supports",
        "supported": "supports",
        "partial": "partial_support",
        "qualify": "qualifies",
        "qualification": "qualifies",
        "contradict": "contradicts",
        "counter": "contradicts",
        "irrelevant_to_claim": "irrelevant",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in RELATIONS else "uncertain"


def normalize_strength(value: str) -> str:
    candidate = str(value or "").strip().casefold()
    return candidate if candidate in STRENGTHS else "weak"


def confidence(value, default: float = 0.0) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return default
