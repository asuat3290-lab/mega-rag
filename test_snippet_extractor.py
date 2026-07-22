#!/usr/bin/env python3
"""Fast tests for sentence-aware evidence snippets."""

import unittest

from snippet_extractor import extract_best_snippet


class SnippetBoundaryTests(unittest.TestCase):
    def test_context_expands_to_sentence_boundaries(self):
        text = (
            "Ein voriger vollständiger Satz mit genügend Kontext. "
            "Der zentrale Ausdruck steht in diesem vollständigen Satz. "
            "Ein nachfolgender vollständiger Satz liefert weiteren Kontext."
        )
        result = extract_best_snippet(
            text, ["zentrale Ausdruck"], before=8, after=20
        )
        self.assertIn("zentrale Ausdruck", result["snippet"])
        self.assertTrue(result["context_boundary_complete"])
        stripped = result["snippet"].removeprefix("...")
        self.assertTrue(stripped.startswith("Der zentrale Ausdruck"))
        self.assertIn("zentrale Ausdruck", result["preview"])


    def test_german_closing_quote_is_a_sentence_boundary(self):
        text = (
            "Ein Vorsatz. „Der zentrale Ausdruck steht im Zitat.“ "
            "Ein Nachsatz folgt."
        )
        result = extract_best_snippet(
            text, ["zentrale Ausdruck"], before=5, after=8
        )
        self.assertTrue(result["context_boundary_complete"])
        self.assertIn("zentrale Ausdruck", result["snippet"])
        self.assertIn(".“", result["snippet"])
if __name__ == "__main__":
    unittest.main(verbosity=2)
