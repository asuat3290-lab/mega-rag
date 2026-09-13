#!/usr/bin/env python3
"""Persistent source identities and conservative version relations for MEGA."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


CATALOG_SCHEMA_VERSION = "mega-source-catalog-v2"
DISCOVERY_METHOD = "chunks_grouping_v1"
AUTO_PROVENANCE = "auto:source-catalog-v1"
UNKNOWN_LANGUAGE = "unknown"
MIXED_LANGUAGE = "mixed"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "source_catalog.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _key(value: Any) -> str:
    return _normalize(value).casefold()


def _normalize_language(value: Any) -> str:
    """Normalize a declared language without inventing one for blank metadata."""
    text = _normalize(value)
    if not text or text.casefold() in {"unknown", "unk", "n/a", "na", "null", "none"}:
        return UNKNOWN_LANGUAGE
    return text


def _language_summary(
    raw_values: Any,
    unknown_records: int = 0,
) -> tuple[str, list[str]]:
    """Return one conservative language label and the observed declarations.

    A known declaration plus blank declarations is deliberately ``mixed``.  A
    blank value is an evidence gap, not permission to inherit a nearby default.
    """
    values = []
    for raw in str(raw_values or "").split(","):
        value = _normalize_language(raw)
        if value not in values:
            values.append(value)
    if int(unknown_records or 0) > 0 and UNKNOWN_LANGUAGE not in values:
        values.append(UNKNOWN_LANGUAGE)
    values.sort(key=str.casefold)
    known_values = [value for value in values if value != UNKNOWN_LANGUAGE]
    if not known_values:
        return UNKNOWN_LANGUAGE, [UNKNOWN_LANGUAGE]
    if len(values) == 1:
        return known_values[0], values
    return MIXED_LANGUAGE, values


def _language_is_chinese(value: Any) -> bool:
    return _normalize_language(value).casefold().replace("_", "-").startswith("zh")


def _metadata_db_from_config() -> Path:
    config_path = SCRIPT_DIR / "config.yaml"
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
        configured = (config.get("paths") or {}).get("metadata_db")
        if configured:
            return Path(configured)
    return SCRIPT_DIR / "metadata.db"


def _resolve_db(path: str | Path | None) -> Path:
    return Path(path) if path else _metadata_db_from_config()


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def ensure_catalog_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_catalog_documents (
            source_id TEXT PRIMARY KEY,
            catalog_schema_version TEXT NOT NULL,
            source_collection TEXT NOT NULL,
            source_key TEXT NOT NULL,
            source_quality TEXT,
            mega_abteilung TEXT,
            band TEXT,
            volume_group TEXT,
            text_type TEXT,
            source_doc TEXT,
            source_part TEXT,
            source_file TEXT,
            title TEXT,
            normalized_title TEXT,
            document_kind TEXT NOT NULL,
            edition_status TEXT NOT NULL,
            date_start TEXT,
            date_end TEXT,
            authority_rank INTEGER NOT NULL DEFAULT 0,
            language TEXT,
            source_url TEXT,
            local_path TEXT,
            record_count INTEGER NOT NULL DEFAULT 0,
            min_page INTEGER,
            max_page INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            discovery_method TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_collection, source_key)
        );

        CREATE INDEX IF NOT EXISTS idx_source_catalog_volume
        ON source_catalog_documents(mega_abteilung, volume_group, text_type);

        CREATE INDEX IF NOT EXISTS idx_source_catalog_collection
        ON source_catalog_documents(source_collection, active);

        CREATE TABLE IF NOT EXISTS source_catalog_record_map (
            record_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            mapping_method TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(source_id) REFERENCES source_catalog_documents(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_catalog_record_source
        ON source_catalog_record_map(source_id);

        CREATE TABLE IF NOT EXISTS source_catalog_groups (
            group_id TEXT PRIMARY KEY,
            group_type TEXT NOT NULL,
            canonical_title TEXT NOT NULL,
            description TEXT,
            provenance TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_catalog_group_members (
            group_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            member_role TEXT,
            sequence_no INTEGER,
            date_start TEXT,
            date_end TEXT,
            provenance TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(group_id, source_id),
            FOREIGN KEY(group_id) REFERENCES source_catalog_groups(group_id),
            FOREIGN KEY(source_id) REFERENCES source_catalog_documents(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_catalog_member_source
        ON source_catalog_group_members(source_id);

        CREATE TABLE IF NOT EXISTS source_catalog_relations (
            relation_id TEXT PRIMARY KEY,
            subject_source_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_source_id TEXT NOT NULL,
            relation_scope TEXT NOT NULL DEFAULT 'source',
            confidence REAL NOT NULL DEFAULT 1.0,
            provenance TEXT NOT NULL,
            note TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(subject_source_id, predicate, object_source_id),
            FOREIGN KEY(subject_source_id) REFERENCES source_catalog_documents(source_id),
            FOREIGN KEY(object_source_id) REFERENCES source_catalog_documents(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_source_catalog_relation_subject
        ON source_catalog_relations(subject_source_id, active);

        CREATE INDEX IF NOT EXISTS idx_source_catalog_relation_object
        ON source_catalog_relations(object_source_id, active);

        CREATE TABLE IF NOT EXISTS source_catalog_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_catalog_builds (
            build_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version TEXT NOT NULL,
            catalog_version TEXT,
            manifest_path TEXT,
            manifest_hash TEXT,
            documents INTEGER NOT NULL DEFAULT 0,
            mapped_records INTEGER NOT NULL DEFAULT 0,
            groups INTEGER NOT NULL DEFAULT 0,
            relations INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )


def _source_key(row: dict[str, Any]) -> str:
    collection = _key(row.get("source_collection") or "ocr")
    if collection == "megadigital":
        base = _normalize(
            row.get("source_doc") or row.get("source_file") or row.get("local_path")
        )
        part = _normalize(row.get("source_part"))
        return f"{base}#{part}" if part else base
    return _normalize(row.get("source_file") or row.get("local_path"))


def _source_id(collection: str, source_key: str) -> str:
    stable = f"{_key(collection)}|{_key(source_key)}"
    return "src_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _parent_band(band: Any) -> str:
    value = _normalize(band)
    if re.fullmatch(r"\d+\.\d+", value):
        return value.split(".", 1)[0]
    return value


def _volume_group(abteilung: Any, band: Any) -> str:
    section = _normalize(abteilung)
    parent = _parent_band(band)
    return f"{section}/{parent}" if section and parent else ""


def _extract_dates(title: str) -> tuple[str | None, str | None]:
    years = re.search(r"(?<!\d)(1[6789]\d{2})(?:\s*[-\u2013]\s*(\d{2,4}))?", title)
    if not years:
        return None, None
    start = years.group(1)
    end = years.group(2)
    if end and len(end) == 2:
        end = start[:2] + end
    return start, end or start


def _classify_document(row: dict[str, Any]) -> tuple[str, str, int]:
    collection = _key(row.get("source_collection") or "ocr")
    text_type = _normalize(row.get("text_type")).upper()
    title_key = _key(row.get("title"))
    quality = _key(row.get("source_quality"))

    if text_type == "APPARAT":
        document_kind = "critical_apparatus"
        edition_status = "critical_apparatus"
    elif collection == "megadigital":
        document_kind = "structured_critical_text"
        if "redaktionsmanuskript" in title_key:
            edition_status = "editorial_manuscript_edition"
        elif any(term in title_key for term in ("manuskript", "grundrisse", "entwurf", "exzerpt")):
            edition_status = "manuscript_or_draft_edition"
        elif any(term in title_key for term in ("druckfassung", "drucktext")):
            edition_status = "print_edition"
        elif any(term in title_key for term in ("brief", "korrespondenz")):
            edition_status = "correspondence_edition"
        else:
            edition_status = "critical_edition_text"
    else:
        document_kind = "ocr_textband"
        edition_status = "critical_edition_textband"

    if collection == "megadigital" and quality == "authoritative_digital":
        authority_rank = 100
    elif collection == "megadigital":
        authority_rank = 80
    elif text_type == "TEXT":
        authority_rank = 45
    else:
        authority_rank = 40
    return document_kind, edition_status, authority_rank


def _discover_documents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(chunks)")
    }
    required = {
        "id", "source_path", "source_file", "mega_abteilung", "band",
        "source_type", "source_collection",
    }
    missing = required - columns
    if missing:
        raise RuntimeError(f"chunks table lacks source metadata columns: {sorted(missing)}")

    def column(name: str, fallback: str = "''") -> str:
        return name if name in columns else fallback

    rows = connection.execute(
        f"""
        SELECT
            COALESCE(NULLIF(source_collection, ''), 'ocr') AS source_collection,
            COALESCE(NULLIF({column('source_quality')}, ''), 'ocr') AS source_quality,
            mega_abteilung,
            band,
            source_type AS text_type,
            COALESCE({column('source_doc')}, '') AS source_doc,
            COALESCE({column('source_part')}, '') AS source_part,
            COALESCE(source_file, '') AS source_file,
            MAX(COALESCE({column('source_title')}, '')) AS title,
            MAX(COALESCE({column('source_url')}, '')) AS source_url,
            MIN(COALESCE(source_path, '')) AS local_path,
            GROUP_CONCAT(DISTINCT NULLIF(TRIM({column('language')}), '')) AS language_values,
            SUM(CASE WHEN NULLIF(TRIM({column('language')}), '') IS NULL THEN 1 ELSE 0 END)
                AS language_unknown_records,
            COUNT(*) AS record_count,
            MIN(page) AS min_page,
            MAX(page) AS max_page
        FROM chunks
        GROUP BY
            COALESCE(NULLIF(source_collection, ''), 'ocr'),
            COALESCE(NULLIF({column('source_quality')}, ''), 'ocr'),
            mega_abteilung, band, source_type,
            COALESCE({column('source_doc')}, ''),
            COALESCE({column('source_part')}, ''),
            COALESCE(source_file, '')
        ORDER BY source_collection, mega_abteilung, band, text_type, source_file
        """
    ).fetchall()
    documents = []
    for raw in rows:
        row = dict(raw)
        source_key = _source_key(row)
        if not source_key:
            continue
        row["language"], row["language_values"] = _language_summary(
            row.get("language_values"), row.get("language_unknown_records")
        )
        source_id = _source_id(row["source_collection"], source_key)
        kind, edition_status, authority_rank = _classify_document(row)
        date_start, date_end = _extract_dates(_normalize(row.get("title")))
        row.update(
            source_id=source_id,
            source_key=source_key,
            volume_group=_volume_group(row.get("mega_abteilung"), row.get("band")),
            document_kind=kind,
            edition_status=edition_status,
            authority_rank=authority_rank,
            date_start=date_start,
            date_end=date_end,
        )
        documents.append(row)
    return documents


def _upsert_document(
    connection: sqlite3.Connection, document: dict[str, Any], now: str
) -> None:
    metadata = {
        "grouping_basis": (
            "source_doc+source_part"
            if _key(document["source_collection"]) == "megadigital"
            else "source_file"
        ),
        "scope_warning": (
            "A source unit is a retrieval carrier, not automatically one historical witness."
        ),
        "language_basis": "distinct non-empty chunks.language values; blank values remain unknown",
        "language_values": list(document.get("language_values") or [UNKNOWN_LANGUAGE]),
        "language_unknown_records": int(document.get("language_unknown_records") or 0),
        "coverage_basis": "indexed chunk records grouped by source identity",
        "completeness_basis": None,
    }
    connection.execute(
        """
        INSERT INTO source_catalog_documents (
            source_id, catalog_schema_version, source_collection, source_key,
            source_quality, mega_abteilung, band, volume_group, text_type,
            source_doc, source_part, source_file, title, normalized_title,
            document_kind, edition_status, date_start, date_end, authority_rank,
            language, source_url, local_path, record_count, min_page, max_page,
            metadata_json, discovery_method, active, created_at, updated_at
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ON CONFLICT(source_id) DO UPDATE SET
            catalog_schema_version=excluded.catalog_schema_version,
            source_collection=excluded.source_collection,
            source_key=excluded.source_key,
            source_quality=excluded.source_quality,
            mega_abteilung=excluded.mega_abteilung,
            band=excluded.band,
            volume_group=excluded.volume_group,
            text_type=excluded.text_type,
            source_doc=excluded.source_doc,
            source_part=excluded.source_part,
            source_file=excluded.source_file,
            title=excluded.title,
            normalized_title=excluded.normalized_title,
            document_kind=excluded.document_kind,
            edition_status=excluded.edition_status,
            date_start=excluded.date_start,
            date_end=excluded.date_end,
            authority_rank=excluded.authority_rank,
            language=excluded.language,
            source_url=excluded.source_url,
            local_path=excluded.local_path,
            record_count=excluded.record_count,
            min_page=excluded.min_page,
            max_page=excluded.max_page,
            metadata_json=excluded.metadata_json,
            discovery_method=excluded.discovery_method,
            active=1,
            updated_at=excluded.updated_at
        """,
        (
            document["source_id"], CATALOG_SCHEMA_VERSION,
            document["source_collection"], document["source_key"],
            document.get("source_quality"), document.get("mega_abteilung"),
            document.get("band"), document.get("volume_group"),
            document.get("text_type"), document.get("source_doc"),
            document.get("source_part"), document.get("source_file"),
            document.get("title"), _key(document.get("title")),
            document["document_kind"], document["edition_status"],
            document.get("date_start"), document.get("date_end"),
            document["authority_rank"], document.get("language"),
            document.get("source_url"), document.get("local_path"),
            document.get("record_count", 0), document.get("min_page"),
            document.get("max_page"),
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            DISCOVERY_METHOD, 1, now, now,
        ),
    )


def _rebuild_record_map(
    connection: sqlite3.Connection,
    documents: Iterable[dict[str, Any]],
    now: str,
) -> int:
    connection.execute("DELETE FROM source_catalog_record_map")
    mapped = 0
    for document in documents:
        collection = _key(document["source_collection"])
        if collection == "megadigital":
            cursor = connection.execute(
                """
                INSERT INTO source_catalog_record_map (
                    record_id, source_id, mapping_method, confidence, updated_at
                )
                SELECT id, ?, 'source_doc+source_part', 1.0, ?
                FROM chunks
                WHERE LOWER(COALESCE(source_collection, 'ocr'))='megadigital'
                  AND COALESCE(source_doc, '')=?
                  AND COALESCE(source_part, '')=?
                """,
                (
                    document["source_id"], now, document.get("source_doc") or "",
                    document.get("source_part") or "",
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO source_catalog_record_map (
                    record_id, source_id, mapping_method, confidence, updated_at
                )
                SELECT id, ?, 'source_file', 1.0, ?
                FROM chunks
                WHERE LOWER(COALESCE(source_collection, 'ocr'))=?
                  AND COALESCE(source_file, '')=?
                """,
                (
                    document["source_id"], now, collection,
                    document.get("source_file") or "",
                ),
            )
        mapped += max(0, int(cursor.rowcount or 0))
    return mapped


