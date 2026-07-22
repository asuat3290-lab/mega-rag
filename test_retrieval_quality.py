#!/usr/bin/env python3
"""Fast regressions for evidence qualification and adequacy states."""

import unittest

from query_plan import build_query_plan
from retrieval_quality import apply_candidate_qualification, assess_retrieval_adequacy


class RetrievalQualityTests(unittest.TestCase):
    def test_generic_capital_is_not_a_direct_concentration_hit(self):
        plan = build_query_plan("马克思如何讨论资本集中与竞争的关系？")
        rows = [
            {"id": "generic", "text": "Das Kapital hat eine Umschlagszeit.",
             "type": "TEXT", "text_layer": "author_text", "abteilung": "II", "band": "10"},
            {"id": "direct", "text": "Die Concentration des Kapitals schreitet fort.",
             "type": "TEXT", "text_layer": "author_text", "abteilung": "II", "band": "10"},
        ]
        ranked = apply_candidate_qualification(rows, plan)
        self.assertEqual(ranked[0]["id"], "direct")
        self.assertEqual(ranked[0]["_qualification"]["candidate_class"], "direct_author_text")
        self.assertEqual(ranked[1]["_qualification"]["candidate_class"], "generic_only")

    def test_related_concept_is_not_treated_as_equivalent(self):
        plan = build_query_plan("资本集中")
        rows = [{"id": "related", "text": "Centralisation des Kapitals",
                 "type": "TEXT", "text_layer": "author_text",
                 "abteilung": "II", "band": "10"}]
        apply_candidate_qualification(rows, plan)
        quality = rows[0]["_qualification"]
        self.assertEqual(quality["candidate_class"], "related_concept_context")
        self.assertFalse(rows[0]["evidence_eligible"])

    def test_strong_quantifier_can_only_be_partial_from_passages(self):
        plan = build_query_plan(
            "马克思在资本论手稿中主要用 Entfremdung 描述资本关系",
            intent_override="claim_verification",
        )
        rows = [{"id": "direct", "text": "Entfremdung der Arbeit",
                 "type": "TEXT", "text_layer": "author_text",
                 "abteilung": "II", "band": "1"}]
        apply_candidate_qualification(rows, plan)
        adequacy = assess_retrieval_adequacy(rows, plan)
        self.assertEqual(adequacy["status"], "partial")
        self.assertIsNotNone(adequacy["strong_claim_warning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
