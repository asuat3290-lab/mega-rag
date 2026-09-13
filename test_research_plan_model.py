#!/usr/bin/env python3
"""No-network tests for bounded ResearchPlan model refinement."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from research_plan_model import build_hybrid_research_plan


class ResearchPlanModelTests(unittest.TestCase):
    @patch("research_plan_model.call_json")
    def test_valid_refinement_is_merged(self, call_json):
        call_json.return_value = (
            {
                "resolved_concepts": ["\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406"],
                "external_concepts": ["\u91cf\u5b50\u6280\u672f\u6cbb\u7406"],
                "subquestions": [
                    {
                        "id": "external_fact",
                        "question": "\u91cf\u5b50\u6280\u672f\u6cbb\u7406\u7684\u5f53\u4ee3\u7ecf\u9a8c\u4e8b\u5b9e\u662f\u4ec0\u4e48\uff1f",
                        "type": "external_empirical",
                        "external_concepts": ["\u91cf\u5b50\u6280\u672f\u6cbb\u7406"],
                        "required_corpus": ["external_empirical"],
                    }
                ],
            },
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "flash-test",
        )
        plan, diagnostics = build_hybrid_research_plan(
            "\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u91cf\u5b50\u7ea0\u7f20\u6cbb\u7406",
            use_cache=False,
        )
        self.assertEqual(plan["coverage_status"], "complete")
        self.assertTrue(plan["external_evidence_required"])
        self.assertFalse(diagnostics["fallback"])
        self.assertEqual(diagnostics["usage"]["total_tokens"], 30)

    @patch("research_plan_model.call_json", side_effect=RuntimeError("offline"))
    def test_model_failure_falls_back_to_local_plan(self, _call_json):
        plan, diagnostics = build_hybrid_research_plan("\u9a6c\u514b\u601d\u5982\u4f55\u8ba8\u8bba\u5229\u6da6\u7387\u4e0b\u964d", use_cache=False)
        self.assertEqual(plan["mode"], "hybrid_fallback_local")
        self.assertTrue(diagnostics["fallback"])
        self.assertEqual(diagnostics["usage"]["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