def _relation_id(subject: str, predicate: str, obj: str) -> str:
    stable = f"{subject}|{predicate}|{obj}"
    return "rel_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _upsert_relation(
    connection: sqlite3.Connection,
    *,
    subject: str,
    predicate: str,
    obj: str,
    scope: str,
    confidence: float,
    provenance: str,
    note: str,
    now: str,
) -> None:
    if not subject or not obj or subject == obj:
        return
    relation_id = _relation_id(subject, predicate, obj)
    connection.execute(
        """
        INSERT INTO source_catalog_relations (
            relation_id, subject_source_id, predicate, object_source_id,
            relation_scope, confidence, provenance, note, active,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,1,?,?)
        ON CONFLICT(subject_source_id, predicate, object_source_id) DO UPDATE SET
            relation_scope=excluded.relation_scope,
            confidence=excluded.confidence,
            provenance=excluded.provenance,
            note=excluded.note,
            active=1,
            updated_at=excluded.updated_at
        """,
        (
            relation_id, subject, predicate, obj, scope, float(confidence),
            provenance, note, now, now,
        ),
    )


def _upsert_group(
    connection: sqlite3.Connection,
    *,
    group_id: str,
    group_type: str,
    title: str,
    description: str,
    provenance: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_catalog_groups (
            group_id, group_type, canonical_title, description, provenance,
            active, created_at, updated_at
        ) VALUES (?,?,?,?,?,1,?,?)
        ON CONFLICT(group_id) DO UPDATE SET
            group_type=excluded.group_type,
            canonical_title=excluded.canonical_title,
            description=excluded.description,
            provenance=excluded.provenance,
            active=1,
            updated_at=excluded.updated_at
        """,
        (group_id, group_type, title, description, provenance, now, now),
    )


def _upsert_membership(
    connection: sqlite3.Connection,
    *,
    group_id: str,
    source_id: str,
    role: str,
    sequence_no: int | None,
    date_start: str | None,
    date_end: str | None,
    provenance: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO source_catalog_group_members (
            group_id, source_id, member_role, sequence_no, date_start, date_end,
            provenance, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(group_id, source_id) DO UPDATE SET
            member_role=excluded.member_role,
            sequence_no=excluded.sequence_no,
            date_start=excluded.date_start,
            date_end=excluded.date_end,
            provenance=excluded.provenance,
            updated_at=excluded.updated_at
        """,
        (
            group_id, source_id, role, sequence_no, date_start, date_end,
            provenance, now, now,
        ),
    )


