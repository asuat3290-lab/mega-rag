#!/usr/bin/env python3
"""Regressions for low-friction agent focus, context, and scope hints."""

import unittest

from planner_policy import assess_query_plan
from query_hints import merge_query_hints
from query_plan import build_query_plan
from report_contract import evaluate_package_gate
from retrieval_quality import apply_candidate_qualification


class AgentFocusFlowTests(unittest.TestCase):
    def test_focus_is_required_while_context_remains_recall_only(self):
        query = (
            "Stoffwechsel Natur Arbeit Boden Agrikultur "
            "kapitalistische Produktionsweise"
        )
        refinement = merge_query_hints(
            focus_terms=["Stoffwechsel"],
            context_terms=[
                "Natur",
                "Arbeit",
                "Boden",
                "Agrikultur",
                "kapitalistische Produktionsweise",
            ],
            target_volumes=["II/1", "II/5"],
            intent="author_argument",
        )
        plan = build_query_plan(query, refinement=refinement)
        rows = [
            {
                "id": "context-only",
                "text": (
                    "Die kapitalistische Produktionsweise veraendert Natur, "
                    "Arbeit, Boden und Agrikultur."
                ),
                "type": "TEXT",
                "text_layer": "author_text",
                "abteilung": "II",
                "band": "5",
            },
            {
                "id": "focus",
                "text": "Der Stoffwechsel zwischen Mensch und Erde wird gestoert.",
                "type": "TEXT",
                "text_layer": "author_text",
                "abteilung": "II",
                "band": "1.2",
            },
        ]

        ranked = apply_candidate_qualification(rows, plan)
        by_id = {row["id"]: row for row in ranked}
        self.assertEqual(ranked[0]["id"], "focus")
        self.assertTrue(by_id["focus"]["evidence_eligible"])
        self.assertFalse(by_id["context-only"]["evidence_eligible"])
        self.assertTrue(
            by_id["context-only"]["_qualification"]["focus_missing"]
        )
        self.assertEqual(plan["focus_terms"][0], "Stoffwechsel")

    def test_multi_work_query_preserves_every_detected_scope(self):
        query = (
            "\u9a6c\u514b\u601d\u4ece\u300a\u5927\u7eb2\u300b\u5230"
            "\u300a\u8d44\u672c\u8bba\u300b\u5982\u4f55\u8ba8\u8bba "
            "Stoffwechsel\uff1f"
        )
        plan = build_query_plan(query)
        scopes = {
            (volume.get("abteilung"), volume.get("band"))
            for volume in plan["target_volumes"]
        }
        self.assertIn(("II", "1"), scopes)
        self.assertIn(("II", None), scopes)

    def test_flat_lexical_query_receives_advisory_not_a_hard_block(self):
        plan = build_query_plan(
            "Stoffwechsel Natur Arbeit Boden Agrikultur "
            "kapitalistische Produktionsweise"
        )
        advice = assess_query_plan(plan)
        self.assertTrue(plan["plan_status"]["valid_for_evidence"])
        self.assertEqual(advice["decision"], "refinement_recommended")
        self.assertIn(
            "flat_multi_term_query_needs_focus",
            advice["reason_codes"],
        )

    def test_explicit_focus_miss_closes_only_the_synthesis_gate(self):
        package = {
            "query": {
                "query_plan": {
                    "plan_status": {"valid_for_evidence": True}
                }
            },
            "summary": {
                "qualified_evidence_count": 2,
                "citation_ready_count": 1,
            },
            "retrieval": {
                "debug": {
                    "adequacy": {"status": "partial", "axes": {}},
                    "focus_diagnostics": {
                        "explicit": True,
                        "qualified_final_hits": 0,
                        "warnings": [
                            "explicit_focus_not_in_qualified_final_evidence"
                        ],
                    },
                }
            },
        }
        gate = evaluate_package_gate(package)
        self.assertFalse(gate["synthesis_allowed"])
        self.assertIn("explicit_focus_not_qualified", gate["errors"])
        self.assertEqual(
            gate["required_next_action"], "repair_plan_or_retrieval"
        )


    def test_explicit_focus_generates_historical_c_k_spelling_variants(self):
        plan = build_query_plan(
            "produktive Arbeit",
            refinement={
                "focus_terms": ["produktive Arbeit", "unproduktive Arbeit"],
                "intent": "author_argument",
            },
        )
        generated = {
            value.casefold()
            for value in plan.get("generated_historical_variants", [])
        }
        self.assertIn("productive arbeit", generated)
        self.assertIn("unproductive arbeit", generated)
        required = [
            group for group in plan["qualification_groups"]
            if group.get("required_for_evidence")
        ]
        alternatives = {
            value.casefold()
            for group in required
            for value in group.get("alternatives", [])
        }
        self.assertIn("productive arbeit", alternatives)
        rows = [
            {
                "id": "historical-spelling",
                "text": "Nur die Arbeit, die Capital producirt, ist productive Arbeit.",
                "type": "TEXT",
                "text_layer": "author_text",
                "abteilung": "II",
                "band": "3",
            }
        ]
        qualified = apply_candidate_qualification(rows, plan)
        self.assertTrue(qualified[0]["evidence_eligible"])

    def test_explicit_author_intent_overrides_local_german_classification(self):
        plan = build_query_plan(
            "Wie kritisiert Marx Smiths Bestimmung der produktiven Arbeit?",
            refinement={
                "focus_terms": ["produktive Arbeit"],
                "intent": "author_argument",
                "target_volumes": ["II/3"],
            },
        )
        self.assertEqual(plan["routing_intent"], "author_argument")
        rows = [
            {
                "id": "editor-introduction",
                "text": "Die Einleitung erörtert produktive Arbeit.",
                "type": "TEXT",
                "text_layer": "editorial_intro",
                "abteilung": "II",
                "band": "3",
            },
            {
                "id": "author-text",
                "text": "Nur die Arbeit, die Capital producirt, ist productive Arbeit.",
                "type": "TEXT",
                "text_layer": "author_text",
                "abteilung": "II",
                "band": "3",
            },
        ]
        ranked = apply_candidate_qualification(rows, plan)
        by_id = {row["id"]: row for row in ranked}
        self.assertFalse(by_id["editor-introduction"]["evidence_eligible"])
        self.assertTrue(by_id["author-text"]["claim_eligible"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
