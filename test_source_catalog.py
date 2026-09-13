#!/usr/bin/env python3
"""Regression tests for persistent source identities and version relations."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from source_catalog import (
    build_source_catalog,
    catalog_status,
    get_source_context,
    hydrate_records_with_source_catalog,
    list_source_documents,
    refresh_source_catalog_if_needed,
)


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
    page INTEGER
);
"""


class SourceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.db_path = root / "metadata.db"
        self.manifest_path = root / "source_catalog.yaml"
        connection = sqlite3.connect(self.db_path)
        connection.executescript(CHUNKS_SCHEMA)
        rows = [
            (
                "ocr5-text-1", "II5_TEXT/page_0001.txt", "ZWEITE BAND 5 TEXT",
                "II", "5", "TEXT", "ocr", "ocr", "", "", "", "", "de", 1,
            ),
            (
                "ocr5-text-2", "II5_TEXT/page_0002.txt", "ZWEITE BAND 5 TEXT",
                "II", "5", "TEXT", "ocr", "ocr", "", "", "", "", "de", 2,
            ),
            (
                "ocr5-app-1", "II5_APP/page_0001.txt", "ZWEITE BAND 5 APPARAT",
                "II", "5", "APPARAT", "ocr", "ocr", "", "", "", "", "de", 1,
            ),
            (
                "md5-1", "megadigital/II5/1", "all_chunks.jsonl",
                "II", "5", "TEXT", "megadigital", "authoritative_digital",
                "MEGA_A2_B005-00_ETX.xml", "0",
                "Das Kapital, 1. Band, Druckfassung 1867",
                "https://example.invalid/ii5", "de", 7,
            ),
            (
                "md5-2", "megadigital/II5/2", "all_chunks.jsonl",
                "II", "5", "TEXT", "megadigital", "authoritative_digital",
                "MEGA_A2_B005-00_ETX.xml", "0",
                "Das Kapital, 1. Band, Druckfassung 1867",
                "https://example.invalid/ii5", "de", 8,
            ),
            (
                "md10-1", "megadigital/II10/1", "all_chunks.jsonl",
                "II", "10", "TEXT", "megadigital", "authoritative_digital",
                "MEGA_A2_B010-00_ETX.xml", "0",
                "Das Kapital, 1. Band, Druckfassung 1890",
                "https://example.invalid/ii10", "de", 9,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO chunks (
                id, source_path, source_file, mega_abteilung, band, source_type,
                source_collection, source_quality, source_doc, source_part,
                source_title, source_url, language, page
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        connection.commit()
        connection.close()
        self.manifest_path.write_text(
            """
schema_version: mega-source-catalog-manifest-v1
version_groups:
  - id: versions:kapital-band-1
    group_type: work_versions
    canonical_title: Das Kapital. Band I
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

    def tearDown(self):
        self.temporary.cleanup()

    def test_build_is_idempotent_and_maps_every_record(self):
        first = build_source_catalog(self.db_path, self.manifest_path)
        second = build_source_catalog(self.db_path, self.manifest_path)
        self.assertEqual(first["documents"], 4)
        self.assertEqual(first["mapped_records"], 6)
        self.assertEqual(second["documents"], 4)
        self.assertEqual(second["mapped_records"], 6)
        self.assertEqual(first["catalog_version"], second["catalog_version"])
        status = catalog_status(self.db_path)
        self.assertTrue(status["available"])
        self.assertFalse(status["build_required"])
        self.assertEqual(status["unmapped_records"], 0)
        self.assertEqual(status["relation_counts"]["apparatus_for"], 1)
        self.assertEqual(status["relation_counts"]["digital_parallel_to_ocr"], 1)
        self.assertEqual(
            status["relation_counts"]["earlier_print_edition_of_same_work"], 1
        )

    def test_version_group_and_relation_are_inspectable(self):
        build_source_catalog(self.db_path, self.manifest_path)
        sources = list_source_documents(
            self.db_path, collection="megadigital", band="5"
        )["sources"]
        self.assertEqual(len(sources), 1)
        context = get_source_context(sources[0]["source_id"], self.db_path)
        self.assertIsNotNone(context)
        group_ids = {group["group_id"] for group in context["groups"]}
        self.assertIn("versions:kapital-band-1", group_ids)
        predicates = {relation["predicate"] for relation in context["relations"]}
        self.assertIn("earlier_print_edition_of_same_work", predicates)
        self.assertIn("digital_parallel_to_ocr", predicates)

    def test_hydration_adds_identity_without_changing_result_fields(self):
        build_source_catalog(self.db_path, self.manifest_path)
        records = [{"id": "md5-1", "page_id": "md5-1", "final_score": 0.75}]
        diagnostics = hydrate_records_with_source_catalog(records, self.db_path)
        self.assertEqual(diagnostics["linked"], 1)
        self.assertEqual(records[0]["final_score"], 0.75)
        identity = records[0]["_source_catalog"]
        self.assertTrue(identity["catalog_linked"])
        self.assertEqual(identity["edition_status"], "print_edition")
        self.assertEqual(identity["volume_group"], "II/5")
        self.assertTrue(
            any(
                group["group_id"] == "versions:kapital-band-1"
                for group in identity["groups"]
            )
        )

    def test_refresh_runs_only_after_chunk_coverage_changes(self):
        build_source_catalog(self.db_path, self.manifest_path)
        unchanged = refresh_source_catalog_if_needed(
            self.db_path, self.manifest_path
        )
        self.assertFalse(unchanged["refreshed"])
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO chunks (
                id, source_path, source_file, mega_abteilung, band, source_type,
                source_collection, source_quality, language, page
            ) VALUES ('ocr10-text-1', 'II10/page_0001.txt', 'ZWEITE BAND 10 TEXT',
                      'II', '10', 'TEXT', 'ocr', 'ocr', 'de', 1)
            """
        )
        connection.commit()
        connection.close()
        refreshed = refresh_source_catalog_if_needed(
            self.db_path, self.manifest_path
        )
        self.assertTrue(refreshed["refreshed"])
        self.assertEqual(catalog_status(self.db_path)["unmapped_records"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
