#!/usr/bin/env python3
"""Regression tests for human-governed planning memory."""

import tempfile
import unittest
import uuid
from pathlib import Path

from planning_memory import (
    find_promoted,
    mark_corpus_validation,
    memory_status,
    record_candidate,
    review_memory,
)


class PlanningMemoryTests(unittest.TestCase):
    def setUp(self):
        self.db = Path(tempfile.gettempdir()) / f"mega_memory_{uuid.uuid4().hex}.db"

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db) + suffix)
            if path.exists():
                path.unlink()

    def test_proposal_is_not_reused_until_validated_and_promoted(self):
        payload = {"core_terms": ["Subsumtion"]}
        memory_id = record_candidate(
            "query_plan",
            "Subsumtion",
            payload,
            planner_version="planner-v1",
            db_path=self.db,
        )
        self.assertIsNone(
            find_promoted(
                "query_plan",
                "subsumtion",
                planner_version="planner-v1",
                db_path=self.db,
            )
        )
        with self.assertRaises(ValueError):
            review_memory(
                memory_id,
                "promote",
                note="premature",
                db_path=self.db,
            )
        mark_corpus_validation(
            memory_id,
            {"passed": True, "text_hits": 3},
            passed=True,
            db_path=self.db,
        )
        review_memory(
            memory_id,
            "promote",
            note="reviewed exact MEGA text hits",
            db_path=self.db,
        )
        reused = find_promoted(
            "query_plan",
            "SUBSUMTION",
            planner_version="planner-v1",
            db_path=self.db,
        )
        self.assertEqual(reused["payload"], payload)
        self.assertEqual(reused["status"], "promoted")
        self.assertEqual(memory_status(db_path=self.db)["reusable"], 1)

    def test_rejected_proposal_is_never_reused(self):
        memory_id = record_candidate(
            "research_plan",
            "test question",
            {"subquestions": [{"question": "bad mapping"}]},
            planner_version="planner-v1",
            db_path=self.db,
        )
        review_memory(
            memory_id,
            "reject",
            note="semantic mapping was not equivalent",
            db_path=self.db,
        )
        self.assertIsNone(
            find_promoted(
                "research_plan",
                "test question",
                planner_version="planner-v1",
                db_path=self.db,
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