def _build_automatic_structure(
    connection: sqlite3.Connection,
    documents: list[dict[str, Any]],
    now: str,
) -> None:
    by_volume: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        if document.get("volume_group"):
            by_volume[document["volume_group"]].append(document)

    for volume_group, members in by_volume.items():
        group_id = "volume:" + volume_group
        _upsert_group(
            connection,
            group_id=group_id,
            group_type="mega_volume",
            title=f"MEGA {volume_group}",
            description=(
                "Retrieval sources assigned to the same parent MEGA volume. "
                "Subband and page alignment may differ."
            ),
            provenance=AUTO_PROVENANCE,
            now=now,
        )
        for member in members:
            _upsert_membership(
                connection,
                group_id=group_id,
                source_id=member["source_id"],
                role=_normalize(member.get("text_type")).casefold(),
                sequence_no=None,
                date_start=member.get("date_start"),
                date_end=member.get("date_end"),
                provenance=AUTO_PROVENANCE,
                now=now,
            )

        texts = [item for item in members if _key(item.get("text_type")) == "text"]
        apparats = [
            item for item in members if _key(item.get("text_type")) == "apparat"
        ]
        for apparatus in apparats:
            same_collection = [
                item for item in texts
                if _key(item.get("source_collection"))
                == _key(apparatus.get("source_collection"))
            ]
            for text in same_collection:
                _upsert_relation(
                    connection,
                    subject=apparatus["source_id"],
                    predicate="apparatus_for",
                    obj=text["source_id"],
                    scope="mega_volume",
                    confidence=0.95,
                    provenance=AUTO_PROVENANCE,
                    note=(
                        "Source units share collection and parent MEGA volume; "
                        "the relation does not align individual apparatus entries."
                    ),
                    now=now,
                )

        digital = [
            item for item in texts
            if _key(item.get("source_collection")) == "megadigital"
        ]
        ocr_texts = [
            item for item in texts if _key(item.get("source_collection")) == "ocr"
        ]
        for structured in digital:
            for ocr in ocr_texts:
                _upsert_relation(
                    connection,
                    subject=structured["source_id"],
                    predicate="digital_parallel_to_ocr",
                    obj=ocr["source_id"],
                    scope="mega_volume",
                    confidence=0.85,
                    provenance=AUTO_PROVENANCE,
                    note=(
                        "Both sources cover the same parent MEGA volume. "
                        "This is not a claim of chunk-level or page-level identity."
                    ),
                    now=now,
                )


