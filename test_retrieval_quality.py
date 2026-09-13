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

    def test_multiword_lexical_group_rejects_single_component_noise(self):
        plan = {
            "intent": "author_argument",
            "routing_intent": "author_argument",
            "claim_strength": "ordinary",
            "core_terms": ["konkrete", "Individuen"],
            "historical_variants": [],
            "related_non_equivalent": [],
            "supporting_terms": [],
            "generic_terms": [],
            "target_volumes": [],
            "relation_pairs": [],
            "qualification_groups": [{
                "id": "lexical_core",
                "alternatives": ["konkrete", "Individuen"],
                "match_mode": "all_near",
                "window_chars": 120,
            }],
            "plan_status": {"valid_for_evidence": True, "issues": []},
        }
        rows = [
            {"id": "noise", "text": "Die konkrete nützliche Arbeit.",
             "type": "TEXT", "text_layer": "author_text"},
            {"id": "direct", "text": "Die konkreten gesellschaftlichen Individuen handeln.",
             "type": "TEXT", "text_layer": "author_text"},
        ]
        ranked = apply_candidate_qualification(rows, plan)
        self.assertEqual(ranked[0]["id"], "direct")
        self.assertTrue(ranked[0]["evidence_eligible"])
        self.assertFalse(ranked[1]["evidence_eligible"])

    def test_apparat_intent_qualifies_editorial_evidence_not_author_text(self):
        plan = {
            "intent": "apparat_question",
            "routing_intent": "apparat_question",
            "claim_strength": "ordinary",
            "core_terms": ["Variante"],
            "historical_variants": [],
            "related_non_equivalent": [],
            "supporting_terms": [],
            "generic_terms": [],
            "target_volumes": [],
            "relation_pairs": [],
            "qualification_groups": [{"id": "variant", "alternatives": ["Variante"]}],
            "plan_status": {"valid_for_evidence": True, "issues": []},
        }
        rows = [
            {"id": "text", "text": "Variante im Argument.",
             "type": "TEXT", "text_layer": "author_text"},
            {"id": "apparat", "text": "Die Variante der Handschrift.",
             "type": "APPARAT", "text_layer": "apparatus"},
        ]
        ranked = apply_candidate_qualification(rows, plan)
        self.assertEqual(ranked[0]["id"], "apparat")
        self.assertTrue(ranked[0]["evidence_eligible"])
        self.assertFalse(ranked[1]["evidence_eligible"])

    def test_scope_only_apparat_query_qualifies_target_editorial_pages(self):
        plan = build_query_plan("德意志意识形态的编者注和异文说明")
        rows = [
            {"id": "target", "text": "Zur Entstehung der Handschrift.",
             "type": "APPARAT", "text_layer": "apparatus",
             "abteilung": "I", "band": "5"},
            {"id": "front", "text": "Inhaltsverzeichnis.",
             "type": "APPARAT", "text_layer": "table_of_contents",
             "abteilung": "I", "band": "5"},
            {"id": "other", "text": "Zur Entstehung einer anderen Handschrift.",
             "type": "APPARAT", "text_layer": "apparatus",
             "abteilung": "I", "band": "6"},
        ]
        ranked = apply_candidate_qualification(rows, plan)
        by_id = {row["id"]: row for row in ranked}
        self.assertTrue(by_id["target"]["evidence_eligible"])
        self.assertEqual(
            by_id["target"]["_qualification"]["candidate_class"],
            "scoped_editorial_evidence",
        )
        self.assertFalse(by_id["front"]["evidence_eligible"])
        self.assertFalse(by_id["other"]["evidence_eligible"])

    def test_adequacy_exposes_semantic_provenance_and_citation_axes(self):
        plan = build_query_plan("利润率下降")
        rows = [{
            "id": "provisional", "text": "Die fallende Profitrate.",
            "type": "TEXT", "text_layer": "textband_unclassified",
            "source_collection": "ocr", "abteilung": "II", "band": "4.2",
        }]
        apply_candidate_qualification(rows, plan)
        adequacy = assess_retrieval_adequacy(rows, plan)
        self.assertEqual(adequacy["axes"]["semantic"]["status"], "partial")
        self.assertEqual(adequacy["axes"]["provenance"]["status"], "provisional")
        self.assertEqual(adequacy["axes"]["citation"]["status"], "not_ready")

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
