#!/usr/bin/env python3
"""Regression tests for evidence calibration in the claim-audit pipeline."""
from __future__ import annotations

import unittest

from claim_audit import audit_claim


def _record(text_layer="author_text", text_type="TEXT"):
    return {
        "id": f"fake:{text_layer}",
        "page_id": f"fake:{text_layer}",
        "record_type": "page",
        "abteilung": "II",
        "band": "4.2",
        "type": text_type,
        "page": 221,
        "page_label": "221",
        "text": "Die fallende Profitrate drückt eine fallende Rate des Mehrwerths aus.",
        "display_snippet": "Die fallende Profitrate drückt eine fallende Rate des Mehrwerths aus.",
        "display_preview": "Die fallende Profitrate drückt eine fallende Rate des Mehrwerths aus.",
        "matched_term": "fallende Profitrate",
        "text_layer": text_layer,
        "text_layer_confidence": 1.0,
        "text_layer_provenance": "unit_test",
        "source_collection": "megadigital",
        "source_quality": "authoritative_digital",
        "source_title": "Test source",
        "source_doc": "test",
        "source_part": "TEXT",
        "ocr_quality": "high",
        "final_score": 1.0,
        "rrf_score": 0.1,
        "_retrieval_sources": ["unit_test"],
    }


def _retriever_for(record):
    def retrieve(question, **_kwargs):
        return {
            "query": {"priority_terms": ["fallende Profitrate", "Profitrate"]},
            "retrieval": {"debug": {"source": "unit_test"}},
            "results": [record],
        }

    return retrieve


def _planner(_idea, _budget):
    return {
        "claims": [
            {
                "text": "马克思把利润率下降理解为资本主义生产的内在趋势",
                "claim_type": "interpretive",
                "scope": {"author": "Marx"},
                "queries": {"support": ["fallende Profitrate"]},
            }
        ]
    }


def _strong_classifier(payload, _budget):
    evidence_id = payload["evidence"][0]["evidence_id"]
    return {
        "claims": [
            {
                "claim_id": "C001",
                "verdict": "strong_support",
                "confidence": 0.92,
                "supported_part": "利润率下降",
                "problematic_part": "",
                "revised_claim": "马克思讨论了利润率下降。",
                "additional_directions": [],
                "assessments": [
                    {
                        "evidence_id": evidence_id,
                        "relation": "supports",
                        "strength": "direct",
                        "rationale": "正文直接出现该表述。",
                    }
                ],
            }
        ],
        "overall": {
            "verdict": "strong_support",
            "confidence": 0.92,
            "summary": "有直接正文证据。",
            "revised_idea": "",
            "additional_directions": [],
        },
    }


class ClaimAuditCalibrationTests(unittest.TestCase):
    def run_audit(self, record, *, use_flash=True):
        return audit_claim(
            "马克思把利润率下降理解为资本主义生产的内在趋势",
            budget="brief",
            use_flash=use_flash,
            use_pro=False,
            use_cache=False,
            retriever=_retriever_for(record),
            planner=_planner if use_flash else None,
            classifier=_strong_classifier if use_flash else None,
        )

    def test_verified_author_text_can_support_strongly(self):
        audit = self.run_audit(_record("author_text", "TEXT"))
        self.assertEqual(audit["overall"]["verdict"], "strong_support")
        self.assertTrue(audit["evidence"][0]["provenance"]["verified_author_text"])

    def test_indirect_author_text_cannot_produce_strong_support(self):
        def indirect_classifier(payload, budget):
            raw = _strong_classifier(payload, budget)
            raw["claims"][0]["assessments"][0]["strength"] = "indirect"
            return raw

        audit = audit_claim(
            "观点", budget="brief", use_cache=False, retriever=_retriever_for(_record()),
            planner=_planner, classifier=indirect_classifier,
        )
        self.assertEqual(audit["overall"]["verdict"], "partial_support")

    def test_editorial_material_cannot_impersonate_author_text(self):
        audit = self.run_audit(_record("apparatus", "APPARAT"))
        assessment = audit["claims"][0]["assessments"][0]
        self.assertEqual(assessment["relation"], "context")
        self.assertEqual(audit["overall"]["verdict"], "insufficient")

    def test_unclassified_textband_downgrades_direct_strength(self):
        audit = self.run_audit(_record("textband_unclassified", "TEXT"))
        assessment = audit["claims"][0]["assessments"][0]
        self.assertEqual(assessment["strength"], "unverified")
        self.assertNotEqual(audit["overall"]["verdict"], "strong_support")

    def test_local_only_mode_returns_candidates_without_semantic_verdict(self):
        audit = self.run_audit(_record("author_text", "TEXT"), use_flash=False)
        self.assertEqual(audit["mode"], "local_candidates_only")
        self.assertEqual(audit["overall"]["verdict"], "insufficient")
        self.assertEqual(audit["summary"]["evidence_count"], 1)
        self.assertEqual(audit["usage"]["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
