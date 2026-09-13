#!/usr/bin/env python3
"""Regression tests for question-level MEGA research planning."""
from __future__ import annotations

import unittest

from research_plan import build_research_plan


COMPOUND_QUERY = (
    "\u9a6c\u514b\u601d\u600e\u6837\u8ba8\u8bba\u5229\u6da6\u7387\u4e0b\u964d\uff0c\u4eba\u5de5\u667a\u80fd\u662f\u5426\u4f1a\u964d\u4f4e\u5229\u6da6\u7387\uff0c"
    "\u5982\u4f55\u7528\u673a\u5668\u7406\u8bba\u8bf4\u660e"
)


class ResearchPlanTests(unittest.TestCase):
    def test_compound_question_is_covered_by_distinct_evidence_branches(self):
        plan = build_research_plan(COMPOUND_QUERY)
        types = {item["type"] for item in plan["subquestions"]}
        self.assertEqual(plan["coverage_status"], "complete")
        self.assertFalse(plan["unmapped_concepts"])
        self.assertTrue(plan["external_evidence_required"])
        self.assertIn("\u5229\u6da6\u7387\u4e0b\u964d", plan["covered_concepts"])
        self.assertIn("\u673a\u5668", plan["covered_concepts"])
        self.assertIn("\u4eba\u5de5\u667a\u80fd", plan["external_concepts"])
        self.assertIn("textual_reconstruction", types)
        self.assertIn("modern_application", types)
        self.assertIn("concept_bridge", types)
        self.assertIn("counter_evidence", types)
        self.assertIn("profit_rate_fall", plan["matched_rule_ids"])
        self.assertIn("capitalist_machinery", plan["matched_rule_ids"])

    def test_unknown_concept_keeps_plan_incomplete(self):
        plan = build_research_plan("\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406")
        self.assertEqual(plan["coverage_status"], "incomplete")
        self.assertTrue(plan["needs_refinement"])
        self.assertTrue(
            any("\u91cf\u5b50\u7ea0\u7f20" in value for value in plan["unmapped_concepts"])
        )

    def test_german_query_is_first_class_without_chinese_glossary_key(self):
        plan = build_research_plan("Subsumtion Hegelschen Rechtsphilosophie")
        self.assertFalse(plan["unmapped_concepts"])
        query_plan = plan["subquestions"][0]["query_plan"]
        self.assertTrue(query_plan["core_terms"])
        self.assertTrue(
            any("subsumtion" in value.casefold() for value in query_plan["core_terms"])
        )

    def test_agent_refinement_can_resolve_unmapped_concept(self):
        refinement = {
            "resolved_concepts": ["\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406"],
            "external_concepts": ["\u91cf\u5b50\u6280\u672f\u6cbb\u7406"],
            "subquestions": [
                {
                    "id": "external_quantum_governance",
                    "question": "\u5f53\u4ee3\u91cf\u5b50\u6280\u672f\u6cbb\u7406\u7684\u7ecf\u9a8c\u4e8b\u5b9e\u662f\u4ec0\u4e48\uff1f",
                    "type": "external_empirical",
                    "covered_concepts": [],
                    "external_concepts": ["\u91cf\u5b50\u6280\u672f\u6cbb\u7406"],
                    "required_corpus": ["external_empirical"],
                }
            ],
        }
        plan = build_research_plan(
            "\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406",
            mode="agent_supplied",
            refinement=refinement,
        )
        self.assertEqual(plan["coverage_status"], "complete")
        self.assertFalse(plan["unmapped_concepts"])
        self.assertTrue(plan["external_evidence_required"])
        self.assertNotIn(
            "Meaningful concepts remain unmapped; use agent refinement or bounded "
            "hybrid planning.",
            plan["warnings"],
        )

    def test_unknown_chinese_concepts_are_not_damaged_by_frame_removal(self):
        plan = build_research_plan(
            "\u9a6c\u514b\u601d\u665a\u5e74\u8de8\u8d8a"
            "\u5361\u592b\u4e01\u5ce1\u8c37\u8bbe\u60f3\u4e0e"
            "\u4e24\u4e2a\u51b3\u4e0d\u4f1a\u7684\u5173\u7cfb"
        )
        residue = " ".join(plan["unmapped_concepts"])
        self.assertIn("\u5361\u592b\u4e01\u5ce1\u8c37", residue)
        self.assertIn("\u4e24\u4e2a\u51b3\u4e0d\u4f1a", residue)
        self.assertNotIn(
            "\u4e24\u4e2a\u51b3\u4e0d\u7684\u5173\u7cfb", residue
        )

    def test_compound_subquestions_inherit_scope_and_fail_closed_when_unmapped(self):
        question = (
            "\u9a6c\u514b\u601d\u5728\u300a\u5269\u4f59\u4ef7\u503c\u7406\u8bba\u300b\u4e2d\u5982\u4f55\u6279\u5224\u65af\u5bc6\u7684"
            "\u751f\u4ea7\u52b3\u52a8/\u975e\u751f\u4ea7\u52b3\u52a8\u533a\u5206\uff1f"
            "\u9a6c\u514b\u601d\u81ea\u5df1\u7684\u6982\u5ff5\u4e0e\u65af\u5bc6\u6709\u4f55\u4e0d\u540c\uff1f"
            "\u8fd9\u4e00\u6279\u5224\u4e0e\u5269\u4f59\u4ef7\u503c\u7406\u8bba\u6574\u4f53\u67b6\u6784\u6709\u4f55\u5173\u7cfb\uff1f"
        )
        plan = build_research_plan(question)
        self.assertGreaterEqual(len(plan["subquestions"]), 3)
        for subquestion in plan["subquestions"][:3]:
            scopes = subquestion["query_plan"].get("target_volumes", [])
            self.assertTrue(
                any(
                    scope.get("abteilung") == "II"
                    and str(scope.get("band")) == "3"
                    for scope in scopes
                ),
                subquestion,
            )
        inherited = plan["subquestions"][1].get("query_refinement") or {}
        context_terms = {
            str(value).casefold() for value in inherited.get("context_terms", [])
        }
        self.assertIn("produktiv", context_terms)
        self.assertFalse(
            plan["subquestions"][1]["query_plan"]["plan_status"]["valid_for_evidence"]
        )
        self.assertTrue(plan["needs_refinement"])

if __name__ == "__main__":
    unittest.main(verbosity=2)

