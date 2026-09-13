#!/usr/bin/env python3
"""No-network tests for optional hybrid QueryPlan refinement."""

import json
import unittest
from unittest.mock import patch

from query_plan import build_query_plan
from query_plan_model import build_hybrid_plan


class HybridPlannerTests(unittest.TestCase):
    @patch("query_plan_model.search_sachregister", return_value={"hits": []})
    @patch("query_plan_model.probe_query_plan", return_value={"terms": [], "summary": {}})
    @patch("query_plan_model.call_json")
    def test_model_terms_pass_through_validator(self, call, _probe, _register):
        call.return_value = (
            {"core_terms": ["Plebs"], "related_non_equivalent": ["Pöbel"],
             "generic_terms": ["Marx"]},
            {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            "test-flash",
        )
        plan, diagnostics = build_hybrid_plan("贱民", local_plan=build_query_plan("贱民"), use_cache=False)
        self.assertIn("Plebs", plan["core_terms"])
        self.assertIn("Pöbel", plan["related_non_equivalent"])
        self.assertEqual(diagnostics["usage"]["total_tokens"], 70)
        self.assertFalse(diagnostics["fallback"])

    @patch("query_plan_model.search_sachregister", return_value={"hits": []})
    @patch("query_plan_model.probe_query_plan", return_value={"terms": [], "summary": {}})
    @patch("query_plan_model.call_json", side_effect=RuntimeError("offline"))
    def test_model_failure_falls_back_to_local(self, _call, _probe, _register):
        local = build_query_plan("Subsumtion")
        plan, diagnostics = build_hybrid_plan("Subsumtion", local_plan=local, use_cache=False)
        self.assertEqual(plan["core_terms"], local["core_terms"])
        self.assertTrue(diagnostics["fallback"])
        self.assertEqual(diagnostics["usage"]["total_tokens"], 0)

    def test_cached_refinement_avoids_model_call(self):
        cached = json.dumps({"core_terms": ["Plebs"]})
        local = build_query_plan("Pauper")
        with patch("query_plan_model.cache_get", return_value=cached), \
                patch("query_plan_model.call_json") as model_call:
            plan, diagnostics = build_hybrid_plan("Pauper", local_plan=local)
        model_call.assert_not_called()
        self.assertIn("Plebs", plan["core_terms"])
        self.assertTrue(diagnostics["cache_hit"])
        self.assertEqual(diagnostics["usage"]["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
