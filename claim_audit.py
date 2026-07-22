#!/usr/bin/env python3
"""Evidence-constrained claim auditing over the local MEGA retrieval index."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((SCRIPT_DIR / "config.yaml").read_text(encoding="utf-8"))

from cache import cache_get, cache_put, make_cache_key
from claim_schema import (
    CLAIM_AUDIT_SCHEMA_VERSION,
    CLASSIFIER_PROMPT_VERSION,
    PLANNER_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
    confidence,
    get_budget,
    normalize_relation,
    normalize_strength,
    normalize_verdict,
)
from index_version import get_current_version
from query_plan import compute_planner_version
from sachregister import register_status
from model_gateway import (
    call_json,
    call_text,
    empty_usage,
    merge_usage,
)
from research_export import serialize_evidence


Planner = Callable[[str, dict], Any]
Classifier = Callable[[dict, dict], Any]
Synthesizer = Callable[[dict, dict], Any]
Retriever = Callable[..., dict]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dedupe(values) -> list[str]:
    output: list[str] = []
    seen = set()
    for value in values or []:
        text = " ".join(str(value or "").strip().split())
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _heuristic_claims(idea: str, budget: dict) -> dict:
    pieces = [
        piece.strip(" \t\r\n。；;")
        for piece in re.split(r"[\r\n。；;]+", idea)
        if piece.strip(" \t\r\n。；;")
    ]
    if not pieces:
        pieces = [idea.strip()]
    claims = []
    for index, text in enumerate(pieces[: budget["max_claims"]], start=1):
        claims.append(
            {
                "claim_id": f"C{index:03d}",
                "text": text,
                "claim_type": "interpretive",
                "scope": {},
                "queries": {
                    "support": [text],
                    "qualify": [],
                    "counter": [],
                    "context": [],
                },
            }
        )
    return {"claims": claims, "planner_mode": "heuristic"}


def _normalize_plan(raw: Any, idea: str, budget: dict) -> dict:
    source = raw if isinstance(raw, dict) else {}
    raw_claims = source.get("claims") if isinstance(source.get("claims"), list) else []
    normalized = []
    for index, item in enumerate(raw_claims[: budget["max_claims"]], start=1):
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or item.get("claim") or "").split())
        if not text:
            continue
        queries = item.get("queries") if isinstance(item.get("queries"), dict) else {}
        support = _as_list(queries.get("support")) + _as_list(item.get("support_query"))
        qualify = _as_list(queries.get("qualify")) + _as_list(item.get("qualifier_queries"))
        counter = _as_list(queries.get("counter")) + _as_list(item.get("counter_queries"))
        context = _as_list(queries.get("context")) + _as_list(item.get("context_queries"))
        normalized.append(
            {
                "claim_id": f"C{index:03d}",
                "text": text,
                "claim_type": str(item.get("claim_type") or "interpretive"),
                "scope": item.get("scope") if isinstance(item.get("scope"), dict) else {},
                "queries": {
                    "support": _dedupe([text] + support)[:2],
                    "qualify": _dedupe(qualify)[:2],
                    "counter": _dedupe(counter)[:2],
                    "context": _dedupe(context)[:2],
                },
            }
        )
    if not normalized:
        return _heuristic_claims(idea, budget)
    return {"claims": normalized, "planner_mode": "model"}


def _planner_prompt(idea: str, budget: dict) -> str:
    return f"""你是 MEGA² 文献检索的查询规划器，不负责判断观点真伪。
把用户观点拆成最多 {budget['max_claims']} 个可以由马克思恩格斯原文检验的原子主张。
为每项生成简短、可用于德语原文检索的查询：support 寻找直接依据；qualify 寻找条件、限制或概念差异；counter 寻找可能反向的文本；context 寻找时期或著作背景。
查询应包含中文概念和德语术语/词形，不要写完整答案，不要声称已经找到证据。

只返回 JSON：
{{
  "claims": [
    {{
      "text": "原子主张",
      "claim_type": "textual|chronological|conceptual|attribution|interpretive",
      "scope": {{"work": "", "period": "", "author": ""}},
      "queries": {{
        "support": ["..."],
        "qualify": ["..."],
        "counter": ["..."],
        "context": ["..."]
      }}
    }}
  ]
}}

