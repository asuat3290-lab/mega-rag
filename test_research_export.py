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
        "context_boundary_complete": True,
        "final_score": 0.8,
        "_retrieval_sources": ["passage_fts", "scoped_authoritative"],
        "evidence_eligible": True,
        "_qualification": {"candidate_class": "direct_author_text"},
        "_debug": {"source_boost": 0.18},
        "_source_catalog": {
            "catalog_linked": True,
            "catalog_version": "sc_fixture",
            "source_id": "src_fixture",
            "document_kind": "structured_critical_text",
            "edition_status": "print_edition",
            "authority_rank": 100,
            "volume_group": "II/5",
            "groups": [
                {
                    "group_id": "versions:kapital-band-1",
                    "group_type": "work_versions",
                    "member_role": "print_edition_1867",
                    "sequence_no": 1,
                }
            ],
            "relations": [],
        },
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
        "evidence_eligible": True,
        "_qualification": {"candidate_class": "direct_author_text"},
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
            "query_plan": {
                "plan_status": {"valid_for_evidence": True, "issues": []},
                "qualification_groups": [
                    {"id": "terms", "alternatives": ["Waarenform", "Ideologie"]}
                ],
            },
        },
        "retrieval": {
            "route": "all",
            "top_k": 2,
            "retrieval_mode": "balanced",
            "rerank_method": "rule",
            "debug": {
                "adequacy": {
                    "status": "adequate",
                    "axes": {
                        "semantic": {"status": "adequate", "candidate_count": 2},
                        "provenance": {"status": "verified", "eligible_count": 2},
                        "citation": {"status": "ready", "ready_count": 1},
                    },
                }
            },
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
    assert digital["evidence_uid"].startswith("ev_")
    assert digital["package_evidence_ref"].endswith(":E001")
    assert package["synthesis_gate"]["synthesis_allowed"] is True
    assert package["summary"]["qualified_evidence_count"] == 2
    assert digital["locator"]["locator_verified"] is True
    assert digital["locator"]["citation_stub"] == "MEGA² II/5, TEXT, S. 117"
    assert digital["provenance"]["reliability_class"] == "structured_author_text"
    assert digital["source_identity"]["source_id"] == "src_fixture"
    assert digital["provenance"]["edition_status"] == "print_edition"
    assert package["source_catalog_versions"] == ["sc_fixture"]
    assert package["summary"]["source_catalog_linked_count"] == 1
    assert package["summary"]["version_group_evidence_count"] == 1
    assert ocr["locator"]["locator_verified"] is False
    assert "PDF physical page" in ocr["locator"]["citation_stub"]
    assert any("not automatically verified" in warning for warning in ocr["warnings"])

    diagnostic_payload = fixture_payload()
    diagnostic_payload["retrieval"]["debug"]["adequacy"] = {
        "status": "insufficient",
        "axes": {"semantic": {"status": "insufficient"}},
    }
    for row in diagnostic_payload["results"]:
        row["evidence_eligible"] = False
    diagnostic = build_research_package("诊断查询", diagnostic_payload)
    assert diagnostic["artifact_type"] == "diagnostic_candidate_package"
    assert diagnostic["synthesis_gate"]["synthesis_allowed"] is False

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
