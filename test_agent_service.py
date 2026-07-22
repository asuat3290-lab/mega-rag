#!/usr/bin/env python3
"""Regression tests for the stable token-aware agent response contract."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_service import evidence_from_package, search_agent


def _retrieval_payload():
    return {
        "query": {
            "question": "利润率下降",
            "expanded_query": "利润率下降 fallende Profitrate",
            "matched_glossary_terms": ["利润率下降"],
            "glossary_hints": {},
            "query_profile": {"intent": "author_argument"},
            "priority_terms": ["fallende Profitrate", "Profitrate"],
        },
        "retrieval": {"route": "main_text", "debug": {}},
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
                "text_layer": "author_text",
                "source_collection": "megadigital",
                "source_quality": "authoritative_digital",
                "source_title": "Test",
                "source_doc": "test",
                "ocr_quality": "high",
                "_retrieval_sources": ["unit_test"],
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
        evidence = response["evidence"][0]
        self.assertTrue(evidence["verified_author_text"])
        self.assertNotIn("german_context", evidence)
        self.assertIn("fallende Profitrate", evidence["preview"])

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

    def test_missing_evidence_id_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text(json.dumps({"evidence": []}), encoding="utf-8")
            response = evidence_from_package(path, ["E999"], detail="index")
            self.assertEqual(response["missing_ids"], ["e999"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
