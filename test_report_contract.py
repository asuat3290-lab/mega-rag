#!/usr/bin/env python3
"""Regression tests for the fail-closed agent report contract."""

import tempfile
import unittest
from pathlib import Path

from report_contract import load_json_artifact, validate_report_claims


def evidence(local_id="E001", uid="ev_one", *, eligible=True, quote=True):
    return {
        "evidence_id": local_id,
        "evidence_uid": uid,
        "package_evidence_ref": f"package:{local_id}",
        "provenance": {"evidence_eligible": eligible},
        "evidence": {
            "quote_eligible": quote,
            "german_context": "Der genaue deutsche Satz steht hier.",
        },
    }


class ReportContractTests(unittest.TestCase):
    def test_textual_claim_requires_known_qualified_evidence(self):
        source = {"evidence": [evidence()]}
        report = {"claims": [{
            "claim_id": "C1",
            "claim_type": "text_supported",
            "text": "Eine textnahe Aussage.",
            "evidence_refs": ["ev_one"],
        }]}
        result = validate_report_claims(report, source)
        self.assertTrue(result["valid"])

        report["claims"][0]["evidence_refs"] = ["ev_missing"]
        result = validate_report_claims(report, source)
        self.assertFalse(result["valid"])
        self.assertTrue(any(value.startswith("unknown_evidence_ref") for value in result["errors"]))

    def test_windows_utf8_bom_json_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_bytes(b"\xef\xbb\xbf" + b'{"claims": []}')
            self.assertEqual(load_json_artifact(path), {"claims": []})

    def test_closed_source_gate_blocks_report_validation(self):
        source = {
            "synthesis_gate": {"synthesis_allowed": False},
            "evidence": [evidence()],
        }
        report = {"claims": [{
            "claim_id": "C1", "claim_type": "text_supported",
            "text": "Aussage", "evidence_refs": ["ev_one"],
        }]}
        result = validate_report_claims(report, source)
        self.assertFalse(result["valid"])
        self.assertIn("source_synthesis_gate_closed", result["errors"])

    def test_textual_claim_cannot_mix_qualified_and_unqualified_refs(self):
        source = {"evidence": [
            evidence(local_id="E001", uid="ev_one", eligible=True),
            evidence(local_id="E002", uid="ev_two", eligible=False),
        ]}
        report = {"claims": [{
            "claim_id": "C1", "claim_type": "text_supported",
            "text": "Aussage", "evidence_refs": ["ev_one", "ev_two"],
        }]}
        result = validate_report_claims(report, source)
        self.assertFalse(result["valid"])
        self.assertIn(
            "textual_claim_uses_unqualified_evidence:C1", result["errors"]
        )

    def test_same_local_id_cannot_refer_to_two_sources(self):
        source = {"evidence": [evidence(uid="ev_one"), evidence(uid="ev_two")]}
        report = {"claims": [{
            "claim_id": "C1", "claim_type": "theoretical_inference",
            "text": "Interpretation", "evidence_refs": ["ev_one"],
        }]}
        result = validate_report_claims(report, source)
        self.assertFalse(result["valid"])
        self.assertIn("duplicate_local_evidence_id:E001", result["errors"])

    def test_quote_must_be_verbatim_and_quote_eligible(self):
        source = {"evidence": [evidence(quote=False)]}
        report = {"claims": [{
            "claim_id": "C1", "claim_type": "text_supported",
            "text": "Aussage", "evidence_refs": ["ev_one"],
            "quotes": [{
                "evidence_ref": "ev_one",
                "text": "Der genaue deutsche Satz steht hier.",
            }],
        }]}
        result = validate_report_claims(report, source)
        self.assertFalse(result["valid"])
        self.assertIn("quote_not_eligible:C1:ev_one", result["errors"])

    def test_contemporary_hypothesis_without_external_source_is_labeled(self):
        source = {"evidence": [evidence()]}
        report = {"claims": [{
            "claim_id": "C1", "claim_type": "empirical_hypothesis",
            "text": "Eine gegenwärtige empirische Hypothese.",
            "evidence_refs": ["ev_one"],
        }]}
        result = validate_report_claims(report, source)
        self.assertTrue(result["valid"])
        self.assertIn("empirical_hypothesis_unverified:C1", result["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
