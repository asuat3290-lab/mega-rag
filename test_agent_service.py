#!/usr/bin/env python3
"""Regression tests for the stable token-aware agent response contract."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_service import evidence_from_package, search_agent
from research_export import serialize_evidence


def _retrieval_payload():
    return {
        "query": {
            "question": "利润率下降",
            "expanded_query": "利润率下降 fallende Profitrate",
            "matched_glossary_terms": ["利润率下降"],
            "glossary_hints": {},
            "query_profile": {"intent": "author_argument"},
            "priority_terms": ["fallende Profitrate", "Profitrate"],
            "query_plan": {
                "plan_status": {"valid_for_evidence": True, "issues": []},
                "qualification_groups": [
                    {"id": "profit", "alternatives": ["fallende Profitrate"]}
                ],
            },
        },
        "retrieval": {
            "route": "main_text",
            "debug": {
                "adequacy": {
                    "status": "adequate",
                    "axes": {
                        "semantic": {"status": "adequate", "candidate_count": 1},
                        "provenance": {"status": "verified", "eligible_count": 1},
                        "citation": {"status": "ready", "ready_count": 1},
                    },
                }
            },
        },
        "results": [
            {
                "id": "test:1",
                "page_id": "test:1",
                "record_type": "page",
                "abteilung": "II",
                "band": "4.2",
                "type": "TEXT",
                "page": 221,
                "page_label": "221",
                "text": "Eine fallende Profitrate ist hier der Gegenstand der Untersuchung.",
                "display_snippet": "Eine fallende Profitrate ist hier der Gegenstand der Untersuchung.",
                "display_preview": "Eine fallende Profitrate ist hier der Gegenstand der Untersuchung.",
                "matched_term": "fallende Profitrate",
        "context_boundary_complete": True,
                "text_layer": "author_text",
                "source_collection": "megadigital",
                "source_quality": "authoritative_digital",
                "source_title": "Test",
                "source_doc": "test",
                "ocr_quality": "high",
                "_retrieval_sources": ["unit_test"],
                "evidence_eligible": True,
                "_qualification": {"candidate_class": "direct_author_text"},
            }
        ],
    }


class AgentServiceTests(unittest.TestCase):
    @patch("agent_service.retrieve_research_evidence", return_value=_retrieval_payload())
    def test_search_index_is_compact_and_source_aware(self, _mock_retrieve):
        response = search_agent("利润率下降", detail="index")
        self.assertTrue(response["ok"])
        self.assertEqual(response["operation"], "search")
        self.assertEqual(response["usage"]["api_tokens"], 0)
        self.assertGreater(response["usage"]["rough_returned_evidence_tokens"], 0)
        self.assertTrue(response["synthesis_gate"]["synthesis_allowed"])
        evidence = response["evidence"][0]
        self.assertTrue(evidence["evidence_uid"].startswith("ev_"))
        self.assertTrue(evidence["verified_author_text"])
        self.assertNotIn("german_context", evidence)
        self.assertIn("fallende Profitrate", evidence["preview"])
        self.assertTrue(evidence["preview_only"])
        self.assertFalse(evidence["quote_eligible"])
        self.assertTrue(evidence["source_quote_eligible"])
        self.assertEqual(evidence["authorship_status"], "author_text_layer")
        self.assertTrue(any("selection only" in value for value in evidence["warnings"]))

    def test_saved_package_supports_selective_expansion(self):
        payload = _retrieval_payload()
        with patch("agent_service.retrieve_research_evidence", return_value=payload):
            with tempfile.TemporaryDirectory() as directory:
                response = search_agent(
                    "利润率下降", detail="index", save=True, output_dir=directory
                )
                package_path = response["artifact_paths"]["json"]
                expanded = evidence_from_package(package_path, ["E001"], detail="full")
                self.assertEqual(len(expanded["evidence"]), 1)
                self.assertIn(
                    "fallende Profitrate",
                    expanded["evidence"][0]["evidence"]["german_context"],
                )
                full_item = expanded["evidence"][0]
                self.assertTrue(full_item["evidence"]["quote_eligible"])
                self.assertEqual(full_item["provenance"]["edition_status"], "critical_edition_text")

    def test_edition_status_distinguishes_manuscript_and_print_text(self):
        record = dict(_retrieval_payload()["results"][0])
        record["source_title"] = "Das Kapital, Druckfassung 1894"
        printed = serialize_evidence(record, 1, ["Profitrate"])
        self.assertEqual(
            printed["provenance"]["edition_status"], "edited_print_edition"
        )

        record["source_title"] = "Oekonomisches Manuskript 1863-1865"
        manuscript = serialize_evidence(record, 1, ["Profitrate"])
        self.assertEqual(
            manuscript["provenance"]["edition_status"], "manuscript_or_draft_edition"
        )

    def test_editorial_material_is_never_quote_eligible(self):
        record = dict(_retrieval_payload()["results"][0])
        record["type"] = "APPARAT"
        record["text_layer"] = "apparatus"
        editorial = serialize_evidence(record, 1, ["Profitrate"])
        self.assertEqual(
            editorial["provenance"]["authorship_status"], "editorial_apparatus"
        )
        self.assertFalse(editorial["evidence"]["quote_eligible"])

    def test_missing_evidence_id_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text(json.dumps({"evidence": []}), encoding="utf-8")
            response = evidence_from_package(path, ["E999"], detail="index")
            self.assertEqual(response["missing_ids"], ["e999"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