用户观点：{idea}"""


def _default_planner(idea: str, budget: dict) -> tuple[dict, dict, str]:
    raw, usage, model = call_json(
        CONFIG["models"]["flash"],
        _planner_prompt(idea, budget),
        max_tokens=budget["planner_max_tokens"],
        temperature=0.1,
    )
    return raw, usage, model


def _unpack_model_result(value: Any) -> tuple[Any, dict, str]:
    if isinstance(value, tuple):
        if len(value) == 3:
            return value[0], value[1] or empty_usage(), str(value[2] or "injected")
        if len(value) == 2:
            return value[0], value[1] or empty_usage(), "injected"
    return value, empty_usage(), "injected"


def _build_jobs(plan: dict, budget: dict) -> list[dict]:
    jobs = []
    limit = budget["retrieval_queries_per_claim"]
    for claim in plan["claims"]:
        queries = claim["queries"]
        primary_parts = _dedupe([claim["text"]] + queries.get("support", []))
        primary = " ".join(primary_parts[:2])[:900]
        jobs.append(
            {"claim_id": claim["claim_id"], "kind": "support", "query": primary}
        )
        if limit >= 2:
            challenge_parts = _dedupe(
                queries.get("qualify", []) + queries.get("counter", [])
            )
            if challenge_parts:
                jobs.append(
                    {
                        "claim_id": claim["claim_id"],
                        "kind": "challenge",
                        "query": " ".join(challenge_parts[:3])[:900],
                    }
                )
        if limit >= 3:
            context_parts = _dedupe(queries.get("context", []))
            if context_parts:
                jobs.append(
                    {
                        "claim_id": claim["claim_id"],
                        "kind": "context",
                        "query": " ".join(context_parts[:2])[:900],
                    }
                )
    output = []
    seen = set()
    for job in jobs:
        key = (job["claim_id"], job["query"].casefold())
        if job["query"] and key not in seen:
            seen.add(key)
            output.append(job)
    return output


def _record_key(record: dict) -> str:
    return str(
        record.get("id")
        or record.get("passage_id")
        or record.get("page_id")
        or hashlib.sha256(str(record.get("text") or "").encode("utf-8")).hexdigest()
    )


def _append_facet(record: dict, job: dict) -> None:
    facets = record.setdefault("_audit_facets", [])
    marker = {
        "claim_id": job["claim_id"],
        "kind": job["kind"],
        "query": job["query"],
    }
    if marker not in facets:
        facets.append(marker)


def _retrieve_candidates(
    plan: dict,
    budget: dict,
    *,
    route: str,
    retrieval_mode: str,
    rerank_method: str,
    retriever: Retriever,
    progress: Callable[[str], None],
) -> tuple[list[dict], list[str], dict]:
    jobs = _build_jobs(plan, budget)
    buckets = []
    priority_terms: list[str] = []
    debug = {"jobs": [], "retrieval_calls": 0, "raw_candidates": 0}
    adequacy_reports = []
    claim_strengths = []
    for job in jobs:
        progress(f"检索 {job['claim_id']} / {job['kind']}")
        payload = retriever(
            job["query"],
            route=route,
            top_k=budget["retrieval_top_k"],
            retrieval_mode=retrieval_mode,
            rerank_method=rerank_method,
            intent_override="claim_verification",
        )
        rows = [copy.deepcopy(row) for row in payload.get("results", [])]
        adequacy = payload.get("retrieval", {}).get("debug", {}).get("adequacy")
        if isinstance(adequacy, dict):
            adequacy_reports.append(adequacy)
        claim_strength = payload.get("query", {}).get("query_plan", {}).get("claim_strength")
        if claim_strength:
            claim_strengths.append(str(claim_strength))
        for row in rows:
            _append_facet(row, job)
        buckets.append({"job": job, "rows": rows, "position": 0})
        priority_terms.extend(payload.get("query", {}).get("priority_terms", []))
        debug["retrieval_calls"] += 1
        debug["raw_candidates"] += len(rows)
        debug["jobs"].append(
            {
                **job,
                "candidate_count": len(rows),
                "retrieval_debug": payload.get("retrieval", {}).get("debug", {}),
                "adequacy": adequacy,
                "claim_strength": claim_strength,
            }
        )

    selected: OrderedDict[str, dict] = OrderedDict()
    claim_counts = defaultdict(int)
    while len(selected) < budget["total_evidence"]:
        progressed = False
        for bucket in buckets:
            rows = bucket["rows"]
            while bucket["position"] < len(rows):
                row = rows[bucket["position"]]
                bucket["position"] += 1
                key = _record_key(row)
                claim_id = bucket["job"]["claim_id"]
                existing = selected.get(key)
                if existing is not None:
                    for facet in row.get("_audit_facets", []):
                        if facet not in existing.setdefault("_audit_facets", []):
                            existing["_audit_facets"].append(facet)
                    progressed = True
                    break
                if claim_counts[claim_id] >= budget["evidence_per_claim"]:
                    continue
                selected[key] = row
                claim_counts[claim_id] += 1
                progressed = True
                break
            if len(selected) >= budget["total_evidence"]:
                break
        if not progressed:
            break
    debug["selected_candidates"] = len(selected)
    debug["claim_candidate_counts"] = dict(claim_counts)
    status_order = ("adequate", "partial", "retrieval_gap", "coverage_gap", "insufficient")
    reported = [item.get("status") for item in adequacy_reports if item.get("status")]
    aggregate = next((status for status in status_order if status in reported), "legacy_unknown")
    debug["adequacy"] = {
        "status": aggregate,
        "job_statuses": reported,
        "reports": adequacy_reports,
        "claim_strengths": _dedupe(claim_strengths),
        "strong_claim_warning": next(
            (item.get("strong_claim_warning") for item in adequacy_reports
             if item.get("strong_claim_warning")), None
        ),
    }
    return list(selected.values()), _dedupe(priority_terms), debug


def _serialize_candidates(records: list[dict], priority_terms: list[str]) -> list[dict]:
    evidence = []
    for rank, record in enumerate(records, start=1):
        item = serialize_evidence(record, rank, priority_terms)
        item["retrieval"]["audit_facets"] = record.get("_audit_facets", [])
        item["retrieval"]["matched_variants"] = record.get("_matched_variants", [])
        item["retrieval"]["concept_groups"] = record.get(
            "_matched_concept_groups", []
        )
        evidence.append(item)
    return evidence


def _classification_payload(idea: str, plan: dict, evidence: list[dict], budget: dict) -> dict:
    compact_evidence = []
    for item in evidence:
        compact_evidence.append(
            {
                "evidence_id": item["evidence_id"],
                "source": item["locator"]["citation_stub"],
                "text_layer": item["provenance"]["reliability_class"],
                "verified_author_text": item["provenance"]["verified_author_text"],
                "locator_verified": item["locator"]["locator_verified"],
                "matched_term": item["evidence"].get("matched_term"),
                "german_context": item["evidence"]["german_context"][: budget["context_chars"]],
                "retrieved_for": item["retrieval"].get("audit_facets", []),
                "warnings": item.get("warnings", []),
            }
        )
    return {
        "idea": idea,
        "claims": [
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "claim_type": claim["claim_type"],
                "scope": claim["scope"],
            }
            for claim in plan["claims"]
        ],
        "evidence": compact_evidence,
    }


def _classifier_prompt(payload: dict) -> str:
    return """你是严格的马克思恩格斯文献证据审查员。只能根据输入的德语上下文判断每项主张获得了何种文本支持。

