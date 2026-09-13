#!/usr/bin/env python3
"""Regression tests for multi-branch research orchestration."""
from __future__ import annotations

import unittest

from research_orchestrator import _select_records, run_research
from research_plan import build_research_plan


COMPOUND_QUERY = (
    "\u9a6c\u514b\u601d\u600e\u6837\u8ba8\u8bba\u5229\u6da6\u7387\u4e0b\u964d\uff0c\u4eba\u5de5\u667a\u80fd\u662f\u5426\u4f1a\u964d\u4f4e\u5229\u6da6\u7387\uff0c"
    "\u5982\u4f55\u7528\u673a\u5668\u7406\u8bba\u8bf4\u660e"
)


def _record(record_id: str = "test:shared") -> dict:
    return {
        "id": record_id,
        "page_id": record_id,
        "record_type": "page",
        "abteilung": "II",
        "band": "4.2",
        "type": "TEXT",
        "page": 221,
        "page_label": "221",
        "text": "Die Profitrate f\u00e4llt; zugleich sind entgegenwirkende Ursachen zu untersuchen.",
        "display_snippet": (
            "Die Profitrate f\u00e4llt; zugleich sind entgegenwirkende Ursachen zu untersuchen."
        ),
        "display_preview": "Die Profitrate f\u00e4llt.",
        "matched_term": "Profitrate",
        "text_layer": "author_text",
        "source_collection": "megadigital",
        "source_quality": "authoritative_digital",
        "source_title": "Das Kapital, Oekonomisches Manuskript 1863-1865, Teil 2",
        "source_doc": "test",
        "ocr_quality": "high",
        "evidence_eligible": True,
        "claim_eligible": True,
        "claim_ready": True,
        "_qualification": {
            "claim_eligible": True,
            "claim_ready": True,
            "target_scope_match": True,
            "direct_core_hits": ["Profitrate"],
            "qualification_bucket": 0,
        },
        "_retrieval_sources": ["unit_test"],
    }


def _payload(status: str = "adequate", record_id: str = "test:shared") -> dict:
    return {
        "query": {"priority_terms": ["Profitrate", "entgegenwirkende Ursachen"]},
        "retrieval": {
            "debug": {"adequacy": {"status": status, "reason": status}},
            "term_probe": {"summary": {"total_pages": 1}},
            "navigation": {"sachregister": []},
        },
        "results": [_record(record_id)],
    }


class ResearchOrchestratorTests(unittest.TestCase):
    def test_external_requirement_keeps_compound_run_partial(self):
        calls = []

        def retriever(query, **kwargs):
            calls.append((query, kwargs))
            return _payload()

        run = run_research(
            COMPOUND_QUERY,
            top_k_per_branch=2,
            max_evidence=10,
            retriever=retriever,
        )
        self.assertEqual(run["status"]["overall"], "partial")
        self.assertEqual(run["status"]["answerability"], "partially_answerable")
        self.assertTrue(run["status"]["missing_requirements"])
        self.assertGreaterEqual(len(calls), 4)
        self.assertEqual(len(run["evidence"]), 1)
        self.assertGreater(
            len(run["evidence"][0]["research"]["branch_ids"]), 1
        )
        self.assertTrue(
            any(
                row["claim_type"] == "empirical_hypothesis"
                for row in run["claim_evidence_matrix"]
            )
        )

    def test_one_failed_required_branch_blocks_overall_adequacy(self):
        plan = build_research_plan("\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u5229\u6da6\u7387\u4e0b\u964d")

        def retriever(query, **kwargs):
            if "entgegenwirkenden" in query.casefold():
                return _payload(status="retrieval_gap", record_id="test:gap")
            return _payload()

        run = run_research(
            plan["question"],
            research_plan=plan,
            retriever=retriever,
        )
        self.assertEqual(run["status"]["overall"], "retrieval_gap")
        self.assertTrue(
            any(
                branch["mega_retrieval_status"] == "retrieval_gap"
                for branch in run["branches"]
            )
        )

    def test_all_mega_branches_adequate_is_answerable(self):
        plan = build_research_plan("\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u5229\u6da6\u7387\u4e0b\u964d")

        def retriever(query, **kwargs):
            return _payload(record_id="test:" + str(abs(hash(query))))

        run = run_research(
            plan["question"],
            research_plan=plan,
            retriever=retriever,
        )
        self.assertEqual(run["status"]["overall"], "adequate")
        self.assertEqual(run["status"]["answerability"], "answerable")
        self.assertFalse(run["status"]["missing_requirements"])
    def test_evidence_budget_is_allocated_across_required_branches(self):
        plan = build_research_plan(COMPOUND_QUERY)
        mega_branches = [
            item for item in plan["subquestions"]
            if "mega" in item.get("required_corpus", [])
        ]

        def retriever(query, **kwargs):
            return _payload(record_id="test:" + str(abs(hash(query))))

        run = run_research(
            plan["question"],
            research_plan=plan,
            max_evidence=len(mega_branches),
            retriever=retriever,
        )
        branch_by_id = {item["id"]: item for item in run["branches"]}
        self.assertEqual(len(run["evidence"]), len(mega_branches))
        self.assertTrue(
            all(branch_by_id[item["id"]]["evidence_ids"] for item in mega_branches)
        )



    def test_definition_evidence_is_selected_over_tangential_example(self):
        tangential = _record("test:tangential")
        tangential["text"] = (
            "Unterscheidung von produktiver und unproduktiver Arbeit. "
            "Die Produktivkraft ist vermindert und die Rate des Mehrwerts bleibt gleich."
        )
        definition = _record("test:definition")
        definition["text"] = (
            "Nur die Arbeit, die Kapital produziert, ist produktive Arbeit. "
            "Der Gebrauchswert besteht nicht in der konkreten Arbeit, sondern in ihrer Funktion."
        )
        subquestion = {
            "question": "Was ist der Unterschied im Begriff der produktiven Arbeit?",
            "type": "textual_reconstruction",
        }
        selected = _select_records(
            [tangential, definition], 1, subquestion
        )
        self.assertEqual(selected[0]["id"], "test:definition")
        debug = selected[0]["_debug"]["evidence_selection"]
        self.assertEqual(debug["cue_family"], "definition_or_distinction")
        self.assertGreaterEqual(debug["cue_hit_count"], 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)

