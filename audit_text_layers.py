#!/usr/bin/env python3
"""Audit conservative MEGA text-layer predictions without mutating the index."""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from text_layer import CLASSIFIER_VERSION, classify_records

BASE_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    with (BASE_DIR / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_groups(conn: sqlite3.Connection) -> list[list[dict]]:
    columns = (
        "id, source_file, source_collection, source_type, mega_abteilung, band, "
        "page, chunk_text, source_title"
    )
    rows = conn.execute(
        f"SELECT {columns} FROM chunks ORDER BY source_file, source_collection, page, id"
    ).fetchall()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    names = [item.strip() for item in columns.split(",")]
    for row in rows:
        record = dict(zip(names, row))
        key = (
            record.get("source_collection") or "ocr",
            record.get("source_file") or record.get("source_title") or "unknown",
            record.get("source_type") or "UNKNOWN",
        )
        groups[key].append(record)
    return list(groups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=BASE_DIR / "docs" / "text_layer_audit.json",
    )
    parser.add_argument("--samples-per-layer", type=int, default=8)
    args = parser.parse_args()

    config = load_config()
    db_path = Path(config["paths"]["metadata_db"])
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        groups = load_groups(conn)
    finally:
        conn.close()

    counts = Counter()
    confidence_bands = Counter()
    source_layer_counts: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list[dict]] = defaultdict(list)
    predictions: dict[str, dict] = {}

    for group in groups:
        decisions = classify_records(group)
        records_by_id = {record["id"]: record for record in group}
        for record_id, decision in decisions.items():
            record = records_by_id[record_id]
            counts[decision.layer] += 1
            confidence_bands[
                "high" if decision.confidence >= 0.9 else
                "medium" if decision.confidence >= 0.6 else "unclassified"
            ] += 1
            source_layer_counts[record.get("source_file") or "megadigital"][decision.layer] += 1
            predictions[record_id] = decision.to_dict()
            if len(samples[decision.layer]) < args.samples_per_layer:
                samples[decision.layer].append({
                    "id": record_id,
                    "source_file": record.get("source_file"),
                    "abteilung": record.get("mega_abteilung"),
                    "band": record.get("band"),
                    "page": record.get("page"),
                    "header": decision.header,
                    "provenance": decision.provenance,
                    "preview": " ".join((record.get("chunk_text") or "").split())[:300].rstrip(),
                })

    report = {
        "created_at": datetime.now().isoformat(),
        "classifier_version": CLASSIFIER_VERSION,
        "total": sum(counts.values()),
        "counts": dict(counts.most_common()),
        "confidence_bands": dict(confidence_bands),
        "samples": dict(samples),
        "source_layer_counts": {
            source: dict(counter) for source, counter in source_layer_counts.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown_path = args.output.with_suffix(".md")
    lines = [
        "# MEGA Text Layer Audit",
        "",
        f"- Classifier: `{CLASSIFIER_VERSION}`",
        f"- Records: {report['total']:,}",
        "",
        "## Predicted Layers",
        "",
        "| Layer | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| {layer} | {count:,} |" for layer, count in counts.most_common())
    lines.extend(["", "## Samples", ""])
    for layer, layer_samples in samples.items():
        lines.append(f"### {layer}")
        lines.append("")
        for sample in layer_samples:
            lines.append(
                f"- {sample['abteilung']}/{sample['band']} p.{sample['page']} "
                f"`{sample['source_file']}` — header: `{sample['header']}`"
            )
            lines.append(f"  - {sample['preview']}")
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "json": str(args.output),
        "markdown": str(markdown_path),
        "total": report["total"],
        "counts": report["counts"],
        "confidence_bands": report["confidence_bands"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())