#!/usr/bin/env python3
"""Boundary and residue regression tests for glossary expansion."""

import unittest

from glossary_loader import expand_with_glossary


def _entry(terms):
    return {
        "de": terms,
        "related": [],
        "abteilung_hint": None,
        "band_hint": None,
        "query_mode": ["exact"],
        "must_search_exact": False,
        "type": "concept",
        "priority": "high",
        "senses": [],
        "aliases": [],
        "recall": [],
        "_legacy": False,
        "_legacy_raw": None,
    }


class GlossaryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.glossary = {
            "\u4ea4\u5f80": _entry(
                ["Verkehr", "Verkehrsform", "Verkehrsverh\u00e4ltnisse"]
            ),
        }

    def test_source_term_does_not_match_inside_longer_word(self):
        expanded, matched, _ = expand_with_glossary("Verkehrung", self.glossary)
        self.assertEqual(matched, [])
        self.assertEqual(expanded, "Verkehrung")

    def test_exact_source_term_and_chinese_key_still_match(self):
        queries = (
            "Verkehr",
            "Verkehrsform",
            "\u9a6c\u514b\u601d\u8ba8\u8bba\u4ea4\u5f80\u5173\u7cfb",
        )
        for query in queries:
            _expanded, matched, _ = expand_with_glossary(query, self.glossary)
            self.assertEqual(matched, ["\u4ea4\u5f80"])


if __name__ == "__main__":
    unittest.main(verbosity=2)