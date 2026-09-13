#!/usr/bin/env python3
"""Focused synthetic regressions for philological coverage and term probes."""

from __future__ import annotations

import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from source_catalog import (
    build_source_catalog,
    catalog_status,
    coverage_report,
    list_source_documents,
)
from term_probe import probe_terms


CHUNKS_SCHEMA = """
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    source_path TEXT,
    source_file TEXT,
    mega_abteilung TEXT,
    band TEXT,
    source_type TEXT,
    source_collection TEXT,
    source_quality TEXT,
    source_doc TEXT,
    source_part TEXT,
    source_title TEXT,
    source_url TEXT,
    language TEXT,
    page INTEGER,
    page_label TEXT,
    passage_no TEXT,
    text_layer TEXT,
    chunk_text TEXT
);
"""


class PhilologyCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(
            os.environ.get(
                "MEGARAG_TEST_ROOT",
                str(Path(__file__).resolve().parent / ".philology-test-runtime"),
            )
        )
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"case-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.db_path = self.root / "metadata.db"
        self.manifest_path = self.root / "source_catalog.yaml"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(CHUNKS_SCHEMA)
        rows = [
            (
                "de-1867-1", "md/1867", "all_chunks.jsonl", "II", "5", "TEXT",
                "megadigital", "authoritative_digital", "MEGA_A2_B005-00_ETX.xml", "0",
                "Das Kapital, Druckfassung 1867", "", "de", 1, "p. 1", "1",
                "author_text", "Allgemeine Arbeit erscheint hier im Text.",
            ),
            (
                "de-1867-2", "md/1867", "all_chunks.jsonl", "II", "5", "TEXT",
                "megadigital", "authoritative_digital", "MEGA_A2_B005-00_ETX.xml", "0",
                "Das Kapital, Druckfassung 1867", "", "de", 2, "p. 2", "2",
                "author_text", "Productiv-\nkräfte werden historisch bestimmt.",
            ),
            (
                "de-1890-1", "md/1890", "all_chunks.jsonl", "II", "5", "TEXT",
                "megadigital", "authoritative_digital", "MEGA_A2_B010-00_ETX.xml", "0",
                "Das Kapital, Druckfassung 1890", "", "de", 3, "p. 3", "3",
                "author_text", "Allgemeine Arbeit erscheint in einer zweiten Version.",
            ),
            (
                "de-1890-2", "md/1890", "all_chunks.jsonl", "II", "5", "TEXT",
                "megadigital", "authoritative_digital", "MEGA_A2_B010-00_ETX.xml", "0",
                "Das Kapital, Druckfassung 1890", "", "de", 4, "p. 4", "4",
                "author_text", "Arbeitsprozess und Arbeit werden unterschieden.",
            ),
            (
                "apparat-1", "ocr/apparat", "apparat.txt", "II", "5", "APPARAT",
                "ocr", "ocr", "", "", "", "", None, 5, "p. 5", "5",
                "apparatus", "Allgemeine Arbeit: Lesart der Ausgabe.",
            ),
            (
                "zh-1", "ocr/zh", "zh.txt", "III", "1", "TEXT",
                "ocr", "ocr", "", "", "", "", "zh", 6, "p. 6", "6",
                "author_text", "一般劳动在中文样本中出现。",
            ),
            (
                "unknown-1", "ocr/unknown", "unknown.txt", "III", "1", "APPARAT",
                "ocr", "ocr", "", "", "", "", None, 7, "p. 7", "7",
                "apparatus", "Arbeitsnotiz ohne Sprachmetadatum.",
            ),
            (
                "mixed-1", "ocr/mixed", "mixed.txt", "III", "2", "TEXT",
                "ocr", "ocr", "", "", "", "", "de", 8, "p. 8", "8",
                "author_text", "Gemischter deutscher Datensatz.",
            ),
            (
                "mixed-2", "ocr/mixed", "mixed.txt", "III", "2", "TEXT",
                "ocr", "ocr", "", "", "", "", "zh", 9, "p. 9", "9",
                "author_text", "混合语言数据集。",
            ),
            (
                "mixed-3", "ocr/mixed", "mixed.txt", "III", "2", "TEXT",
                "ocr", "ocr", "", "", "", "", None, 12, "p. 12", "12",
                "author_text", "Zeile ohne Sprachmetadatum.",
            ),
            (
                "split-1", "ocr/split", "split.txt", "III", "2", "TEXT",
                "ocr", "ocr", "", "", "", "", "de", 10, "p. 10", "10",
                "author_text", "Arbeits",
            ),
            (
                "split-2", "ocr/split", "split.txt", "III", "2", "TEXT",
                "ocr", "ocr", "", "", "", "", "de", 11, "p. 11", "11",
                "author_text", "bedingungen",
            ),
        ]
        connection.executemany(
            """
            INSERT INTO chunks (
                id, source_path, source_file, mega_abteilung, band, source_type,
                source_collection, source_quality, source_doc, source_part,
                source_title, source_url, language, page, page_label, passage_no,
                text_layer, chunk_text
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        connection.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_text, content='chunks', content_rowid='rowid')"
        )
        connection.execute(
            "INSERT INTO chunks_fts(rowid, chunk_text) SELECT rowid, chunk_text FROM chunks"
        )
        connection.commit()
        connection.close()
        self.manifest_path.write_text(
            """
schema_version: mega-source-catalog-manifest-v1
version_groups:
  - id: versions:kapital
    group_type: work_versions
    canonical_title: Das Kapital
    members:
      - selector:
          source_doc: MEGA_A2_B005-00_ETX.xml
        role: edition_1867
        sequence: 1
      - selector:
          source_doc: MEGA_A2_B010-00_ETX.xml
        role: edition_1890
        sequence: 2
relations:
  - subject:
      source_doc: MEGA_A2_B005-00_ETX.xml
    predicate: earlier_print_edition_of_same_work
    object:
      source_doc: MEGA_A2_B010-00_ETX.xml
    scope: textual_history
    confidence: 1.0
""".strip(),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_coverage_preserves_unknown_mixed_and_conservative_counts(self) -> None:
        build_source_catalog(self.db_path, self.manifest_path)
        status = catalog_status(self.db_path)
        self.assertFalse(status["build_required"])
        self.assertTrue(status["language_coverage"]["no_hit_is_not_absence"])

        report = coverage_report(self.db_path, page_size=50)
        self.assertIsNone(report["summary"]["historical_work_count"])
        self.assertTrue(any(row["language"] == "unknown" for row in report["rows"]))
        mixed = [row for row in report["rows"] if row["source_unit"]["source_file"] == "mixed.txt"]
        self.assertEqual(len(mixed), 1)
        self.assertEqual(mixed[0]["language"], "mixed")
        self.assertEqual(
            set(mixed[0]["language_values"]), {"de", "unknown", "zh"}
        )
        self.assertFalse(mixed[0]["known_coverage"]["completeness"]["is_complete"])

        page_one = list_source_documents(self.db_path, page=1, page_size=2)
        page_two = list_source_documents(self.db_path, page=2, page_size=2)
        ids = [item["source_id"] for item in page_one["sources"] + page_two["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(list_source_documents(self.db_path, work="Das Kapital")["sources"])
        self.assertTrue(list_source_documents(self.db_path, version="1890")["sources"])

    def test_term_probe_separates_types_preserves_raw_text_and_pages(self) -> None:
        build_source_catalog(self.db_path, self.manifest_path)
        probe = probe_terms(
            [],
            term_groups={
                "exact_phrase": ["Allgemeine Arbeit"],
                "lexical_variant": ["Productivkräfte"],
                "semantic_related": ["Arbeitsprozess"],
            },
            page_size=50,
            db_path=self.db_path,
        )
        types = {item["match_type"] for item in probe["matches"]}
        self.assertEqual(types, {"exact_phrase", "lexical_variant", "semantic_related"})
        variant = next(item for item in probe["matches"] if item["match_type"] == "lexical_variant")
        self.assertEqual(variant["matched_form"], "Productiv-\nkräfte")
        self.assertIn("Productiv-\nkräfte", variant["context"])
        self.assertTrue(variant["match_id"].startswith("tm_"))
        self.assertTrue(variant["source_id"])
        self.assertTrue(variant["version"]["source_doc"])
        self.assertEqual(variant["locator"]["text_type"], "TEXT")

        all_matches = []
        page = 1
        while True:
            current = probe_terms(
                [],
                term_groups={
                    "exact_phrase": ["Allgemeine Arbeit"],
                    "lexical_variant": ["Productivkräfte"],
                    "semantic_related": ["Arbeitsprozess"],
                },
                page=page,
                page_size=2,
                db_path=self.db_path,
            )
            all_matches.extend(current["matches"])
            if not current["pagination"]["has_next"]:
                break
            page += 1
        match_ids = [item["match_id"] for item in all_matches]
        self.assertEqual(len(match_ids), len(set(match_ids)))
        self.assertEqual(len(all_matches), probe["pagination"]["total_matches"])
        self.assertGreaterEqual(len({item["version"]["source_doc"] for item in all_matches}), 2)

        zh = probe_terms(["一般劳动"], language="zh", db_path=self.db_path)
        self.assertTrue(zh["matches"])
        self.assertTrue(all(item["language"] == "zh" for item in zh["matches"]))
        unknown = probe_terms(["Allgemeine Arbeit"], language="unknown", db_path=self.db_path)
        self.assertTrue(unknown["matches"])
        self.assertTrue(all(item["language"] == "unknown" for item in unknown["matches"]))
        apparatus = probe_terms(
            ["Allgemeine Arbeit"], text_type="APPARAT", db_path=self.db_path
        )
        self.assertTrue(apparatus["matches"])
        self.assertTrue(all(item["locator"]["text_type"] == "APPARAT" for item in apparatus["matches"]))

        cross_record = probe_terms(["Arbeitsbedingungen"], db_path=self.db_path)
        self.assertEqual(cross_record["matches"], [])
        self.assertEqual(cross_record["terms"][0]["result_status"], "no_match_in_indexed_scope")
        self.assertIsNone(cross_record["terms"][0]["absence_claim"])

        legacy = probe_terms(["Allgemeine Arbeit"], db_path=self.db_path)
        self.assertIn("terms", legacy)
        self.assertIn("summary", legacy)
        self.assertIn("total_page_hits", legacy["summary"])


if __name__ == "__main__":
    unittest.main()
