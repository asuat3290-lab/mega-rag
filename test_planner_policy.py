#!/usr/bin/env python3
"""Regression tests for agent-first planning decisions."""

import unittest

from planner_policy import assess_query_plan, assess_research_plan
from query_plan import build_query_plan
from research_plan import build_research_plan


class PlannerPolicyTests(unittest.TestCase):
    def test_precise_german_query_stays_local(self):
        advice = assess_query_plan(
            build_query_plan("Subsumtion Hegelschen Rechtsphilosophie")
        )
        self.assertEqual(advice["decision"], "local")
        self.assertFalse(advice["should_refine"])

    def test_unknown_chinese_concept_requires_agent_refinement(self):
        plan = build_research_plan(
            "\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba"
            "\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406"
        )
        advice = assess_research_plan(plan)
        self.assertEqual(advice["decision"], "refinement_required")
        self.assertIn("meaningful_unmapped_concepts", advice["reason_codes"])

    def test_diachronic_question_recommends_agent_without_forcing_it(self):
        plan = build_research_plan(
            "\u9a6c\u514b\u601d\u5f02\u5316\u6982\u5ff5\u7684\u53d8\u5316"
        )
        advice = assess_research_plan(plan)
        self.assertEqual(advice["decision"], "refinement_recommended")
        self.assertIn("diachronic_branch_selection", advice["reason_codes"])

    def test_completed_agent_decomposition_does_not_request_it_again(self):
        plan = {
            "mode": "agent_supplied",
            "question_type": "concept_relation",
            "coverage_status": "complete",
            "unmapped_concepts": [],
            "external_evidence_required": False,
            "subquestions": [
                {
                    "id": "Q01",
                    "required_corpus": ["mega"],
                    "query_plan": {
                        "plan_status": {"valid_for_evidence": True}
                    },
                }
            ],
        }
        advice = assess_research_plan(plan)
        self.assertEqual(advice["decision"], "local")
        self.assertNotIn(
            "compound_question_decomposition", advice["reason_codes"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