规则：
1. supports 表示上下文直接支持；partial_support 表示只支持其中一部分；qualifies 表示需要加条件或缩小范围；contradicts 表示明确相反；context 只是背景；irrelevant 不相关；uncertain 无法判断。
2. editorial_apparatus/editorial_material 只能说明编者判断，不能当成马克思或恩格斯原文。
3. unclassified_textband 即使内容看似相关，也必须标 strength=unverified。
4. 不得虚构引文、页码、作者或未提供的反例。未找到证据不等于观点为假。
5. verdict 只能是 strong_support、partial_support、qualified、unsupported、contradicted、insufficient。

只返回 JSON：
{
  "claims": [{
    "claim_id": "C001",
    "verdict": "...",
    "confidence": 0.0,
    "supported_part": "",
    "problematic_part": "",
    "revised_claim": "",
    "additional_directions": [""],
    "assessments": [{
      "evidence_id": "E001",
      "relation": "supports|partial_support|qualifies|contradicts|context|irrelevant|uncertain",
      "strength": "direct|indirect|weak|unverified",
      "rationale": ""
    }]
  }],
  "overall": {
    "verdict": "...",
    "confidence": 0.0,
    "summary": "",
    "revised_idea": "",
    "additional_directions": [""]
  }
}

输入：\n""" + json.dumps(payload, ensure_ascii=False)


def _default_classifier(payload: dict, budget: dict) -> tuple[dict, dict, str]:
    raw, usage, model = call_json(
        CONFIG["models"]["flash"],
        _classifier_prompt(payload),
        max_tokens=budget["classifier_max_tokens"],
        temperature=0.1,
    )
    return raw, usage, model


def _fallback_classification(plan: dict, evidence: list[dict]) -> dict:
    ids_by_claim = defaultdict(list)
    for item in evidence:
        for facet in item["retrieval"].get("audit_facets", []):
            ids_by_claim[facet.get("claim_id")].append(item["evidence_id"])
    return {
        "claims": [
            {
                "claim_id": claim["claim_id"],
                "verdict": "insufficient",
                "confidence": 0.2 if ids_by_claim[claim["claim_id"]] else 0.0,
                "supported_part": "",
                "problematic_part": "仅完成候选证据检索，尚未进行语义支持/反证判定。",
                "revised_claim": claim["text"],
                "additional_directions": [],
                "assessments": [
                    {
                        "evidence_id": evidence_id,
                        "relation": "uncertain",
                        "strength": "unverified",
                        "rationale": "候选证据，需模型或人工细读判断。",
                    }
                    for evidence_id in _dedupe(ids_by_claim[claim["claim_id"]])
                ],
            }
            for claim in plan["claims"]
        ],
        "overall": {
            "verdict": "insufficient",
            "confidence": 0.2 if evidence else 0.0,
            "summary": "本地检索已完成，但未启用语义证据审查，不能自动判定观点成立。",
            "revised_idea": "",
            "additional_directions": [],
        },
    }


def _normalize_classification(raw: Any, plan: dict, evidence: list[dict]) -> dict:
    source = raw if isinstance(raw, dict) else {}
    by_claim = {
        str(item.get("claim_id")): item
        for item in source.get("claims", [])
        if isinstance(item, dict)
    }
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    normalized_claims = []
    for claim in plan["claims"]:
        item = by_claim.get(claim["claim_id"], {})
        assessments = []
        for assessment in item.get("assessments", []):
            if not isinstance(assessment, dict):
                continue
            evidence_id = str(assessment.get("evidence_id") or "")
            evidence_item = evidence_by_id.get(evidence_id)
            if evidence_item is None:
                continue
            relation = normalize_relation(assessment.get("relation"))
            strength = normalize_strength(assessment.get("strength"))
            reliability = evidence_item["provenance"]["reliability_class"]
            verified = evidence_item["provenance"]["verified_author_text"]
            calibration = ""
            if reliability in {"editorial_apparatus", "editorial_material"} and relation in {
                "supports", "partial_support", "contradicts"
            }:
                relation = "context"
                strength = "indirect"
                calibration = "编者材料不能直接证明作者观点。"
            elif not verified and strength == "direct":
                strength = "unverified"
                calibration = "该 Textband 尚未自动验证为作者原文。"
            rationale = str(assessment.get("rationale") or "").strip()[:1000]
            if calibration:
                rationale = f"{rationale} {calibration}".strip()
            assessments.append(
                {
                    "evidence_id": evidence_id,
                    "relation": relation,
                    "strength": strength,
                    "rationale": rationale,
                }
            )

        proposed = normalize_verdict(item.get("verdict"))
        support = [a for a in assessments if a["relation"] in {"supports", "partial_support"}]
        counters = [a for a in assessments if a["relation"] == "contradicts"]
        verified_support = [
            a for a in support
            if evidence_by_id[a["evidence_id"]]["provenance"]["verified_author_text"]
        ]
        verified_counter = [
            a for a in counters
            if evidence_by_id[a["evidence_id"]]["provenance"]["verified_author_text"]
        ]
        verified_direct_support = [
            a for a in verified_support
            if a["relation"] == "supports" and a["strength"] == "direct"
        ]
        verified_direct_counter = [
            a for a in verified_counter if a["strength"] == "direct"
        ]
        if proposed == "strong_support" and not verified_direct_support:
            proposed = "partial_support" if support else "insufficient"
        if proposed == "contradicted" and not verified_direct_counter:
            proposed = "qualified" if counters else "insufficient"
        if proposed == "unsupported" and verified_support:
            proposed = "partial_support"
        if not any(a["relation"] not in {"irrelevant", "uncertain"} for a in assessments):
            proposed = "insufficient"
        conf = confidence(item.get("confidence"), 0.0)
        if not verified_support and not verified_counter:
            conf = min(conf, 0.55)
        normalized_claims.append(
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "claim_type": claim["claim_type"],
                "scope": claim["scope"],
                "verdict": proposed,
                "confidence": conf,
                "supported_part": str(item.get("supported_part") or "").strip(),
                "problematic_part": str(item.get("problematic_part") or "").strip(),
                "revised_claim": str(item.get("revised_claim") or claim["text"]).strip(),
                "additional_directions": _dedupe(item.get("additional_directions", []))[:8],
                "assessments": assessments,
            }
        )

    overall_raw = source.get("overall") if isinstance(source.get("overall"), dict) else {}
    verdicts = [item["verdict"] for item in normalized_claims]
    proposed_overall = normalize_verdict(overall_raw.get("verdict"))
    if len(verdicts) == 1:
        proposed_overall = verdicts[0]
    elif "contradicted" in verdicts or "qualified" in verdicts:
        proposed_overall = "qualified"
    elif verdicts and all(value == "strong_support" for value in verdicts):
        proposed_overall = "strong_support"
    elif any(value in {"strong_support", "partial_support"} for value in verdicts):
        proposed_overall = "partial_support"
    elif verdicts and all(value == "unsupported" for value in verdicts):
        proposed_overall = "unsupported"
    else:
        proposed_overall = "insufficient"
    overall_confidence = confidence(overall_raw.get("confidence"), 0.0)
    if normalized_claims:
        overall_confidence = min(
            overall_confidence or sum(item["confidence"] for item in normalized_claims) / len(normalized_claims),
            max(item["confidence"] for item in normalized_claims),
        )
    return {
        "claims": normalized_claims,
        "overall": {
            "verdict": proposed_overall,
            "confidence": round(overall_confidence, 3),
            "summary": str(overall_raw.get("summary") or "").strip(),
            "revised_idea": str(overall_raw.get("revised_idea") or "").strip(),
            "additional_directions": _dedupe(
                overall_raw.get("additional_directions", [])
                + [
                    direction
                    for item in normalized_claims
                    for direction in item["additional_directions"]
                ]
            )[:12],
        },
    }


def _synthesis_prompt(audit: dict, budget: dict) -> str:
    evidence = []
    for item in audit["evidence"][: budget["total_evidence"]]:
        evidence.append(
            {
                "id": item["evidence_id"],
                "source": item["locator"]["citation_stub"],
                "layer": item["provenance"]["reliability_class"],
                "verified_author_text": item["provenance"]["verified_author_text"],
                "context": item["evidence"]["german_context"][: budget["context_chars"]],
            }
        )
    payload = {
        "idea": audit["idea"],
        "overall": audit["overall"],
        "claims": audit["claims"],
        "evidence": evidence,
    }
    return """你是马克思主义文献研究专家。根据给定的观点核验 JSON 写一份简洁中文说明。
