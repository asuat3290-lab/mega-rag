#!/usr/bin/env python3
"""Regression tests for fail-closed planning and TEXT evidence gates."""

import unittest

from report_contract import evaluate_package_gate, evaluate_research_run_gate


class ReportContractGateTests(unittest.TestCase):
    def _package(self, text_type="TEXT", advice=None):
        return {
            "query": {
                "planning_advice": advice or {"decision": "local", "reason_codes": []},
                "query_plan": {"plan_status": {"valid_for_evidence": True}},
                "query_profile": {"intent": "author_argument"},
            },
            "summary": {
                "qualified_evidence_count": 1,
                "citation_ready_count": 0,
            },
            "retrieval": {
                "debug": {
                    "adequacy": {
                        "status": "adequate",
                        "axes": {
                            "semantic": {"status": "adequate"},
                            "provenance": {"status": "verified"},
                        },
                    }
                }
            },
            "evidence": [
                {
                    "locator": {"text_type": text_type},
                    "provenance": {"evidence_eligible": True},
                }
            ],
        }

    def test_unresolved_semantic_refinement_closes_gate(self):
        package = self._package(
            advice={
                "decision": "refinement_recommended",
                "reason_codes": [
                    "unmapped_lexical_concept_needs_semantic_refinement"
                ],
            }
        )
        gate = evaluate_package_gate(package)
        self.assertFalse(gate["synthesis_allowed"])
        self.assertIn("query_refinement_or_research_plan_required", gate["errors"])
        self.assertEqual(gate["required_next_action"], "refine_plan")

    def test_author_argument_requires_qualified_text(self):
        gate = evaluate_package_gate(self._package(text_type="APPARAT"))
        self.assertFalse(gate["synthesis_allowed"])
        self.assertIn("author_text_evidence_missing", gate["errors"])

    def test_qualified_text_advances_to_expansion(self):
        gate = evaluate_package_gate(self._package())
        self.assertTrue(gate["synthesis_allowed"])
        self.assertFalse(gate["completion_allowed"])
        self.assertEqual(gate["required_next_action"], "expand_selected_evidence")

    def test_research_run_author_branch_rejects_apparat_only(self):
        run = {
            "status": {"overall": "adequate", "missing_requirements": []},
            "research_plan": {
                "subquestions": [
                    {"query_intent": "author_argument", "required_corpus": ["mega"]}
                ]
            },
            "evidence": [
                {
                    "locator": {"text_type": "APPARAT"},
                    "provenance": {"evidence_eligible": True},
                }
            ],
        }
        gate = evaluate_research_run_gate(run)
        self.assertFalse(gate["synthesis_allowed"])
        self.assertIn("author_text_evidence_missing", gate["errors"])


    def test_each_author_branch_requires_its_own_text_evidence(self):
        run = {
            "status": {"overall": "adequate", "missing_requirements": []},
            "research_plan": {
                "subquestions": [
                    {
                        "id": "Q01",
                        "query_intent": "author_argument",
                        "required_corpus": ["mega"],
                    },
                    {
                        "id": "Q02",
                        "query_intent": "author_argument",
                        "required_corpus": ["mega"],
                    },
                ]
            },
            "claim_evidence_matrix": [
                {"subquestion_id": "Q01", "evidence_ids": ["E001"]},
                {"subquestion_id": "Q02", "evidence_ids": ["E002"]},
            ],
            "evidence": [
                {
                    "evidence_id": "E001",
                    "locator": {"text_type": "TEXT"},
                    "provenance": {
                        "evidence_eligible": True,
                        "claim_eligible": True,
                    },
                },
                {
                    "evidence_id": "E002",
                    "locator": {"text_type": "APPARAT"},
                    "provenance": {
                        "evidence_eligible": True,
                        "claim_eligible": False,
                    },
                },
            ],
        }
        gate = evaluate_research_run_gate(run)
        self.assertFalse(gate["synthesis_allowed"])
        self.assertIn("author_text_evidence_missing:Q02", gate["errors"])

    def test_research_run_warns_when_all_claim_evidence_is_provisional(self):
        run = {
            "status": {"overall": "partial", "missing_requirements": []},
            "research_plan": {
                "subquestions": [
                    {
                        "id": "Q01",
                        "query_intent": "author_argument",
                        "required_corpus": ["mega"],
                    }
                ]
            },
            "claim_evidence_matrix": [
                {"subquestion_id": "Q01", "evidence_ids": ["E001"]}
            ],
            "evidence": [
                {
                    "evidence_id": "E001",
                    "locator": {"text_type": "TEXT"},
                    "provenance": {
                        "evidence_eligible": True,
                        "claim_eligible": True,
                        "provenance_ready": False,
                    },
                }
            ],
        }
        gate = evaluate_research_run_gate(run)
        self.assertTrue(gate["synthesis_allowed"])
        self.assertIn(
            "provisional_source_layer_requires_disclosure",
            gate["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
