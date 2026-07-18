#!/usr/bin/env python3
"""Fast fixtures for the MEGA research-package exporter."""

import json
import tempfile
from pathlib import Path

from research_export import (
    build_research_package,
    render_research_markdown,
    write_research_package,
)


def fixture_payload() -> dict:
    digital = {
        "id": "digital-page-1",
        "page_id": "digital-page-1",
        "record_type": "passage",
        "passage_id": "passage-1",
        "abteilung": "II",
        "band": "5",
        "type": "TEXT",
        "page": 120,
        "page_label": "117",
        "passage_no": 2,
        "char_start": 100,
        "char_end": 520,
        "source_collection": "megadigital",
        "source_quality": "authoritative_digital",
        "source_title": "Das Kapital, 1. Band, Druckfassung 1867",
        "source_url": "https://example.invalid/mega?page=120",
        "page_kind": "megadigital_page",
        "text_layer": "author_text",
        "text_layer_confidence": 1.0,
        "text_layer_provenance": "megadigital",
        "ocr_quality": "high",
        "display_snippet": "Die Waarenform enthält den Ausdruck des Werths.",
        "display_preview": "Die Waarenform enthält den Ausdruck des Werths.",
        "matched_term": "Waarenform",
        "final_score": 0.8,
        "_retrieval_sources": ["passage_fts", "scoped_authoritative"],
        "_debug": {"source_boost": 0.18},
    }
    ocr = {
        "id": "ocr-page-1",
        "page_id": "ocr-page-1",
        "record_type": "page",
        "abteilung": "I",
        "band": "5",
        "type": "TEXT",
        "page": 147,
        "source_collection": "ocr",
        "source_quality": "ocr",
        "page_kind": "pdf",
        "text_layer": "textband_unclassified",
        "text_layer_confidence": 0.0,
        "text_layer_provenance": "conservative-v1",
        "ocr_quality": "medium",
        "display_snippet": "Die Moral, Religion, Metaphysik und sonstige Ideologie.",
        "display_preview": "Die Moral, Religion, Metaphysik und sonstige Ideologie.",
        "matched_term": "Ideologie",
        "final_score": 0.7,
        "_retrieval_sources": ["page_fts"],
        "_debug": {},
    }
    return {
        "query": {
            "question": "商品形式与意识形态",
            "expanded_query": "商品形式 意识形态 Waarenform Ideologie",
            "matched_glossary_terms": ["商品", "意识形态"],
            "glossary_hints": {},
            "query_profile": {
                "intent": "author_argument",
                "target_abteilung": None,
                "target_band": None,
            },
            "priority_terms": ["Waarenform", "Ideologie"],
        },
        "retrieval": {
            "route": "all",
            "top_k": 2,
            "retrieval_mode": "balanced",
            "rerank_method": "rule",
            "debug": {},
        },
        "results": [digital, ocr],
    }


def main() -> int:
    package = build_research_package("商品形式与意识形态", fixture_payload())
    assert package["schema_version"] == "mega-research-package-v1"
    assert package["summary"]["evidence_count"] == 2
    assert package["summary"]["verified_author_text_count"] == 1

    digital, ocr = package["evidence"]
    assert digital["evidence_id"] == "E001"
    assert digital["locator"]["locator_verified"] is True
    assert digital["locator"]["citation_stub"] == "MEGA² II/5, TEXT, S. 117"
    assert digital["provenance"]["reliability_class"] == "structured_author_text"
    assert ocr["locator"]["locator_verified"] is False
    assert "PDF physical page" in ocr["locator"]["citation_stub"]
    assert any("not automatically verified" in warning for warning in ocr["warnings"])

    markdown = render_research_markdown(package)
    assert "E001" in markdown and "E002" in markdown
    assert "Waarenform" in markdown
    assert "PDF physical pages are retrieval locators" in markdown

    with tempfile.TemporaryDirectory() as temporary:
        paths = write_research_package(package, temporary, "both")
        json_path = Path(paths["json"])
        markdown_path = Path(paths["markdown"])
        assert json_path.exists() and markdown_path.exists()
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert loaded["package_id"] == package["package_id"]

    print("PASS: research package schema, citation boundaries, and file export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
