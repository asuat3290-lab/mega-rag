#!/usr/bin/env python3
"""Regression for propagating agent scopes into retrieval and rerank profiles."""

import unittest

from glossary_loader import load_glossary
from research_export import _prepare_query


class QueryScopeProfileTests(unittest.TestCase):
    def test_agent_target_volumes_reach_query_profile(self):
        prepared = _prepare_query(
            "Stoffwechsel",
            load_glossary(),
            planner_mode="agent_supplied",
            plan_refinement={
                "focus_terms": ["Stoffwechsel"],
                "target_volumes": ["II/1", "II/5"],
                "intent": "author_argument",
            },
        )
        profile = prepared["query_profile"]
        scopes = {
            (scope.get("abteilung"), scope.get("band"))
            for scope in profile["target_volumes"]
        }
        self.assertEqual(profile["target_abteilung"], "II")
        self.assertEqual(profile["target_band"], "1")
        self.assertIn(("II", "1"), scopes)
        self.assertIn(("II", "5"), scopes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
