#!/usr/bin/env python3
"""Regression tests for advisory agent protocol tracing."""

import os
import tempfile
import unittest
from unittest.mock import patch

from protocol_trace import build_protocol_report, record_protocol_event


class ProtocolTraceTests(unittest.TestCase):
    def test_trace_report_surfaces_soft_process_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "trace.db")
            with patch.dict(
                os.environ, {"MEGA_PROTOCOL_TRACE_DB": database}, clear=False
            ):
                for index in range(4):
                    record_protocol_event(
                        "session-one",
                        "search",
                        query=f"flat query {index}",
                        payload={
                            "planning_advice": {
                                "reason_codes": [
                                    "flat_multi_term_query_needs_focus",
                                    "unmapped_lexical_concept_needs_semantic_refinement",
                                ]
                            },
                            "focus_explicit": False,
                            "register_consulted": True,
                            "register_hits": 2,
                            "refinement_used": False,
                        },
                    )
                report = build_protocol_report("session-one")

        self.assertEqual(report["searches_run"], 4)
        self.assertFalse(report["research_run_used"])
        self.assertTrue(report["sachregister_consulted"])
        self.assertIn(
            "multiple_sequential_searches_without_research_run",
            report["warnings"],
        )
        self.assertIn(
            "flat_multi_term_search_without_explicit_focus",
            report["warnings"],
        )
        self.assertIn(
            "unmapped_lexical_concept_not_refined",
            report["warnings"],
        )

    def test_explicit_focus_hit_counts_are_aggregated(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "trace.db")
            with patch.dict(
                os.environ, {"MEGA_PROTOCOL_TRACE_DB": database}, clear=False
            ):
                record_protocol_event(
                    "session-two",
                    "search",
                    query="Stoffwechsel",
                    payload={
                        "focus_explicit": True,
                        "refinement_used": True,
                        "register_consulted": True,
                        "focus_diagnostics": {
                            "per_term": [{
                                "term": "Stoffwechsel",
                                "candidate_hits": 12,
                                "final_hits": 4,
                                "qualified_final_hits": 4,
                            }]
                        },
                    },
                )
                record_protocol_event(
                    "session-two",
                    "evidence",
                    payload={"evidence_expanded": 2},
                )
                report = build_protocol_report("session-two")

        self.assertEqual(report["evidence_expanded"], 2)
        self.assertEqual(
            report["core_term_hit_rate"]["Stoffwechsel"]["qualified_final_hits"],
            4,
        )
        self.assertEqual(report["advisory_status"], "clean")


if __name__ == "__main__":
    unittest.main(verbosity=2)