必须区分：有支持的部分、需要修正的部分、反向或限定证据、可补充方向。
每个实质判断使用 E### 证据编号；不得增加输入中没有的引文、页码或作者归属。
如果定位未验证或文本层级未确认，必须保留警告。只输出说明正文。

核验数据：\n""" + json.dumps(payload, ensure_ascii=False)


def _default_synthesizer(audit: dict, budget: dict) -> tuple[str, dict, str]:
    result = call_text(
        CONFIG["models"]["pro"],
        _synthesis_prompt(audit, budget),
        max_tokens=budget["pro_max_tokens"],
        temperature=0.3,
    )
    return result.text, result.usage, result.model


def _deterministic_summary(audit: dict) -> str:
    overall = audit["overall"]
    lines = [
        f"总体判断：{overall['verdict']}（置信度 {overall['confidence']:.2f}）。",
        overall.get("summary") or "结论仅限于当前召回证据。",
    ]
    for claim in audit["claims"]:
        lines.append(
            f"{claim['claim_id']}：{claim['verdict']}。"
            + (f" {claim['problematic_part']}" if claim.get("problematic_part") else "")
        )
    return "\n\n".join(line for line in lines if line)


def _cache_key(
    idea: str,
    budget: dict,
    route: str,
    retrieval_mode: str,
    rerank_method: str,
    use_flash: bool,
    use_pro: bool,
    index_version: str,
) -> str:
    model_names = ",".join(
        str(CONFIG.get("models", {}).get(name, {}).get("model", ""))
        for name in ("flash", "pro")
    )
    register_version = register_status().get("register_version") or "register-missing"
    prompt_version = ":".join(
        (PLANNER_PROMPT_VERSION, CLASSIFIER_PROMPT_VERSION, SYNTHESIS_PROMPT_VERSION,
         compute_planner_version(), register_version)
    )
    return make_cache_key(
        idea,
        filters=f"{route}|flash={use_flash}|pro={use_pro}",
        top_k=budget["total_evidence"],
        mode=f"claim-audit:{budget['name']}:{retrieval_mode}:{rerank_method}",
        index_version=index_version,
        prompt_version=prompt_version,
        model=model_names,
    )


def audit_claim(
    idea: str,
    *,
    budget: str = "standard",
    route: str = "main_text",
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    use_flash: bool = True,
    use_pro: bool = False,
    use_cache: bool = True,
    retriever: Retriever | None = None,
    planner: Planner | None = None,
    classifier: Classifier | None = None,
    synthesizer: Synthesizer | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Audit an interpretive claim and return evidence-constrained structured JSON."""
    started = time.perf_counter()
    idea = " ".join(str(idea or "").strip().split())
    if not idea:
        raise ValueError("idea must not be empty")
    if route not in {"all", "main_text", "apparat"}:
        raise ValueError(f"unsupported route: {route}")
    resource_budget = get_budget(budget)
    progress = progress or (lambda _message: None)
    custom_retriever = retriever is not None
    if retriever is None:
        from research_export import retrieve_research_evidence

        retriever = retrieve_research_evidence
    custom_dependencies = custom_retriever or any(
        value is not None for value in (planner, classifier, synthesizer)
    )
    index_version = get_current_version()
    cache_key = _cache_key(
        idea,
        resource_budget,
        route,
        retrieval_mode,
        rerank_method,
        use_flash,
        use_pro,
        index_version,
    )
    if use_cache and not custom_dependencies:
        cached = cache_get(cache_key)
        if cached:
            try:
                output = json.loads(cached)
                output.setdefault("runtime", {})["cache_hit"] = True
                output["runtime"]["api_tokens_this_run"] = 0
                output["runtime"]["elapsed_seconds_this_run"] = round(
                    time.perf_counter() - started, 3
                )
                return output
            except (TypeError, json.JSONDecodeError):
                pass

    warnings: list[str] = []
    usage = empty_usage()
    models = {"planner": None, "classifier": None, "synthesizer": None}
    plan = _heuristic_claims(idea, resource_budget)
    if use_flash:
        try:
            progress("使用 Flash 拆分原子主张和检索面")
            raw_plan, plan_usage, plan_model = _unpack_model_result(
                (planner or _default_planner)(idea, resource_budget)
            )
            plan = _normalize_plan(raw_plan, idea, resource_budget)
            usage = merge_usage(usage, plan_usage)
            models["planner"] = plan_model
        except Exception as exc:
            warnings.append(f"观点拆分模型不可用，使用保守单主张模式：{type(exc).__name__}")
    else:
        warnings.append("未启用 Flash：使用启发式主张拆分。")

    retrieval_started = time.perf_counter()
    records, priority_terms, retrieval_debug = _retrieve_candidates(
        plan,
        resource_budget,
        route=route,
        retrieval_mode=retrieval_mode,
        rerank_method=rerank_method,
        retriever=retriever,
        progress=progress,
    )
    evidence = _serialize_candidates(records, priority_terms)
    retrieval_seconds = time.perf_counter() - retrieval_started

    raw_classification = _fallback_classification(plan, evidence)
    classification_mode = "local_candidates_only"
    if use_flash and evidence:
        try:
            progress("使用 Flash 判断支持、限定与反证关系")
            payload = _classification_payload(idea, plan, evidence, resource_budget)
            raw_classification, classify_usage, classify_model = _unpack_model_result(
                (classifier or _default_classifier)(payload, resource_budget)
            )
            usage = merge_usage(usage, classify_usage)
            models["classifier"] = classify_model
            classification_mode = "model_evidence_review"
        except Exception as exc:
            warnings.append(f"证据关系模型不可用，仅返回候选证据：{type(exc).__name__}")
    elif not evidence:
        warnings.append("当前检索未返回候选证据；这不能证明该观点在全部文本中不存在。")

    normalized = _normalize_classification(raw_classification, plan, evidence)
    retrieval_state = retrieval_debug.get("adequacy", {})
    retrieval_status = retrieval_state.get("status", "legacy_unknown")
    strong_claim_warning = retrieval_state.get("strong_claim_warning")
    if strong_claim_warning:
        for claim in normalized["claims"]:
            if claim["verdict"] == "strong_support":
                claim["verdict"] = "partial_support"
                claim["problematic_part"] = " ".join(filter(None, [
                    claim.get("problematic_part", ""), strong_claim_warning
                ])).strip()
        if normalized["overall"]["verdict"] == "strong_support":
            normalized["overall"]["verdict"] = "partial_support"
    if retrieval_status in {"retrieval_gap", "coverage_gap", "insufficient"}:
        if normalized["overall"]["verdict"] not in {"contradicted"}:
            normalized["overall"]["verdict"] = "insufficient"
            normalized["overall"]["confidence"] = min(
                normalized["overall"]["confidence"], 0.45
            )
    judgment_status = (
        "model_reviewed" if classification_mode == "model_evidence_review"
        else "candidates_only"
    )
    answerability = {
        "adequate": "answerable", "partial": "partially_answerable",
        "legacy_unknown": "unknown",
    }.get(retrieval_status, "not_answerable")
    audit = {
        "schema_version": CLAIM_AUDIT_SCHEMA_VERSION,
        "audit_id": hashlib.sha256(
            f"{idea}|{index_version}|{_now_iso()}".encode("utf-8")
        ).hexdigest()[:16],
        "generated_at": _now_iso(),
        "index_version": index_version,
        "idea": idea,
        "budget": resource_budget["name"],
        "mode": classification_mode,
        "scope": {
            "route": route,
            "retrieval_mode": retrieval_mode,
            "rerank_method": rerank_method,
        },
        "status": {
            "retrieval": retrieval_status,
            "judgment": judgment_status,
            "answerability": answerability,
            "strong_claim_warning": strong_claim_warning,
        },
        "plan": plan,
        "claims": normalized["claims"],
        "overall": normalized["overall"],
        "evidence": evidence,
        "retrieval": retrieval_debug,
        "warnings": _dedupe(
            warnings
            + [
                "检索未命中不能单独证明某种表述从未出现在马克思恩格斯文本中。",
                "正式引用前必须核验印刷页码、作者归属和未分类 Textband。",
            ]
        ),
        "models": models,
        "usage": usage,
    }

    can_synthesize_with_pro = (
        use_pro
        and resource_budget["pro_max_tokens"] > 0
        and classification_mode == "model_evidence_review"
    )
    if can_synthesize_with_pro:
        try:
            progress("使用 Pro 生成受证据约束的学术综合")
            synthesis, synthesis_usage, synthesis_model = _unpack_model_result(
                (synthesizer or _default_synthesizer)(audit, resource_budget)
            )
            audit["academic_synthesis"] = str(synthesis).strip()
            usage = merge_usage(usage, synthesis_usage)
            audit["usage"] = usage
            audit["models"]["synthesizer"] = synthesis_model
        except Exception as exc:
            audit["warnings"].append(f"Pro 综合不可用：{type(exc).__name__}")
            audit["academic_synthesis"] = _deterministic_summary(audit)
    else:
        if (
            use_pro
            and resource_budget["pro_max_tokens"] > 0
            and classification_mode != "model_evidence_review"
        ):
            audit["warnings"].append("Flash 证据审查未完成，已跳过 Pro 以避免无依据综合和额外费用。")
        audit["academic_synthesis"] = _deterministic_summary(audit)

    audit["summary"] = {
        "verdict": audit["overall"]["verdict"],
        "confidence": audit["overall"]["confidence"],
        "retrieval_status": audit["status"]["retrieval"],
        "judgment_status": audit["status"]["judgment"],
        "answerability": audit["status"]["answerability"],
        "atomic_claims": len(audit["claims"]),
        "evidence_count": len(evidence),
        "verified_author_text_count": sum(
            item["provenance"]["verified_author_text"] for item in evidence
        ),
        "unverified_locator_count": sum(
            not item["locator"]["locator_verified"] for item in evidence
        ),
        "rough_evidence_tokens": sum(
            item["evidence"]["rough_token_estimate"] for item in evidence
        ),
    }
    audit["runtime"] = {
        "cache_hit": False,
        "retrieval_seconds": round(retrieval_seconds, 3),
        "elapsed_seconds_this_run": round(time.perf_counter() - started, 3),
        "api_tokens_this_run": int(audit["usage"].get("total_tokens", 0)),
    }
    if use_cache and not custom_dependencies:
        cache_put(
            cache_key,
            "claim_audit",
            audit,
            index_version=index_version,
            prompt_version=":".join(
                (PLANNER_PROMPT_VERSION, CLASSIFIER_PROMPT_VERSION, SYNTHESIS_PROMPT_VERSION,
                 compute_planner_version(),
                 register_status().get("register_version") or "register-missing")
            ),
            model=",".join(value or "" for value in models.values()),
        )
    return audit


