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

    def test_structured_alias_and_source_language_term_share_one_concept(self):
        glossary = {
            "研究概念": {
                "de": ["präziser Ausdruck", "historische Form"],
                "aliases": ["概念别名"],
                "type": "concept",
                "priority": "high",
                "related": [],
                "senses": [],
                "abteilung_hint": None,
                "band_hint": None,
                "query_mode": ["exact"],
                "must_search_exact": False,
                "_legacy": False,
            }
        }
        alias_plan = build_query_plan("概念别名", glossary=glossary)
        german_plan = build_query_plan("präziser Ausdruck", glossary=glossary)
        for plan in (alias_plan, german_plan):
            self.assertTrue(plan["plan_status"]["valid_for_evidence"])
            self.assertIn("präziser Ausdruck", plan["core_terms"])
            self.assertEqual(
                plan["qualification_groups"][0]["alternatives"][0],
                "präziser Ausdruck",
            )

    def test_legacy_glossary_is_recall_only_until_phrase_migration(self):
        glossary = {
            "旧词条": {
                "de": ["alpha beta gamma"],
                "aliases": [],
                "type": "concept",
                "related": [],
                "senses": [],
                "abteilung_hint": None,
                "band_hint": None,
                "query_mode": ["exact"],
                "must_search_exact": False,
                "_legacy": True,
                "_legacy_raw": "alpha beta gamma",
            }
        }
        plan = build_query_plan("旧词条", glossary=glossary)
        self.assertFalse(plan["plan_status"]["valid_for_evidence"])
        self.assertIn("missing_qualification_groups", plan["plan_status"]["issues"])
        self.assertEqual(plan["plan_status"]["legacy_ambiguous_terms"], ["旧词条"])

    def test_work_title_does_not_impersonate_a_core_concept(self):
        ideology = build_query_plan("德意志意识形态中马克思如何讨论现实的人")
        self.assertNotIn("Ideologie", ideology["core_terms"])
        self.assertIn("Ideologie", ideology["generic_terms"])
        self.assertIn("wirklich thätigen Menschen", ideology["core_terms"])
        self.assertEqual(
            ideology["plan_status"]["title_embedded_terms"], ["意识形态"]
        )

        capital = build_query_plan("资本论手稿中关于剩余价值的论述")
        self.assertNotIn("Kapital", capital["core_terms"])
        self.assertIn("Mehrwert", capital["core_terms"])

    def test_repeated_concept_outside_work_title_remains_core(self):
        plan = build_query_plan("德意志意识形态中如何讨论意识形态概念")
        self.assertIn("Ideologie", plan["core_terms"])
        self.assertNotIn("意识形态", plan["plan_status"]["title_embedded_terms"])

    def test_recall_only_terms_do_not_qualify_chinese_concept_evidence(self):
        glossary = {
            "宽概念": {
                "de": ["exact phrase"],
                "recall": ["broad navigation term"],
                "aliases": [],
                "type": "concept",
                "priority": "normal",
                "related": [],
                "senses": [],
                "abteilung_hint": None,
                "band_hint": None,
                "query_mode": ["exact"],
                "must_search_exact": False,
                "_legacy": False,
            }
        }
        plan = build_query_plan("宽概念", glossary=glossary)
        self.assertIn("broad navigation term", plan["expanded_query"])
        self.assertIn("exact phrase", plan["core_terms"])
        self.assertNotIn("broad navigation term", plan["core_terms"])
        self.assertEqual(
            plan["qualification_groups"][0]["alternatives"], ["exact phrase"]
        )

    def test_targeted_apparat_question_is_valid_without_a_concept_term(self):
        plan = build_query_plan("德意志意识形态的编者注和异文说明")
        self.assertEqual(plan["routing_intent"], "apparat_question")
        self.assertFalse(plan["core_terms"])
        self.assertTrue(plan["plan_status"]["scope_only_valid"])
        self.assertTrue(plan["plan_status"]["valid_for_evidence"])
        self.assertEqual(plan["target_volumes"][0]["abteilung"], "I")
        self.assertEqual(plan["target_volumes"][0]["band"], "5")

    def test_bare_year_does_not_force_a_work_volume(self):
        manuscript = build_query_plan("1844手稿中马克思如何讨论异化")
        self.assertTrue(any(
            volume.get("abteilung") == "I" and volume.get("band") == "2"
            for volume in manuscript["target_volumes"]
        ))

        letters = build_query_plan("1844年马克思书信中的经济状况")
        self.assertFalse(any(
            volume.get("abteilung") == "I" and volume.get("band") == "2"
            for volume in letters["target_volumes"]
        ))

    def test_local_planner_never_calls_model_gateway(self):
        with patch("model_gateway.call_json", side_effect=AssertionError("API called")):
            plan = build_query_plan("Subsumtion Hegelschen Rechtsphilosophie")
        self.assertEqual(plan["mode"], "local")
        self.assertIn("Subsumtion", plan["core_terms"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
