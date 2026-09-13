#!/usr/bin/env python3
"""Regression tests for the persistent MEGA evidence library."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from evidence_library import (
    build_library_export,
    export_library,
    get_evidence,
    import_research_package,
    library_status,
    list_evidence,
    render_library_markdown,
    update_review,
)


def evidence_item(
    evidence_id: str,
    page_id: str,
    context: str,
    page: int,
    printed_page: str | None = None,
    title: str = "Test source",
) -> dict:
    quote_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
    digital = printed_page is not None
    return {
        "evidence_id": evidence_id,
        "rank": int(evidence_id[1:]),
        "record": {
            "record_type": "passage",
            "record_id": f"passage:{page_id}",
            "page_id": page_id,
            "passage_id": f"passage-{page_id}",
            "content_hash": hashlib.sha256(page_id.encode()).hexdigest(),
        },
        "source": {
            "display_label": f"MEGA II/5 source page {page}",
            "title": title,
            "collection": "megadigital" if digital else "ocr",
            "quality": "authoritative_digital" if digital else "ocr",
            "source_file": "fixture.jsonl",
            "source_doc": "fixture.xml" if digital else None,
            "source_part": "0" if digital else None,
            "source_url": "https://example.invalid/mega" if digital else None,
            "local_path": f"fixture/{page_id}",
        },
        "locator": {
            "abteilung": "II",
            "band": "5",
            "text_type": "TEXT",
            "physical_or_source_page": page,
            "printed_page_label": printed_page,
            "passage_no": 1,
            "char_start": 10,
            "char_end": 10 + len(context),
            "locator_kind": (
                "megadigital_text_page" if digital else "pdf_physical_page"
            ),
            "locator_verified": digital,
            "citation_stub": (
                f"MEGA² II/5, TEXT, S. {printed_page}"
                if digital
                else f"MEGA² II/5, TEXT, PDF physical page {page}"
            ),
            "citation_note": (
                "Verify edition details."
                if digital
                else "Physical PDF page is not a printed MEGA page."
            ),
        },
        "provenance": {
            "text_layer": "author_text" if digital else "textband_unclassified",
            "text_layer_label": (
                "作者原文（结构化文本）" if digital else "TEXT 卷未分类材料"
            ),
            "text_layer_confidence": 0.99 if digital else 0.0,
            "text_layer_provenance": "fixture",
            "verified_author_text": digital,
            "reliability_class": (
                "structured_author_text" if digital else "unclassified_textband"
            ),
            "ocr_quality": "high" if digital else "medium",
        },
        "evidence": {
            "german_context": context,
            "preview": context,
            "matched_term": "Arbeit",
            "matched_priority_terms": ["Arbeit"],
            "quote_sha256": quote_hash,
        },
        "retrieval": {"sources": ["fixture"], "final_score": 1.0},
        "warnings": [] if digital else ["Verify the scan and printed page."],
        "review": {
            "status": "unreviewed",
            "claim_supported": None,
            "literal_translation": None,
            "research_notes": None,
        },
    }


def package(package_id: str, question: str, evidence: list[dict]) -> dict:
    return {
        "schema_version": "mega-research-package-v1",
        "package_id": package_id,
        "generated_at": "2026-07-18T12:00:00+08:00",
        "index_version": "fixture-index",
        "question": question,
        "query_hash": hashlib.sha256(question.encode()).hexdigest(),
        "query": {"question": question},
        "retrieval": {"route": "all"},
        "summary": {"evidence_count": len(evidence)},
        "evidence": evidence,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mega_evidence_library_") as temporary:
        root = Path(temporary)
        database = root / "library.db"
        first = evidence_item(
            "E001",
            "digital-1",
            "Die konkrete Arbeit gilt hier als Verwirklichungsform abstrakter Arbeit.",
            42,
            "33",
            title="Original immutable title",
        )
        second = evidence_item(
            "E002",
            "ocr-1",
            "Die Arbeit erscheint in einer allgemeinen gesellschaftlichen Form.",
            120,
        )
        package_one = package("package-one", "一般劳动是什么", [first, second])

        stats = import_research_package(package_one, database)
        assert stats["evidence_inserted"] == 2
        assert stats["associations_added"] == 2

        repeated = import_research_package(package_one, database)
        assert repeated["evidence_inserted"] == 0
        assert repeated["evidence_reused"] == 2
        assert repeated["associations_added"] == 0

        reused = evidence_item(
            "E001",
            "digital-1",
            first["evidence"]["german_context"],
            42,
            "33",
            title="This must not overwrite immutable provenance",
        )
        third = evidence_item(
            "E002",
            "digital-2",
            "Alle Privatarbeiten erhalten ihren gesellschaftlichen Charakter.",
            43,
            "34",
        )
        package_two = package("package-two", "劳动如何取得社会形式", [reused, third])
        merged = import_research_package(package_two, database)
        assert merged["evidence_inserted"] == 1
        assert merged["evidence_reused"] == 1
        assert merged["associations_added"] == 2

        current = library_status(database)
        assert current["packages"] == 2
        assert current["evidence"] == 3
        assert current["package_links"] == 4
        assert current["status"]["unreviewed"] == 3

        enrichment_context = (
            "Die gedruckte Seitenangabe wird nachtr?glich aus der digitalen Quelle erg?nzt."
        )
        partial = evidence_item(
            "E001",
            "digital-partial",
            enrichment_context,
            55,
            title="First partial source",
        )
        partial["source"].update(
            {
                "collection": "megadigital",
                "quality": "authoritative_digital",
                "source_doc": "partial.xml",
                "source_url": "https://example.invalid/partial",
            }
        )
        partial["locator"].update(
            {
                "locator_kind": "megadigital_source_page",
                "citation_stub": "MEGA II/5, TEXT, MEGAdigital source page 55",
            }
        )
        import_research_package(
            package("package-three-a", "missing page lead", [partial]), database
        )
        completed = evidence_item(
            "E001",
            "digital-partial",
            enrichment_context,
            55,
            "45",
            title="Must not replace the first non-empty title",
        )
        enriched_stats = import_research_package(
            package("package-three-b", "completed printed page", [completed]), database
        )
        assert enriched_stats["provenance_enriched"] == 1
        enriched_item = list_evidence(database, search="nachtr?glich")[0]
        assert enriched_item["printed_page_label"] == "45"
        assert enriched_item["locator_verified"] is True
        assert enriched_item["citation_stub"].endswith("S. 45")
        assert enriched_item["source_title"] == "First partial source"
        assert library_status(database)["provenance_history"] == 1

        item = get_evidence("L000001", database)
        assert item["source_title"] == "Original immutable title"
        assert item["occurrence_count"] == 2
        assert len(item["questions"]) == 2

        reviewed = update_review(
            "L000001",
            {
                "status": "accepted",
                "claim_supported": "General labour receives a social form.",
                "claim_not_supported": "This passage does not establish a chronology.",
                "literal_translation": "具体劳动在这里充当抽象劳动的实现形式。",
                "research_notes": "Retain the contrast between concrete and abstract labour.",
                "thesis_section": "第二章 第一节",
                "tags": "一般劳动, 抽象劳动, 价值形式, 一般劳动",
                "verified_print_page": "33",
                "locator_verified_by_user": True,
                "verified_by": "fixture-reviewer",
            },
            database,
        )
        assert reviewed["review_status"] == "accepted"
        assert reviewed["revision"] == 1
        assert reviewed["tags"] == ["一般劳动", "抽象劳动", "价值形式"]
        assert reviewed["locator_verified_by_user"] is True

        import_research_package(package_one, database)
        after_reimport = get_evidence("L000001", database)
        assert after_reimport["review_status"] == "accepted"
        assert after_reimport["claim_supported"] == reviewed["claim_supported"]
        assert after_reimport["revision"] == 1

        accepted = list_evidence(
            database,
            status="accepted",
            thesis_section="第二章",
            tag="抽象劳动",
        )
        assert len(accepted) == 1
        assert list_evidence(database, search="Verwirklichungsform")
        assert len(list_evidence(database, locator_verified=False)) == 1

        exported = export_library(
            database,
            root / "exports",
            status="accepted",
        )
        assert exported["export"]["summary"]["evidence_count"] == 1
        assert exported["export"]["summary"]["citation_ready_count"] == 1
        assert Path(exported["paths"]["markdown"]).exists()
        assert Path(exported["paths"]["json"]).exists()
        markdown = Path(exported["paths"]["markdown"]).read_text(encoding="utf-8")
        assert "L000001" in markdown
        assert "具体劳动在这里充当抽象劳动的实现形式" in markdown
        assert "Citation ready: `yes`" in markdown

        compact = build_library_export(accepted)
        assert compact["schema_version"] == "mega-evidence-library-export-v1"
        assert "L000001" in render_library_markdown(compact)

        failed = False
        try:
            update_review("L000001", {"status": "invented"}, database)
        except ValueError:
            failed = True
        assert failed, "invalid review status must be rejected"

        conn = sqlite3.connect(database)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT COUNT(*) FROM review_history").fetchone()[0] == 1
        finally:
            conn.close()

    print("PASS: evidence library import, dedupe, review history, and export")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