def render_claim_audit_markdown(audit: dict, include_context: bool = True) -> str:
    summary = audit["summary"]
    lines = [
        "# MEGA² 观点核验报告",
        "",
        f"- Audit ID: `{audit['audit_id']}`",
        f"- Index version: `{audit['index_version']}`",
        f"- Verdict: `{summary['verdict']}`",
        f"- Confidence: `{summary['confidence']}`",
        f"- Evidence: `{summary['evidence_count']}`",
        f"- API tokens this run: `{audit['runtime']['api_tokens_this_run']}`",
        "",
        "## 原始观点",
        "",
        audit["idea"],
        "",
        "## 总体说明",
        "",
        audit.get("academic_synthesis") or audit["overall"].get("summary", ""),
        "",
        "## 原子主张",
    ]
    for claim in audit["claims"]:
        lines.extend(
            [
                "",
                f"### {claim['claim_id']} `{claim['verdict']}`",
                "",
                claim["text"],
                "",
                f"- 有支撑部分：{claim.get('supported_part') or '-'}",
                f"- 需要修正：{claim.get('problematic_part') or '-'}",
                f"- 建议改写：{claim.get('revised_claim') or '-'}",
                "- 证据关系："
                + (
                    "；".join(
                        f"{item['evidence_id']}={item['relation']}"
                        for item in claim.get("assessments", [])
                    )
                    or "-"
                ),
            ]
        )
    lines.extend(["", "## 证据索引", ""])
    for item in audit["evidence"]:
        lines.extend(
            [
                f"### {item['evidence_id']} {item['locator']['citation_stub']}",
                "",
                f"- 层级：{item['provenance']['text_layer_label']}",
                f"- 命中：{item['evidence'].get('matched_term') or '-'}",
                f"- 定位已核验：{'yes' if item['locator']['locator_verified'] else 'no'}",
            ]
        )
        if include_context:
            lines.extend(["", item["evidence"]["german_context"], ""])
    lines.extend(["", "## 警告", ""])
    lines.extend(f"- {warning}" for warning in audit.get("warnings", []))
    return "\n".join(lines).rstrip() + "\n"


