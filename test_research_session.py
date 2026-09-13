#!/usr/bin/env python3
"""State-machine regression tests for formal Agent research sessions."""

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agent_service import (
    research_session_continue_agent,
    research_session_expand_agent,
    research_session_finalize_agent,
    research_session_start_agent,
    research_session_status_agent,
)
from research_session import (
    COMPLETE,
    EVIDENCE_EXPANDED,
    EVIDENCE_QUALIFIED,
    NEEDS_REFINEMENT,
    PLANNED,
)


@contextmanager
def writable_test_directory():
    root = Path(__file__).resolve().parent / f".test_session_{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ResearchSessionTests(unittest.TestCase):
    def _plan_response(self, *, needs_refinement=False):
        advice = {
            "decision": "refinement_required" if needs_refinement else "local",
            "should_refine": needs_refinement,
            "reason_codes": ["missing_discriminating_core"] if needs_refinement else [],
        }
        plan = {
            "question_type": "textual_research",
            "subquestions": [
                {
                    "id": "Q01",
                    "question": "Test question",
                    "type": "textual_reconstruction",
                    "query_intent": "author_argument",
                    "required_corpus": ["mega"],
                    "required_evidence": "author_text",
                    "covered_concepts": ["Testbegriff"],
                    "external_concepts": [],
                    "query_refinement": None,
                }
            ],
            "unmapped_concepts": ["unknown"] if needs_refinement else [],
            "external_concepts": [],
            "coverage_status": "incomplete" if needs_refinement else "complete",
        }
        return {
            "ok": True,
            "operation": "research_plan",
            "research_plan": plan,
            "planning_advice": advice,
            "planner_diagnostics": {"effective_mode": "local"},
            "usage": {"api_tokens": 0},
        }

    def _evidence(self, evidence_id="E001", branch_ids=None):
        return {
            "evidence_id": evidence_id,
            "evidence_uid": f"ev_{evidence_id.lower()}",
            "package_evidence_ref": f"pkg:{evidence_id}",
            "source": {"collection": "megadigital"},
            "locator": {"text_type": "TEXT", "locator_verified": True},
            "provenance": {
                "evidence_eligible": True,
                "semantic_ready": True,
                "scope_ready": True,
                "provenance_ready": True,
                "claim_eligible": True,
                "claim_ready": True,
                "verified_author_text": True,
            },
            "evidence": {
                "german_context": "Testbegriff im deutschen Original.",
                "quote_eligible": False,
            },
            "research": {"branch_ids": list(branch_ids or [])},
        }

    def _source_artifact(
        self, directory: Path, evidence=None, claim_matrix=None
    ) -> Path:
        path = directory / "research_run_fixture.json"
        path.write_text(
            json.dumps(
                {
                    "synthesis_gate": {"synthesis_allowed": True},
                    "evidence": evidence or [self._evidence()],
                    "claim_evidence_matrix": claim_matrix or [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path
    def _run_response(self, artifact: Path, evidence=None):
        compact = []
        for item in evidence or [self._evidence()]:
            compact.append(
                {
                    "evidence_id": item["evidence_id"],
                    "evidence_uid": item["evidence_uid"],
                    "package_evidence_ref": item["package_evidence_ref"],
                    "evidence_eligible": True,
                    "claim_eligible": True,
                    "claim_ready": True,
                    "branch_ids": list(item.get("research", {}).get("branch_ids", [])),
                    "verified_author_text": True,
                    "text_type": "TEXT",
                }
            )
        return {
            "ok": True,
            "operation": "research_run",
            "run_id": "run_fixture",
            "research_plan": self._plan_response()["research_plan"],
            "planning_advice": self._plan_response()["planning_advice"],
            "status": {"overall": "adequate"},
            "synthesis_gate": {"synthesis_allowed": True, "errors": [], "warnings": []},
            "evidence": compact,
            "artifact_paths": {"json": str(artifact)},
            "usage": {"api_tokens": 0, "planner": {"effective_mode": "local"}},
        }

    def test_complete_flow_issues_receipt(self):
        with writable_test_directory() as temporary:
            root = Path(temporary)
            db = root / "sessions.db"
            source = self._source_artifact(root)
            with patch("agent_service.research_plan_agent", return_value=self._plan_response()), patch(
                "agent_service.get_current_version", return_value="idx-test"
            ):
                started = research_session_start_agent("Test question", db_path=db)
            session_id = started["session"]["session_id"]
            self.assertEqual(started["session"]["state"], PLANNED)
            with patch(
                "agent_service.research_run_agent",
                return_value=self._run_response(source),
            ):
                retrieved = research_session_continue_agent(session_id, db_path=db)
            self.assertEqual(retrieved["session"]["state"], EVIDENCE_QUALIFIED)
            expanded = research_session_expand_agent(
                session_id, ["E001"], db_path=db
            )
            self.assertEqual(expanded["session"]["state"], EVIDENCE_EXPANDED)
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "C1",
                                "claim_type": "text_supported",
                                "evidence_refs": ["E001"],
                                "quotes": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            finalized = research_session_finalize_agent(
                session_id, report, db_path=db
            )
            self.assertTrue(finalized["completion_allowed"])
            self.assertEqual(finalized["session"]["state"], COMPLETE)
            self.assertTrue(Path(finalized["completion_receipt"]["receipt_path"]).exists())
            status = research_session_status_agent(session_id, db_path=db)
            self.assertGreaterEqual(len(status["events"]), 6)

    def test_refinement_state_blocks_unrefined_local_retrieval(self):
        with writable_test_directory() as temporary:
            db = Path(temporary) / "sessions.db"
            with patch(
                "agent_service.research_plan_agent",
                return_value=self._plan_response(needs_refinement=True),
            ), patch("agent_service.get_current_version", return_value="idx-test"):
                started = research_session_start_agent("Unknown query", db_path=db)
            session_id = started["session"]["session_id"]
            self.assertEqual(started["session"]["state"], NEEDS_REFINEMENT)
            with patch("agent_service.research_run_agent") as mocked_run:
                blocked = research_session_continue_agent(session_id, db_path=db)
            self.assertTrue(blocked["blocked"])
            mocked_run.assert_not_called()

    def test_report_cannot_use_unexpanded_evidence(self):
        with writable_test_directory() as temporary:
            root = Path(temporary)
            db = root / "sessions.db"
            all_evidence = [self._evidence("E001"), self._evidence("E002")]
            source = self._source_artifact(root, all_evidence)
            with patch("agent_service.research_plan_agent", return_value=self._plan_response()), patch(
                "agent_service.get_current_version", return_value="idx-test"
            ):
                started = research_session_start_agent("Test question", db_path=db)
            session_id = started["session"]["session_id"]
            with patch(
                "agent_service.research_run_agent",
                return_value=self._run_response(source, all_evidence),
            ):
                research_session_continue_agent(session_id, db_path=db)
            research_session_expand_agent(session_id, ["E001"], db_path=db)
            report = root / "bad_report.json"
            report.write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "C1",
                                "claim_type": "text_supported",
                                "evidence_refs": ["E002"],
                                "quotes": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            finalized = research_session_finalize_agent(
                session_id, report, db_path=db
            )
            self.assertFalse(finalized["completion_allowed"])
            self.assertIn(
                "report_uses_unexpanded_evidence:E002",
                finalized["validation"]["errors"],
            )
            self.assertEqual(finalized["session"]["state"], EVIDENCE_EXPANDED)

    def test_finalize_before_expansion_is_rejected(self):
        with writable_test_directory() as temporary:
            root = Path(temporary)
            db = root / "sessions.db"
            with patch("agent_service.research_plan_agent", return_value=self._plan_response()), patch(
                "agent_service.get_current_version", return_value="idx-test"
            ):
                started = research_session_start_agent("Test question", db_path=db)
            report = root / "report.json"
            report.write_text('{"claims": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot finalize"):
                research_session_finalize_agent(
                    started["session"]["session_id"], report, db_path=db
                )


    def test_retrieval_revisions_are_persisted(self):
        with writable_test_directory() as temporary:
            root = Path(temporary)
            db = root / "sessions.db"
            source = self._source_artifact(root)
            with patch("agent_service.research_plan_agent", return_value=self._plan_response()), patch(
                "agent_service.get_current_version", return_value="idx-test"
            ):
                started = research_session_start_agent("Test question", db_path=db)
            session_id = started["session"]["session_id"]
            with patch(
                "agent_service.research_run_agent",
                return_value=self._run_response(source),
            ):
                first = research_session_continue_agent(session_id, db_path=db)
                second = research_session_continue_agent(session_id, db_path=db)
            self.assertEqual(first["session"]["active_revision"], 1)
            self.assertEqual(second["session"]["active_revision"], 2)
            self.assertEqual(len(second["session"]["artifact_revisions"]), 2)
            self.assertEqual(
                second["session"]["artifact_revisions"][1]["parent_revision"], 1
            )

    def test_finalize_requires_expanded_evidence_for_each_branch(self):
        with writable_test_directory() as temporary:
            root = Path(temporary)
            db = root / "sessions.db"
            evidence = [
                self._evidence("E001", ["Q01"]),
                self._evidence("E002", ["Q02"]),
            ]
            matrix = [
                {
                    "subquestion_id": "Q01",
                    "claim_type": "text_supported",
                    "evidence_ids": ["E001"],
                    "status": "adequate",
                },
                {
                    "subquestion_id": "Q02",
                    "claim_type": "theoretical_inference",
                    "evidence_ids": ["E002"],
                    "status": "partial",
                },
            ]
            source = self._source_artifact(root, evidence, matrix)
            with patch("agent_service.research_plan_agent", return_value=self._plan_response()), patch(
                "agent_service.get_current_version", return_value="idx-test"
            ):
                started = research_session_start_agent("Test question", db_path=db)
            session_id = started["session"]["session_id"]
            with patch(
                "agent_service.research_run_agent",
                return_value=self._run_response(source, evidence),
            ):
                research_session_continue_agent(session_id, db_path=db)
            research_session_expand_agent(session_id, ["E001"], db_path=db)
            report = root / "one_branch_report.json"
            report.write_text(
                json.dumps(
                    {
                        "claims": [
                            {
                                "claim_id": "C1",
                                "claim_type": "text_supported",
                                "evidence_refs": ["E001"],
                                "quotes": [],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            finalized = research_session_finalize_agent(
                session_id, report, db_path=db
            )
            self.assertFalse(finalized["completion_allowed"])
            errors = finalized["validation"]["errors"]
            self.assertIn("branch_without_expanded_evidence:Q02", errors)
            self.assertIn("report_omits_required_branch:Q02", errors)

if __name__ == "__main__":
    unittest.main()