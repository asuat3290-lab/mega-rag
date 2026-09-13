#!/usr/bin/env python3
"""Isolated tests for register navigation and exact-term probing."""

import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from sachregister import build_sachregister_index, search_sachregister
from term_probe import probe_terms


class AuxiliaryRetrievalTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.gettempdir())
        marker = uuid.uuid4().hex
        self.source = root / f"mega_aux_{marker}_metadata.db"
        self.register = root / f"mega_aux_{marker}_sachregister.db"
        conn = sqlite3.connect(self.source)
        conn.executescript(
            """
            CREATE TABLE chunks(
                id TEXT PRIMARY KEY, mega_abteilung TEXT, band TEXT,
                source_type TEXT, page INTEGER, source_file TEXT,
                page_label TEXT, source_collection TEXT, content_hash TEXT,
                text_layer TEXT, chunk_text TEXT
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                chunk_text, source_file, content='chunks', content_rowid='rowid'
            );
            """
        )
        rows = [
            ("toc", "II", "10", "APPARAT", 10, "II_10_APPARAT", "",
             "ocr", "toc-hash", "editorial_material",
             "Inhalt Literaturregister 700 Namenregister 730 Sachregister 760"),
            ("reg", "II", "10", "APPARAT", 760, "II_10_APPARAT", "",
             "ocr", "reg-hash", "editorial_material",
             "Sachregister\nConcentration des Kapitals 635 636\nCentralisation 640-642"),
            ("text", "II", "10", "TEXT", 635, "II_10_TEXT", "635",
             "megadigital", "text-hash", "author_text",
             "Die Concentration des Kapitals entwickelt sich mit der Akkumulation."),
        ]
        conn.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
        conn.commit()
        conn.close()

    def tearDown(self):
        for base in (self.source, self.register):
            for suffix in ("", "-wal", "-shm", ".tmp"):
                path = Path(str(base) + suffix)
                if path.exists():
                    path.unlink()

    def test_register_is_navigation_only_and_toc_is_excluded(self):
        status = build_sachregister_index(self.source, self.register)
        self.assertEqual(status["pages"], 1)
        result = search_sachregister(
            ["Concentration"], top_k=5, db_path=self.register
        )
        self.assertEqual(len(result["hits"]), 1)
        self.assertFalse(result["hits"][0]["evidence_eligible"])
        self.assertIn("635", result["hits"][0]["reference_pages"])

    def test_term_probe_separates_text_and_apparat(self):
        result = probe_terms(["Concentration"], db_path=self.source)
        item = result["terms"][0]
        self.assertEqual(item["total_pages"], 2)
        self.assertEqual(item["text_pages"], 1)
        self.assertEqual(item["apparat_pages"], 1)

    def test_term_probe_reports_union_separately_from_per_term_sum(self):
        result = probe_terms(
            ["Concentration", "Akkumulation"], db_path=self.source
        )
        summary = result["summary"]
        self.assertEqual(summary["summed_term_page_hits"], 3)
        self.assertEqual(summary["unique_page_hits"], 2)
        self.assertEqual(summary["total_page_hits"], 2)
        self.assertEqual(summary["overlap_page_hits"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