def write_claim_audit(
    audit: dict,
    output_dir: str | Path | None = None,
    output_format: str = "both",
) -> dict:
    if output_format not in {"both", "json", "markdown"}:
        raise ValueError(f"unsupported output format: {output_format}")
    base = Path(
        output_dir
        or CONFIG.get("paths", {}).get("research_exports")
        or SCRIPT_DIR / "research_exports"
    ) / "claim_audits"
    base.mkdir(parents=True, exist_ok=True)
    stem = f"claim_audit_{audit['generated_at'].replace(':', '').replace('-', '')[:15]}_{audit['audit_id'][:8]}"
    paths = {"json": None, "markdown": None}
    if output_format in {"both", "json"}:
        path = base / f"{stem}.json"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        paths["json"] = str(path.resolve())
    if output_format in {"both", "markdown"}:
        path = base / f"{stem}.md"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(render_claim_audit_markdown(audit), encoding="utf-8")
        temporary.replace(path)
        paths["markdown"] = str(path.resolve())
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("idea")
    parser.add_argument("--budget", choices=("brief", "standard", "deep"), default="standard")
    parser.add_argument("--route", choices=("all", "main_text", "apparat"), default="main_text")
    parser.add_argument("--no-flash", action="store_true")
    parser.add_argument("--pro", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--format", choices=("both", "json", "markdown"), default="both")
    args = parser.parse_args()
    try:
        audit = audit_claim(
            args.idea,
            budget=args.budget,
            route=args.route,
            use_flash=not args.no_flash,
            use_pro=args.pro,
            use_cache=not args.no_cache,
            progress=lambda message: print(message, file=sys.stderr),
        )
        paths = write_claim_audit(audit, args.output_dir, args.format)
        print(json.dumps({"summary": audit["summary"], "paths": paths}, ensure_ascii=False))
        return 0 if audit["summary"]["evidence_count"] else 2
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