def _manifest_payload(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if not path or not path.exists():
        return {}, None
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8-sig")) or {}
    if not isinstance(payload, dict):
        raise ValueError("source catalog manifest must contain a YAML mapping")
    return payload, hashlib.sha256(raw).hexdigest()


def _selector_matches(document: dict[str, Any], selector: dict[str, Any]) -> bool:
    aliases = {
        "collection": "source_collection",
        "abteilung": "mega_abteilung",
        "type": "text_type",
    }
    for requested_field, expected in selector.items():
        field = aliases.get(requested_field, requested_field)
        actual = document.get(field)
        if isinstance(expected, list):
            if _key(actual) not in {_key(value) for value in expected}:
                return False
        elif _key(actual) != _key(expected):
            return False
    return True


def _select_documents(
    documents: list[dict[str, Any]], selector: dict[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(selector, dict) or not selector:
        return []
    return [item for item in documents if _selector_matches(item, selector)]


def _apply_manifest(
    connection: sqlite3.Connection,
    documents: list[dict[str, Any]],
    payload: dict[str, Any],
    manifest_path: Path | None,
    now: str,
) -> None:
    provenance = f"manifest:{manifest_path.name if manifest_path else 'inline'}"
    for group in payload.get("version_groups", []) or []:
        if not isinstance(group, dict) or not group.get("id"):
            continue
        group_id = _normalize(group["id"])
        _upsert_group(
            connection,
            group_id=group_id,
            group_type=_normalize(group.get("group_type") or "work_versions"),
            title=_normalize(group.get("canonical_title") or group_id),
            description=_normalize(group.get("description")),
            provenance=provenance,
            now=now,
        )
        for member in group.get("members", []) or []:
            selector = member.get("selector") if isinstance(member, dict) else None
            for document in _select_documents(documents, selector or {}):
                _upsert_membership(
                    connection,
                    group_id=group_id,
                    source_id=document["source_id"],
                    role=_normalize(member.get("role")),
                    sequence_no=member.get("sequence"),
                    date_start=_normalize(member.get("date_start")) or document.get("date_start"),
                    date_end=_normalize(member.get("date_end")) or document.get("date_end"),
                    provenance=provenance,
                    now=now,
                )

    for relation in payload.get("relations", []) or []:
        if not isinstance(relation, dict):
            continue
        subjects = _select_documents(documents, relation.get("subject") or {})
        objects = _select_documents(documents, relation.get("object") or {})
        for subject in subjects:
            for obj in objects:
                _upsert_relation(
                    connection,
                    subject=subject["source_id"],
                    predicate=_normalize(relation.get("predicate") or "related_to"),
                    obj=obj["source_id"],
                    scope=_normalize(relation.get("scope") or "textual_history"),
                    confidence=float(relation.get("confidence", 1.0)),
                    provenance=provenance,
                    note=_normalize(relation.get("note")),
                    now=now,
                )


def _catalog_version(
    connection: sqlite3.Connection, manifest_hash: str | None
) -> str:
    payload = {
        "schema": CATALOG_SCHEMA_VERSION,
        "manifest_hash": manifest_hash,
        "documents": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT source_id, source_collection, source_key, mega_abteilung,
                       band, text_type, document_kind, edition_status,
                       authority_rank, language, record_count, metadata_json
                FROM source_catalog_documents WHERE active=1 ORDER BY source_id
                """
            )
        ],
        "groups": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT g.group_id, g.group_type, m.source_id, m.member_role,
                       m.sequence_no, m.date_start, m.date_end
                FROM source_catalog_groups g
                JOIN source_catalog_group_members m ON m.group_id=g.group_id
                WHERE g.active=1 ORDER BY g.group_id, m.source_id
                """
            )
        ],
        "relations": [
            tuple(row)
            for row in connection.execute(
                """
                SELECT subject_source_id, predicate, object_source_id,
                       relation_scope, confidence, provenance
                FROM source_catalog_relations
                WHERE active=1
                ORDER BY subject_source_id, predicate, object_source_id
                """
            )
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "sc_" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def build_source_catalog(
    metadata_db: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Discover source units and rebuild only derived catalog structures."""
    db_path = _resolve_db(metadata_db)
    manifest = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST
    now = _now_iso()
    connection = _connect(db_path)
    ensure_catalog_schema(connection)
    manifest_payload, manifest_hash = _manifest_payload(manifest)
    build_id = connection.execute(
        """
        INSERT INTO source_catalog_builds (
            schema_version, manifest_path, manifest_hash, status, started_at
        ) VALUES (?, ?, ?, 'running', ?)
        """,
        (
            CATALOG_SCHEMA_VERSION,
            str(manifest.resolve()) if manifest.exists() else None,
            manifest_hash,
            now,
        ),
    ).lastrowid
    connection.commit()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE source_catalog_documents SET active=0, updated_at=?
            WHERE discovery_method=?
            """,
            (now, DISCOVERY_METHOD),
        )
        connection.execute(
            """
            DELETE FROM source_catalog_group_members
            WHERE provenance=? OR provenance LIKE 'manifest:%'
            """,
            (AUTO_PROVENANCE,),
        )
        connection.execute(
            """
            DELETE FROM source_catalog_relations
            WHERE provenance=? OR provenance LIKE 'manifest:%'
            """,
            (AUTO_PROVENANCE,),
        )
        connection.execute(
            """
            DELETE FROM source_catalog_groups
            WHERE provenance=? OR provenance LIKE 'manifest:%'
            """,
            (AUTO_PROVENANCE,),
        )

        documents = _discover_documents(connection)
        for document in documents:
            _upsert_document(connection, document, now)
        mapped = _rebuild_record_map(connection, documents, now)
        _build_automatic_structure(connection, documents, now)
        _apply_manifest(connection, documents, manifest_payload, manifest, now)
        version = _catalog_version(connection, manifest_hash)
        connection.execute(
            """
            INSERT INTO source_catalog_state(key, value, updated_at)
            VALUES ('catalog_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (version, now),
        )
        counts = {
            "documents": connection.execute(
                "SELECT COUNT(*) FROM source_catalog_documents WHERE active=1"
            ).fetchone()[0],
            "mapped_records": connection.execute(
                "SELECT COUNT(*) FROM source_catalog_record_map"
            ).fetchone()[0],
            "groups": connection.execute(
                "SELECT COUNT(*) FROM source_catalog_groups WHERE active=1"
            ).fetchone()[0],
            "relations": connection.execute(
                "SELECT COUNT(*) FROM source_catalog_relations WHERE active=1"
            ).fetchone()[0],
        }
        completed = _now_iso()
        connection.execute(
            """
            UPDATE source_catalog_builds SET
                catalog_version=?, documents=?, mapped_records=?, groups=?,
                relations=?, status='completed', completed_at=?
            WHERE build_id=?
            """,
            (
                version, counts["documents"], counts["mapped_records"],
                counts["groups"], counts["relations"], completed, build_id,
            ),
        )
        connection.commit()
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_version": version,
            "metadata_db": str(db_path.resolve()),
            "manifest_path": str(manifest.resolve()) if manifest.exists() else None,
            "manifest_hash": manifest_hash,
            **counts,
            "status": "completed",
        }
    except Exception as exc:
        connection.rollback()
        connection.execute(
            """
            UPDATE source_catalog_builds
            SET status='failed', error=?, completed_at=?
            WHERE build_id=?
            """,
            (f"{type(exc).__name__}: {exc}", _now_iso(), build_id),
        )
        connection.commit()
        raise
    finally:
        connection.close()


def _language_coverage(connection: sqlite3.Connection) -> dict[str, Any]:
    """Explain language coverage using metadata and a conservative script signal."""
    tables = _table_names(connection)
    if "chunks" not in tables:
        return {
            "available": False,
            "reason": "chunks_table_missing",
            "no_hit_is_not_absence": True,
        }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
    language_expr = "language" if "language" in columns else "''"
    text_expr = "chunk_text" if "chunk_text" in columns else "''"
    source_type_expr = "source_type" if "source_type" in columns else "''"
    raw_counts = connection.execute(
        f"""
        SELECT NULLIF(TRIM({language_expr}), '') AS language, COUNT(*) AS records
        FROM chunks GROUP BY NULLIF(TRIM({language_expr}), '')
        ORDER BY records DESC, language
        """
    ).fetchall()
    metadata_counts: dict[str, int] = {}
    for row in raw_counts:
        label = _normalize_language(row["language"])
        metadata_counts[label] = metadata_counts.get(label, 0) + int(row["records"])

    script_clause = (
        f"({text_expr} GLOB '*[一-龥]*' OR {text_expr} GLOB '*[㐀-鿿]*')"
    )
    try:
        script_records = int(
            connection.execute(
                f"SELECT COUNT(*) FROM chunks WHERE {script_clause}"
            ).fetchone()[0]
        )
        mixed_script_records = int(
            connection.execute(
                f"SELECT COUNT(*) FROM chunks WHERE {script_clause} "
                f"AND {text_expr} GLOB '*[A-Za-z]*'"
            ).fetchone()[0]
        )
        script_by_type = {
            str(row["source_type"] or "unknown"): int(row["records"])
            for row in connection.execute(
                f"""
                SELECT COALESCE({source_type_expr}, 'unknown') AS source_type,
                       COUNT(*) AS records
                FROM chunks WHERE {script_clause}
                GROUP BY COALESCE({source_type_expr}, 'unknown')
                ORDER BY source_type
                """
            )
        }
    except sqlite3.Error:
        script_records = 0
        mixed_script_records = 0
        script_by_type = {}

    declared_chinese = sum(
        count for label, count in metadata_counts.items()
        if _language_is_chinese(label)
    )
    if declared_chinese or script_records:
        status = "chinese_records_detected"
    else:
        status = "no_chinese_metadata_or_script_signal"
    return {
        "available": True,
        "indexed_record_count": sum(metadata_counts.values()),
        "metadata_language_counts": metadata_counts,
        "declared_chinese_records": declared_chinese,
        "script_signal_chinese_records": script_records,
        "script_signal_mixed_language_records": mixed_script_records,
        "script_signal_by_text_type": script_by_type,
        "status": status,
        "basis": [
            "chunks.language metadata, with blank values reported as unknown",
            "conservative CJK character signal in indexed chunk_text",
        ],
        "limitations": [
            "The script signal is diagnostic and does not classify every language.",
            "The indexed record count is not a corpus completeness measure.",
            "No hit or no script signal does not prove that the corpus is absent.",
        ],
        "completeness": {
            "status": "not_assessed",
            "is_complete": False,
            "basis": None,
        },
        "no_hit_is_not_absence": True,
    }


def catalog_status(metadata_db: str | Path | None = None) -> dict[str, Any]:
    db_path = _resolve_db(metadata_db)
    if not db_path.exists():
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "available": False,
            "build_required": True,
            "reason": "metadata_db_missing",
        }
    connection = _connect(db_path, readonly=True)
    try:
        tables = _table_names(connection)
        if "source_catalog_documents" not in tables:
            return {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "available": False,
                "build_required": True,
                "reason": "catalog_tables_missing",
            }
        chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        mapped = int(
            connection.execute(
                "SELECT COUNT(*) FROM source_catalog_record_map"
            ).fetchone()[0]
        )
        version_row = (
            connection.execute(
                "SELECT value FROM source_catalog_state WHERE key='catalog_version'"
            ).fetchone()
            if "source_catalog_state" in tables else None
        )
        last_build = connection.execute(
            """
            SELECT build_id, schema_version, status, started_at, completed_at, error
            FROM source_catalog_builds ORDER BY build_id DESC LIMIT 1
            """
        ).fetchone()
        schema_outdated = bool(
            last_build and str(last_build["schema_version"] or "") != CATALOG_SCHEMA_VERSION
        )
        relation_counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """
                SELECT predicate, COUNT(*) FROM source_catalog_relations
                WHERE active=1 GROUP BY predicate ORDER BY predicate
                """
            )
        }
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "available": True,
            "catalog_version": version_row[0] if version_row else None,
            "documents": int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_catalog_documents WHERE active=1"
                ).fetchone()[0]
            ),
            "mapped_records": mapped,
            "chunk_records": chunks,
            "unmapped_records": max(0, chunks - mapped),
            "groups": int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_catalog_groups WHERE active=1"
                ).fetchone()[0]
            ),
            "relations": int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_catalog_relations WHERE active=1"
                ).fetchone()[0]
            ),
            "relation_counts": relation_counts,
            "language_coverage": _language_coverage(connection),
            "schema_outdated": schema_outdated,
            "build_required": chunks != mapped or not version_row or schema_outdated,
            "last_build": dict(last_build) if last_build else None,
        }
    finally:
        connection.close()


def _document_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    metadata = data.pop("metadata_json", "{}")
    try:
        data["metadata"] = json.loads(metadata or "{}")
    except json.JSONDecodeError:
        data["metadata"] = {"parse_error": True}
    data["language"] = _normalize_language(data.get("language"))
    data.setdefault("metadata_basis", [
        "source_catalog_documents derived from indexed chunks metadata",
        "language is unknown when the indexed metadata is blank",
    ])
    data["active"] = bool(data.get("active", 0))
    return data


def _filter_values(value: str | Iterable[str] | None) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return [_normalize(item) for item in values if _normalize(item)]


def _attach_version_groups(
    connection: sqlite3.Connection,
    documents: list[dict[str, Any]],
) -> None:
    if not documents or "source_catalog_group_members" not in _table_names(connection):
        return
    source_ids = [str(item["source_id"]) for item in documents if item.get("source_id")]
    if not source_ids:
        return
    placeholders = ",".join("?" for _ in source_ids)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = connection.execute(
        f"""
        SELECT m.source_id, g.group_id, g.group_type, g.canonical_title,
               g.description, m.member_role, m.sequence_no, m.date_start,
               m.date_end, m.provenance
        FROM source_catalog_group_members m
        JOIN source_catalog_groups g ON g.group_id=m.group_id
        WHERE m.source_id IN ({placeholders}) AND g.active=1
        ORDER BY g.group_type, g.group_id
        """,
        source_ids,
    ).fetchall()
    for row in rows:
        item = dict(row)
        source_id = str(item.pop("source_id"))
        groups[source_id].append(item)
    for document in documents:
        document["version_groups"] = groups.get(str(document.get("source_id")), [])


def list_source_documents(
    metadata_db: str | Path | None = None,
    *,
    abteilung: str | None = None,
    band: str | None = None,
    collection: str | None = None,
    text_type: str | None = None,
    language: str | Iterable[str] | None = None,
    work: str | Iterable[str] | None = None,
    version: str | Iterable[str] | None = None,
    limit: int = 50,
    page: int = 1,
    page_size: int | None = None,
) -> dict[str, Any]:
    db_path = _resolve_db(metadata_db)
    connection = _connect(db_path, readonly=True)
    try:
        tables = _table_names(connection)
        if "source_catalog_documents" not in tables:
            bounded_page_size = max(1, min(int(page_size or limit), 200))
            return {
                "sources": [], "count": 0, "total_count": 0,
                "catalog_available": False, "page": 1,
                "page_size": bounded_page_size, "has_next": False,
            }
        where = ["active=1"]
        params: list[Any] = []
        if abteilung not in (None, ""):
            where.append("LOWER(mega_abteilung)=LOWER(?)")
            params.append(str(abteilung))
        if band not in (None, ""):
            band_text = str(band)
            if "." in band_text:
                where.append("LOWER(band)=LOWER(?)")
                params.append(band_text)
            else:
                where.append("(LOWER(band)=LOWER(?) OR LOWER(band) LIKE LOWER(?))")
                params.extend([band_text, band_text + ".%"])
        for field, value in (("source_collection", collection), ("text_type", text_type)):
            if value not in (None, ""):
                where.append(f"LOWER({field})=LOWER(?)")
                params.append(str(value))

        language_values = _filter_values(language)
        if language_values:
            clauses = []
            for value in language_values:
                if _key(value) == UNKNOWN_LANGUAGE:
                    clauses.append("LOWER(COALESCE(language, '')) IN ('', 'unknown')")
                else:
                    clauses.append("LOWER(language)=LOWER(?)")
                    params.append(value)
            where.append("(" + " OR ".join(clauses) + ")")

        work_values = _filter_values(work)
        if work_values:
            clauses = []
            for value in work_values:
                pattern = f"%{value}%"
                item_clauses = [
                    "LOWER(COALESCE(title, '')) LIKE LOWER(?)",
                    "LOWER(COALESCE(source_doc, '')) LIKE LOWER(?)",
                    "LOWER(COALESCE(source_file, '')) LIKE LOWER(?)",
                ]
                params.extend([pattern, pattern, pattern])
                if "source_catalog_group_members" in tables:
                    item_clauses.append(
                        """EXISTS (
                            SELECT 1
                            FROM source_catalog_group_members gm
                            JOIN source_catalog_groups gg ON gg.group_id=gm.group_id
                            WHERE gm.source_id=source_catalog_documents.source_id
                              AND gg.active=1
                              AND LOWER(COALESCE(gg.canonical_title, '')) LIKE LOWER(?)
                        )"""
                    )
                    params.append(pattern)
                clauses.append("(" + " OR ".join(item_clauses) + ")")
            where.append("(" + " OR ".join(clauses) + ")")

        version_values = _filter_values(version)
        if version_values:
            clauses = []
            for value in version_values:
                pattern = f"%{value}%"
                item_clauses = [
                    "LOWER(COALESCE(edition_status, '')) LIKE LOWER(?)",
                    "LOWER(COALESCE(title, '')) LIKE LOWER(?)",
                    "LOWER(COALESCE(source_doc, '')) LIKE LOWER(?)",
                ]
                params.extend([pattern, pattern, pattern])
                if "source_catalog_group_members" in tables:
                    item_clauses.append(
                        """EXISTS (
                            SELECT 1
                            FROM source_catalog_group_members gm
                            JOIN source_catalog_groups gg ON gg.group_id=gm.group_id
                            WHERE gm.source_id=source_catalog_documents.source_id
                              AND gg.active=1
                              AND (
                                  LOWER(COALESCE(gg.canonical_title, '')) LIKE LOWER(?)
                                  OR LOWER(COALESCE(gm.member_role, '')) LIKE LOWER(?)
                              )
                        )"""
                    )
                    params.extend([pattern, pattern])
                clauses.append("(" + " OR ".join(item_clauses) + ")")
            where.append("(" + " OR ".join(clauses) + ")")

        where_sql = " AND ".join(where)
        total_count = int(
            connection.execute(
                f"SELECT COUNT(*) FROM source_catalog_documents WHERE {where_sql}",
                params,
            ).fetchone()[0]
        )
        bounded_page = max(1, int(page))
        bounded_page_size = max(1, min(int(page_size or limit), 200))
        offset = (bounded_page - 1) * bounded_page_size
        query_params = [*params, bounded_page_size, offset]
        rows = connection.execute(
            f"""
            SELECT * FROM source_catalog_documents
            WHERE {where_sql}
            ORDER BY mega_abteilung, band, text_type, authority_rank DESC, source_id
            LIMIT ? OFFSET ?
            """,
            query_params,
        ).fetchall()
        sources = [_document_payload(row) for row in rows]
        _attach_version_groups(connection, sources)
        version_row = (
            connection.execute(
                "SELECT value FROM source_catalog_state WHERE key='catalog_version'"
            ).fetchone()
            if "source_catalog_state" in tables else None
        )
        return {
            "sources": sources,
            "count": len(sources),
            "total_count": total_count,
            "catalog_available": True,
            "catalog_version": version_row[0] if version_row else None,
            "limit": bounded_page_size,
            "page": bounded_page,
            "page_size": bounded_page_size,
            "offset": offset,
            "has_next": offset + len(sources) < total_count,
            "filters": {
                "abteilung": abteilung,
                "band": band,
                "collection": collection,
                "text_type": text_type,
                "language": language_values,
                "work": work_values,
                "version": version_values,
            },
        }
    finally:
        connection.close()


def coverage_report(
    metadata_db: str | Path | None = None,
    *,
    abteilung: str | None = None,
    band: str | None = None,
    collection: str | None = None,
    text_type: str | None = None,
    language: str | Iterable[str] | None = None,
    work: str | Iterable[str] | None = None,
    version: str | Iterable[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Return bounded, evidence-labelled coverage from the existing catalog."""
    listing = list_source_documents(
        metadata_db,
        abteilung=abteilung,
        band=band,
        collection=collection,
        text_type=text_type,
        language=language,
        work=work,
        version=version,
        page=page,
        page_size=page_size,
    )
    if not listing.get("catalog_available"):
        return {
            "catalog_available": False,
            "filters": listing.get("filters", {}),
            "rows": [],
            "summary": {
                "indexed_record_count": 0,
                "source_unit_count": 0,
                "volume_group_count": 0,
                "historical_work_count": None,
                "historical_work_count_basis": "not_derived_from_source_units",
                "scope": "returned_page",
            },
            "coverage_limitations": [
                "The source catalog is not built; no coverage claim is made.",
                "No result is not evidence that the corpus is absent.",
            ],
            "pagination": {
                "page": listing.get("page", 1),
                "page_size": listing.get("page_size", page_size),
                "total_rows": 0,
                "has_next": False,
            },
        }

    rows = []
    for source in listing.get("sources", []):
        groups = [
            group for group in source.get("version_groups", [])
            if group.get("group_type") != "mega_volume"
        ]
        curated = [group for group in groups if group.get("canonical_title")]
        if curated:
            work_identity = {
                "label": curated[0]["canonical_title"],
                "basis": "curated_source_catalog_group",
                "group_ids": [group.get("group_id") for group in curated],
            }
        elif source.get("title"):
            work_identity = {
                "label": source.get("title"),
                "basis": "chunks.source_title",
                "group_ids": [],
            }
        else:
            work_identity = {
                "label": None,
                "basis": "work_identity_not_recorded",
                "group_ids": [],
            }

        known_gaps = []
        if source.get("language") == UNKNOWN_LANGUAGE:
            known_gaps.append("language_metadata_unknown")
        elif source.get("language") == MIXED_LANGUAGE:
            known_gaps.append("language_metadata_mixed")
        if not source.get("title") and not curated:
            known_gaps.append("work_identity_not_recorded")
        if not source.get("record_count"):
            known_gaps.append("no_indexed_records_for_source_unit")
        known_gaps.append("completeness_not_assessed")

        rows.append({
            "source_id": source.get("source_id"),
            "work": work_identity,
            "version": {
                "edition_status": source.get("edition_status"),
                "source_document_kind": source.get("document_kind"),
                "groups": groups,
            },
            "volume": {
                "abteilung": source.get("mega_abteilung"),
                "band": source.get("band"),
                "volume_group": source.get("volume_group"),
            },
            "language": source.get("language", UNKNOWN_LANGUAGE),
            "language_values": source.get("metadata", {}).get(
                "language_values", [source.get("language", UNKNOWN_LANGUAGE)]
            ),
            "text_type": source.get("text_type") or UNKNOWN_LANGUAGE,
            "record_count": int(source.get("record_count") or 0),
            "source_unit": {
                "collection": source.get("source_collection"),
                "source_key": source.get("source_key"),
                "source_file": source.get("source_file"),
                "source_doc": source.get("source_doc"),
                "source_part": source.get("source_part"),
            },
            "locator_range": {
                "min_page": source.get("min_page"),
                "max_page": source.get("max_page"),
            },
            "known_coverage": {
                "indexed_records": int(source.get("record_count") or 0),
                "coverage_basis": "source_catalog_documents.record_count from indexed chunks",
                "completeness": {
                    "status": "not_assessed",
                    "is_complete": False,
                    "basis": None,
                },
            },
            "known_gaps": known_gaps,
            "metadata_basis": [
                "chunks.source_title/source_doc/source_part",
                "chunks.mega_abteilung/band/source_type",
                "chunks.language; blank values remain unknown",
                "source_catalog.yaml only where a curated group is present",
            ],
        })

    volume_groups = {
        row["volume"].get("volume_group")
        for row in rows if row["volume"].get("volume_group")
    }
    return {
        "catalog_available": True,
        "catalog_version": listing.get("catalog_version"),
        "filters": listing.get("filters", {}),
        "rows": rows,
        "summary": {
            "indexed_record_count": sum(row["record_count"] for row in rows),
            "source_unit_count": len(rows),
            "volume_group_count": len(volume_groups),
            "historical_work_count": None,
            "historical_work_count_basis": (
                "not_derived_from_source_units_or_volume_groups"
            ),
            "scope": "returned_page",
        },
        "coverage_limitations": [
            "A source unit is a retrieval carrier, not automatically one historical work or witness.",
            "File, source-unit, and volume counts are not historical-work counts.",
            "No completeness basis is recorded; rows are indexed-record coverage only.",
            "No hit is not evidence that the requested corpus is absent.",
        ],
        "pagination": {
            "page": listing.get("page", 1),
            "page_size": listing.get("page_size", page_size),
            "total_rows": listing.get("total_count", len(rows)),
            "has_next": listing.get("has_next", False),
        },
    }


def get_source_context(
    source_id: str, metadata_db: str | Path | None = None
) -> dict[str, Any] | None:
    db_path = _resolve_db(metadata_db)
    connection = _connect(db_path, readonly=True)
    try:
        if "source_catalog_documents" not in _table_names(connection):
            return None
        document = connection.execute(
            """
            SELECT * FROM source_catalog_documents
            WHERE source_id=? AND active=1
            """,
            (source_id,),
        ).fetchone()
        if not document:
            return None
        groups = [
            dict(row)
            for row in connection.execute(
                """
                SELECT g.group_id, g.group_type, g.canonical_title, g.description,
                       m.member_role, m.sequence_no, m.date_start, m.date_end,
                       m.provenance
                FROM source_catalog_group_members m
                JOIN source_catalog_groups g ON g.group_id=m.group_id
                WHERE m.source_id=? AND g.active=1
                ORDER BY g.group_type, g.group_id
                """,
                (source_id,),
            )
        ]
        relations = []
        rows = connection.execute(
            """
            SELECT r.*, d.source_id AS related_source_id,
                   d.title AS related_title, d.mega_abteilung AS related_abteilung,
                   d.band AS related_band, d.text_type AS related_text_type,
                   d.edition_status AS related_edition_status,
                   CASE WHEN r.subject_source_id=? THEN 'outgoing' ELSE 'incoming' END
                       AS direction
            FROM source_catalog_relations r
            JOIN source_catalog_documents d
              ON d.source_id=CASE
                    WHEN r.subject_source_id=? THEN r.object_source_id
                    ELSE r.subject_source_id
                 END
            WHERE r.active=1
              AND (r.subject_source_id=? OR r.object_source_id=?)
            ORDER BY r.predicate, related_source_id
            """,
            (source_id, source_id, source_id, source_id),
        ).fetchall()
        for row in rows:
            item = dict(row)
            for field in (
                "subject_source_id", "object_source_id", "active",
                "created_at", "updated_at",
            ):
                item.pop(field, None)
            relations.append(item)
        version_row = connection.execute(
            "SELECT value FROM source_catalog_state WHERE key='catalog_version'"
        ).fetchone()
        return {
            "catalog_version": version_row[0] if version_row else None,
            "document": _document_payload(document),
            "groups": groups,
            "relations": relations,
        }
    finally:
        connection.close()


def hydrate_records_with_source_catalog(
    records: list[dict[str, Any]],
    metadata_db: str | Path | None = None,
) -> dict[str, Any]:
    """Attach source identity to retrieval records without changing their ranking."""
    page_ids = list(
        dict.fromkeys(
            str(record.get("page_id") or record.get("id") or "")
            for record in records
            if record.get("page_id") or record.get("id")
        )
    )
    if not page_ids:
        return {"linked": 0, "catalog_available": False}
    db_path = _resolve_db(metadata_db)
    connection = _connect(db_path, readonly=True)
    try:
        tables = _table_names(connection)
        if "source_catalog_record_map" not in tables:
            return {"linked": 0, "catalog_available": False}
        placeholders = ",".join("?" for _ in page_ids)
        rows = connection.execute(
            f"""
            SELECT m.record_id, d.*
            FROM source_catalog_record_map m
            JOIN source_catalog_documents d ON d.source_id=m.source_id
            WHERE m.record_id IN ({placeholders}) AND d.active=1
            """,
            page_ids,
        ).fetchall()
        documents = {str(row["record_id"]): _document_payload(row) for row in rows}
        source_ids = list(
            dict.fromkeys(
                item["source_id"] for item in documents.values() if item.get("source_id")
            )
        )
        groups_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relations_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if source_ids:
            source_placeholders = ",".join("?" for _ in source_ids)
            for row in connection.execute(
                f"""
                SELECT m.source_id, g.group_id, g.group_type, g.canonical_title,
                       m.member_role, m.sequence_no, m.date_start, m.date_end
                FROM source_catalog_group_members m
                JOIN source_catalog_groups g ON g.group_id=m.group_id
                WHERE m.source_id IN ({source_placeholders}) AND g.active=1
                ORDER BY g.group_type, g.group_id
                """,
                source_ids,
            ):
                groups_by_source[str(row["source_id"])].append(
                    {key: row[key] for key in row.keys() if key != "source_id"}
                )
            relation_rows = connection.execute(
                f"""
                SELECT r.*, ds.title AS subject_title, do.title AS object_title,
                       ds.edition_status AS subject_edition_status,
                       do.edition_status AS object_edition_status
                FROM source_catalog_relations r
                JOIN source_catalog_documents ds ON ds.source_id=r.subject_source_id
                JOIN source_catalog_documents do ON do.source_id=r.object_source_id
                WHERE r.active=1 AND (
                    r.subject_source_id IN ({source_placeholders})
                    OR r.object_source_id IN ({source_placeholders})
                )
                ORDER BY r.predicate, r.relation_id
                """,
                [*source_ids, *source_ids],
            ).fetchall()
            wanted = set(source_ids)
            for row in relation_rows:
                data = dict(row)
                subject = str(data["subject_source_id"])
                obj = str(data["object_source_id"])
                if subject in wanted:
                    relations_by_source[subject].append(
                        {
                            "relation_id": data["relation_id"],
                            "direction": "outgoing",
                            "predicate": data["predicate"],
                            "related_source_id": obj,
                            "related_title": data["object_title"],
                            "related_edition_status": data["object_edition_status"],
                            "scope": data["relation_scope"],
                            "confidence": data["confidence"],
                            "provenance": data["provenance"],
                            "note": data["note"],
                        }
                    )
                if obj in wanted:
                    relations_by_source[obj].append(
                        {
                            "relation_id": data["relation_id"],
                            "direction": "incoming",
                            "predicate": data["predicate"],
                            "related_source_id": subject,
                            "related_title": data["subject_title"],
                            "related_edition_status": data["subject_edition_status"],
                            "scope": data["relation_scope"],
                            "confidence": data["confidence"],
                            "provenance": data["provenance"],
                            "note": data["note"],
                        }
                    )
        version_row = connection.execute(
            "SELECT value FROM source_catalog_state WHERE key='catalog_version'"
        ).fetchone()
        version = version_row[0] if version_row else None
        linked = 0
        for record in records:
            page_id = str(record.get("page_id") or record.get("id") or "")
            document = documents.get(page_id)
            if not document:
                continue
            source_id = str(document["source_id"])
            record["_source_catalog"] = {
                "catalog_linked": True,
                "catalog_version": version,
                "source_id": source_id,
                "document_kind": document.get("document_kind"),
                "edition_status": document.get("edition_status"),
                "authority_rank": document.get("authority_rank"),
                "volume_group": document.get("volume_group"),
                "date_start": document.get("date_start"),
                "date_end": document.get("date_end"),
                "record_count": document.get("record_count"),
                "groups": groups_by_source.get(source_id, []),
                "relations": relations_by_source.get(source_id, []),
            }
            linked += 1
        return {
            "linked": linked,
            "requested": len(page_ids),
            "catalog_available": True,
            "catalog_version": version,
        }
    finally:
        connection.close()


def refresh_source_catalog_if_needed(
    metadata_db: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Refresh derived tables only when chunk coverage has changed."""
    status = catalog_status(metadata_db)
    if not status.get("build_required", True):
        return {"refreshed": False, **status}
    result = build_source_catalog(metadata_db, manifest_path)
    return {"refreshed": True, **result}


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("build", "status", "list", "coverage", "show")
    )
    parser.add_argument("source_id", nargs="?")
    parser.add_argument("--metadata-db")
    parser.add_argument("--manifest")
    parser.add_argument("--abteilung")
    parser.add_argument("--band")
    parser.add_argument("--collection")
    parser.add_argument("--text-type")
    parser.add_argument("--language")
    parser.add_argument("--work")
    parser.add_argument("--version")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int)
    args = parser.parse_args()
    if args.command == "build":
        output = build_source_catalog(args.metadata_db, args.manifest)
    elif args.command == "status":
        output = catalog_status(args.metadata_db)
    elif args.command == "list":
        output = list_source_documents(
            args.metadata_db,
            abteilung=args.abteilung,
            band=args.band,
            collection=args.collection,
            text_type=args.text_type,
            language=args.language,
            work=args.work,
            version=args.version,
            limit=args.limit,
            page=args.page,
            page_size=args.page_size,
        )
    elif args.command == "coverage":
        output = coverage_report(
            args.metadata_db,
            abteilung=args.abteilung,
            band=args.band,
            collection=args.collection,
            text_type=args.text_type,
            language=args.language,
            work=args.work,
            version=args.version,
            page=args.page,
            page_size=args.page_size or args.limit,
        )
    else:
        if not args.source_id:
            parser.error("show requires source_id")
        output = get_source_context(args.source_id, args.metadata_db)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
