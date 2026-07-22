#!/usr/bin/env python3
"""Multi-branch, token-conscious research orchestration for MEGA evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from evidence_identity import package_evidence_ref
from index_version import get_current_version
from report_contract import evaluate_research_run_gate
from research_export import retrieve_research_evidence, serialize_evidence
from research_plan import build_research_plan, compact_research_plan


RESEARCH_RUN_PROTOCOL = "mega-research-run-v1"

Retriever = Callable[..., dict]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dedupe(values: list[Any]) -> list[Any]:
    output = []
    seen = set()
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(
            value, (dict, list, tuple)
        ) else str(value).casefold()
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _evidence_key(item: dict) -> str:
    record = item.get("record", {})
    return str(
        item.get("evidence_uid")
        or record.get("passage_id")
        or record.get("record_id")
        or record.get("content_hash")
        or item.get("evidence", {}).get("quote_sha256")
        or item.get("evidence_id")
    )


def _branch_route(subquestion: dict) -> str:
    if subquestion.get("type") == "philology":
        return "all"
    if subquestion.get("query_intent") == "apparat_question":
        return "apparat"
    return "main_text"


def _claim_type(subquestion_type: str) -> str:
    return {
        "textual_reconstruction": "text_supported",
        "counter_evidence": "text_supported_qualification",
        "philology": "philological_finding",
        "diachronic_comparison": "comparative_inference",
        "quantitative": "corpus_level_claim",
        "concept_relation": "theoretical_inference",
        "concept_bridge": "theoretical_inference",
        "modern_application": "empirical_hypothesis",
        "external_empirical": "empirical_hypothesis",
    }.get(subquestion_type, "research_claim")


def _allowed_use(subquestion_type: str) -> str:
    return {
        "textual_reconstruction": "May support a statement about the retrieved MEGA passage.",
        "counter_evidence": "May qualify a textual claim; do not treat it as an automatic refutation.",
        "philology": "Must distinguish author text, edited print text, and apparatus.",
        "diachronic_comparison": "Requires dated evidence from more than one period.",
        "quantitative": "Requires corpus-level counts, not ordinary top-k passages.",
        "concept_relation": "The relation must be argued; co-occurrence alone is insufficient.",
        "concept_bridge": "Label the bridge as interpretation unless Marx states it directly.",
        "modern_application": "Label as a contemporary application and add external empirical evidence.",
        "external_empirical": "MEGA cannot establish this claim; use external contemporary sources.",
    }.get(subquestion_type, "Use only within the stated evidence boundary.")


def _select_records(results: list[dict], limit: int) -> list[dict]:
    eligible = [record for record in results if record.get("evidence_eligible")]
    ineligible = [record for record in results if not record.get("evidence_eligible")]
    return (eligible + ineligible)[:limit]

def _fair_evidence_keys(branch_rows: list[dict], limit: int) -> list[str]:
    """Allocate evidence round-robin so early branches cannot exhaust the budget."""
    queues = [
        list(dict.fromkeys(branch.get("evidence_keys", [])))
        for branch in branch_rows
        if branch.get("evidence_keys")
    ]
    selected: list[str] = []
    seen: set[str] = set()
    while queues and len(selected) < limit:
        next_round = []
        progressed = False
        for queue in queues:
            while queue and queue[0] in seen:
                queue.pop(0)
            if queue and len(selected) < limit:
                key = queue.pop(0)
                seen.add(key)
                selected.append(key)
                progressed = True
            if queue:
                next_round.append(queue)
        if not progressed:
            break
        queues = next_round
    return selected



def _safe_index_version() -> str:
    try:
        return get_current_version()
    except Exception:
        return "unavailable"


def run_research(
    question: str,
    *,
    research_plan: dict | None = None,
    planner_mode: str = "local",
    research_refinement: dict | None = None,
    top_k_per_branch: int = 5,
    max_evidence: int = 12,
    retrieval_mode: str = "original_first",
    rerank_method: str = "rule",
    retriever: Retriever | None = None,
) -> dict:
    """Run required MEGA branches and preserve non-MEGA evidence gaps."""
    if top_k_per_branch < 1 or max_evidence < 1:
        raise ValueError("research evidence limits must be positive")
    retriever = retriever or retrieve_research_evidence
    plan = research_plan or build_research_plan(
        question,
        mode=planner_mode,
        refinement=research_refinement,
    )

    evidence_by_key: dict[str, dict] = {}
    branch_rows: list[dict] = []
    missing_requirements: list[str] = []

    for subquestion in plan.get("subquestions", []):
        branch_id = subquestion["id"]
        required_corpus = list(subquestion.get("required_corpus", ["mega"]))
        external_required = "external_empirical" in required_corpus
        mega_required = "mega" in required_corpus
        branch = {
            "id": branch_id,
            "question": subquestion.get("question"),
            "type": subquestion.get("type"),
            "origin": subquestion.get("origin"),
            "required_corpus": required_corpus,
            "required_evidence": subquestion.get("required_evidence"),
            "mega_retrieval_status": "not_required",
            "status": "pending",
            "reason": "",
            "evidence_keys": [],
            "evidence_ids": [],
            "retrieval_debug": {},
        }

        if mega_required:
            refinement = subquestion.get("query_refinement")
            try:
                payload = retriever(
                    subquestion.get("question") or question,
                    route=_branch_route(subquestion),
                    top_k=top_k_per_branch,
                    retrieval_mode=retrieval_mode,
                    rerank_method=rerank_method,
                    intent_override=subquestion.get("query_intent"),
                    planner_mode="agent_supplied" if refinement else "local",
                    plan_refinement=refinement,
                )
                adequacy = (
                    payload.get("retrieval", {}).get("debug", {}).get("adequacy", {})
                )
                branch["mega_retrieval_status"] = adequacy.get("status", "unknown")
                branch["reason"] = adequacy.get("reason", "")
                branch["retrieval_debug"] = {
                    "adequacy": adequacy,
                    "term_probe": payload.get("retrieval", {}).get("term_probe", {}).get("summary", {}),
                    "navigation_hits": len(
                        payload.get("retrieval", {}).get("navigation", {}).get("sachregister", [])
                    ),
                }
                selected = _select_records(payload.get("results", []), top_k_per_branch)
                priority_terms = payload.get("query", {}).get("priority_terms", [])
                for record in selected:
                    item = serialize_evidence(record, 0, priority_terms)
                    key = _evidence_key(item)
                    if key not in evidence_by_key:
                        item.setdefault("research", {})["branch_ids"] = [branch_id]
                        item["research"]["claim_types"] = [_claim_type(subquestion.get("type", ""))]
                        evidence_by_key[key] = item
                    elif key in evidence_by_key:
                        research = evidence_by_key[key].setdefault("research", {})
                        research["branch_ids"] = _dedupe(research.get("branch_ids", []) + [branch_id])
                        research["claim_types"] = _dedupe(
                            research.get("claim_types", []) + [_claim_type(subquestion.get("type", ""))]
                        )
                    if key in evidence_by_key:
                        branch["evidence_keys"].append(key)
            except Exception as exc:
                branch["mega_retrieval_status"] = "error"
                branch["reason"] = f"{type(exc).__name__}: {exc}"

        if external_required:
            missing_requirements.append(
                f"{branch_id}: contemporary external empirical evidence"
            )
            if branch["mega_retrieval_status"] in {"adequate", "partial", "not_required"}:
                branch["status"] = "external_evidence_required"
            else:
                branch["status"] = branch["mega_retrieval_status"]
        else:
            branch["status"] = branch["mega_retrieval_status"]
        branch_rows.append(branch)

    selected_keys = _fair_evidence_keys(branch_rows, max_evidence)
    evidence = [evidence_by_key[key] for key in selected_keys]
    key_to_id = {}
    for rank, item in enumerate(evidence, start=1):
        item["rank"] = rank
        item["evidence_id"] = f"E{rank:03d}"
        key_to_id[_evidence_key(item)] = item["evidence_id"]
    for branch in branch_rows:
        branch["evidence_ids"] = _dedupe([
            key_to_id[key] for key in branch.pop("evidence_keys", []) if key in key_to_id
        ])

    statuses = Counter(branch.get("status", "unknown") for branch in branch_rows)
    mega_bad = any(
        branch.get("mega_retrieval_status") in {
            "retrieval_gap", "coverage_gap", "insufficient", "error", "unknown"
        }
        for branch in branch_rows
        if "mega" in branch.get("required_corpus", [])
    )
    mega_partial = any(
        branch.get("mega_retrieval_status") == "partial" for branch in branch_rows
    )
    plan_incomplete = bool(plan.get("unmapped_concepts"))
    external_missing = bool(missing_requirements)

    if plan_incomplete:
        overall_status = "incomplete_plan"
        answerability = "not_answerable"
    elif mega_bad:
        overall_status = "retrieval_gap"
        answerability = "partially_answerable" if evidence else "not_answerable"
    elif external_missing or mega_partial:
        overall_status = "partial"
        answerability = "partially_answerable"
    else:
        overall_status = "adequate"
        answerability = "answerable"

    claim_matrix = [
        {
            "subquestion_id": item["id"],
            "claim_type": _claim_type(item.get("type", "")),
            "evidence_ids": next(
                (row["evidence_ids"] for row in branch_rows if row["id"] == item["id"]),
                [],
            ),
            "status": next(
                (row["status"] for row in branch_rows if row["id"] == item["id"]),
                "unknown",
            ),
            "allowed_use": _allowed_use(item.get("type", "")),
        }
        for item in plan.get("subquestions", [])
    ]
    rough_tokens = sum(
        int(item.get("evidence", {}).get("rough_token_estimate") or 0) for item in evidence
    )
    generated_at = _now_iso()
    index_version = _safe_index_version()
    run_id = hashlib.sha256(
        f"{question}|{index_version}|{generated_at}".encode("utf-8")
    ).hexdigest()[:16]
    warnings = _dedupe(
        list(plan.get("warnings", []))
        + [
            "Index previews are selection aids, not quote-eligible text.",
            "A contemporary application must be labelled as inference unless external evidence is supplied.",
            "Overall adequacy requires every required MEGA branch, not merely one successful search.",
        ]
    )
    id_to_uid = {}
    for item in evidence:
        item["package_evidence_ref"] = package_evidence_ref(
            run_id, item["evidence_id"]
        )
        item["evidence_role"] = (
            "qualified_evidence"
            if item.get("provenance", {}).get("evidence_eligible")
            else "provisional_candidate"
        )
        id_to_uid[item["evidence_id"]] = item.get("evidence_uid")
    for row in claim_matrix:
        row["evidence_uids"] = [
            id_to_uid[evidence_id]
            for evidence_id in row.get("evidence_ids", [])
            if id_to_uid.get(evidence_id)
        ]

    payload = {
        "protocol": RESEARCH_RUN_PROTOCOL,
        "run_id": run_id,
        "generated_at": generated_at,
        "index_version": index_version,
        "question": question,
        "research_plan": compact_research_plan(plan),
        "status": {
            "overall": overall_status,
            "answerability": answerability,
            "question_coverage": plan.get("question_coverage", 0.0),
            "coverage_status": plan.get("coverage_status"),
            "branch_status_counts": dict(statuses),
            "missing_requirements": missing_requirements,
        },
        "branches": branch_rows,
        "claim_evidence_matrix": claim_matrix,
        "evidence": evidence,
        "usage": {
            "api_tokens": 0,
            "rough_selected_evidence_tokens": rough_tokens,
            "selected_evidence_count": len(evidence),
        },
        "warnings": warnings,
    }
    payload["synthesis_gate"] = evaluate_research_run_gate(payload)
    return payload


def write_research_run(
    payload: dict,
    *,
    output_dir: str | Path,
) -> str:
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"research_run_{payload['run_id']}.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    return str(path)
