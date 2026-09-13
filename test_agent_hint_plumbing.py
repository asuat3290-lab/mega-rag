#!/usr/bin/env python3
"""Service-level regression for lightweight agent query hints."""

import unittest
from unittest.mock import patch

from agent_service import search_agent


def _package() -> dict:
    return {
        "index_version": "test-index",
        "query": {
            "expanded_query": "Stoffwechsel",
            "query_profile": {
                "intent": "author_argument",
                "target_abteilung": "II",
                "target_band": "1",
            },
            "priority_terms": ["Stoffwechsel"],
            "query_plan": {
                "mode": "agent_supplied",
                "planner_version": "test-plan",
                "focus_explicit": True,
            },
            "planner_diagnostics": {"usage": {"total_tokens": 0}},
            "planning_advice": {"decision": "local", "reason_codes": []},
        },
        "retrieval": {
            "debug": {
                "adequacy": {"status": "partial"},
                "register_navigation_hits": 1,
                "focus_diagnostics": {
                    "explicit": True,
                    "qualified_final_hits": 1,
                    "per_term": [{
                        "term": "Stoffwechsel",
                        "candidate_hits": 3,
                        "final_hits": 1,
                        "qualified_final_hits": 1,
                    }],
                    "warnings": [],
                },
            }
        },
        "artifact_type": "research_evidence_package",
        "synthesis_gate": {"synthesis_allowed": True},
        "summary": {
            "qualified_evidence_count": 1,
            "citation_ready_count": 1,
        },
        "evidence": [],
        "usage_constraints": [],
    }


class AgentHintPlumbingTests(unittest.TestCase):
    @patch("agent_service.build_research_package", return_value=_package())
    @patch("agent_service.retrieve_research_evidence", return_value={"results": []})
    def test_search_hints_compile_into_existing_refinement(
        self, retrieve, _build
    ):
        response = search_agent(
            "Stoffwechsel Natur Arbeit",
            focus_terms=["Stoffwechsel"],
            context_terms=["Natur", "Arbeit"],
            target_volumes=["II/1"],
            intent="author_argument",
        )
        kwargs = retrieve.call_args.kwargs
        self.assertEqual(kwargs["planner_mode"], "agent_supplied")
        self.assertEqual(
            kwargs["plan_refinement"]["focus_terms"], ["Stoffwechsel"]
        )
        self.assertEqual(
            kwargs["plan_refinement"]["context_terms"], ["Natur", "Arbeit"]
        )
        self.assertEqual(
            kwargs["plan_refinement"]["target_volumes"], ["II/1"]
        )
        self.assertEqual(
            response["focus_diagnostics"]["qualified_final_hits"], 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
