#!/usr/bin/env python3
"""Local retrieval gate for claim-audit concepts; it makes no semantic verdicts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from research_export import retrieve_research_evidence


def _contains_term(row: dict, terms: list[str]) -> bool:
    text = str(row.get("display_snippet") or row.get("text") or "").casefold()
    return any(str(term).casefold() in text for term in terms)


def evaluate_case(case: dict, top_k: int) -> dict:
    payload = retrieve_research_evidence(
        case["query"], route="all", top_k=top_k, retrieval_mode="original_first"
    )
    rows = payload.get("results", [])
    expected_terms = case.get("expected_terms", [])
    expected_abteilung = str(case.get("expected_abteilung") or "")
    expected_band = str(case.get("expected_band") or "")
    preferred_type = str(case.get("preferred_text_type") or "")
    term_hits = [index for index, row in enumerate(rows, start=1) if _contains_term(row, expected_terms)]
    scoped_hits = []
    for index, row in enumerate(rows, start=1):
        if expected_abteilung and str(row.get("abteilung") or "") != expected_abteilung:
            continue
        if expected_band and str(row.get("band") or "") != expected_band:
            continue
        if preferred_type and str(row.get("type") or row.get("source_type") or "") != preferred_type:
            continue
        if expected_terms and not _contains_term(row, expected_terms):
            continue
        scoped_hits.append(index)
    if not rows:
        status = "XFAIL_DATA"
        reason = "retrieval returned no indexed candidates"
    elif not term_hits:
        status = "FAIL_ALG"
        reason = "top results do not contain an expected lexical form"
    elif (expected_abteilung or expected_band or preferred_type) and not scoped_hits:
        status = "FAIL_ALG"
        reason = "expected scoped TEXT evidence is absent from the candidate set"
    else:
        status = "PASS"
        reason = "expected lexical and scope signals are present"
    return {
        "id": case["id"],
        "query": case["query"],
        "status": status,
        "reason": reason,
        "term_hit_ranks": term_hits,
        "scoped_hit_ranks": scoped_hits,
        "top": [
            {
                "rank": index,
                "abteilung": row.get("abteilung"),
                "band": row.get("band"),
                "type": row.get("type") or row.get("source_type"),
                "page": row.get("page_label") or row.get("page"),
                "matched_term": row.get("matched_term"),
            }
            for index, row in enumerate(rows[:10], start=1)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="claim_eval.yaml")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8")).get("cases", [])
    results = [evaluate_case(case, args.top_k) for case in cases]
    if args.json:
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{result['status']:10} {result['id']}: {result['reason']}")
            print(f"  term ranks={result['term_hit_ranks']} scoped ranks={result['scoped_hit_ranks']}")
    return 1 if any(item["status"] == "FAIL_ALG" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
