#!/usr/bin/env python3
"""Fast regression tests for deterministic query planning."""

import unittest
from unittest.mock import patch

from query_plan import build_query_plan


class QueryPlanTests(unittest.TestCase):
    def test_concentration_distinguishes_historical_and_related_terms(self):
        plan = build_query_plan("马克思如何讨论资本集中与竞争的关系？")
        self.assertEqual(plan["intent"], "concept_relation")
        self.assertIn("Konzentration", plan["core_terms"])
        self.assertIn("Concentration", plan["historical_variants"])
        self.assertIn("Centralisation", plan["related_non_equivalent"])
        self.assertNotIn("Centralisation", plan["core_terms"])
        self.assertIn("Kapital", plan["generic_terms"])
        self.assertTrue(any(branch["id"] == "sachregister" for branch in plan["branches"]))
        self.assertTrue(any(volume.get("label") == "II/10" for volume in plan["target_volumes"]))

    def test_generic_term_remains_core_when_queried_alone(self):
        plan = build_query_plan("Kapital")
        self.assertIn("Kapital", plan["core_terms"])

    def test_claim_plan_demotes_contextual_generic_terms(self):
        plan = build_query_plan(
            "马克思在资本论手稿中主要用 Entfremdung 描述资本关系",
            intent_override="claim_verification",
        )
        self.assertEqual(plan["intent"], "claim_verification")
        self.assertEqual(plan["routing_intent"], "author_argument")
        self.assertEqual(plan["claim_strength"], "predominant")
        self.assertIn("Entfremdung", plan["core_terms"])
        self.assertIn("Kapital", plan["generic_terms"])
        self.assertNotIn("Kapital", plan["core_terms"])
        self.assertTrue(any(volume.get("abteilung") == "II" for volume in plan["target_volumes"]))

    def test_local_planner_never_calls_model_gateway(self):
        with patch("model_gateway.call_json", side_effect=AssertionError("API called")):
            plan = build_query_plan("Subsumtion Hegelschen Rechtsphilosophie")
        self.assertEqual(plan["mode"], "local")
        self.assertIn("Subsumtion", plan["core_terms"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
