# MEGA Source Catalog and Version Relations

## Purpose

The source catalog separates three things that must not be collapsed:

1. a retrieval carrier, such as an OCR Textband folder or a MEGAdigital XML file;
2. its MEGA volume and TEXT/APPARAT scope;
3. a declared textual-history group, such as two print editions of the same work.

The catalog is derived alongside the existing index. It does not replace or
rewrite `chunks`, FTS5, passages, or LanceDB vectors.

## Tables

`source_catalog_documents`

- one stable `source_id` per OCR source folder or MEGAdigital source document;
- collection, quality, MEGA volume, TEXT/APPARAT, title and locator metadata;
- conservative `document_kind`, `edition_status`, date range and authority rank;
- record counts and a warning that a retrieval carrier is not automatically one
  historical textual witness.

`source_catalog_record_map`

- maps each `chunks.id` to exactly one catalog source;
- uses `source_file` for OCR and `source_doc + source_part` for MEGAdigital;
- is derived and can be rebuilt without changing indexed text.

`source_catalog_groups` and `source_catalog_group_members`

- automatic `mega_volume` groups collect retrieval carriers assigned to the
  same parent volume;
- curated `work_versions`, `textual_stages`, and `multipart_edition` groups are
  read from `source_catalog.yaml`;
- group membership is source-level metadata, not proof that passages are
  textually identical.

`source_catalog_relations`

- automatic `apparatus_for` connects same-collection APPARAT and TEXT carriers;
- automatic `digital_parallel_to_ocr` connects MEGAdigital and OCR coverage of
  the same parent MEGA volume;
- curated relations such as `earlier_print_edition_of_same_work` are declared
  in YAML with confidence, provenance and an explicit scope note.

## Build and inspect

```powershell
python mega_agent.py source-catalog-build
python mega_agent.py source-catalog-status
python mega_agent.py source-catalog-list --abteilung II --band 5
python mega_agent.py source-catalog-list --work "Das Kapital" --version 1890 --page 1 --page-size 20
python mega_agent.py source-catalog-coverage --language unknown --text-type APPARAT --page-size 20
python mega_agent.py source-catalog-show SOURCE_ID
```

The build is idempotent. It updates only tables prefixed with
`source_catalog_`. Existing OCR, FTS and vector records remain unchanged.
Normal `build_index.py --build` and non-dry-run `import_megadigital.py`
operations refresh the catalog only when chunk coverage has changed. Use
`--skip-source-catalog` for maintenance runs that must defer this derived
refresh.

`source-catalog-build --manifest PATH` accepts another reviewed manifest. The
code supports selectors by collection, source document, source file, MEGA
section, band and TEXT/APPARAT type.

## Coverage semantics and bounded listing

The catalog schema is `mega-source-catalog-v2`. `source-catalog-list` retains
the old `sources`, `count` and `limit` fields and adds filters for `language`,
`work` and `version`, plus `page`, `page_size`, `total_count` and `has_next`.
Work and version filters use recorded title/source-document fields and curated
version groups when available; they do not invent a historical work identity.

`source-catalog-coverage` is a read-only view over the same derived catalog. A
row reports the work identity and basis, version/edition metadata, MEGA
volume, language, TEXT/APPARAT, indexed record count, locator range, known
coverage, known gaps and metadata basis. File, source-unit and volume counts
are explicitly not historical-work counts. Completeness is always
`not_assessed` unless an independent completeness basis is recorded.

Language aggregation is conservative: blank values are `unknown`, more than
one declared language is `mixed`, and a known language combined with blank
records is also `mixed`. A no-hit result is labelled as no match in the
indexed scope; it is not an absence claim.

The catalog adds only derived `source_catalog_*` tables. The coverage query
does not rebuild the catalog, source text, FTS, vectors or the real metadata
database.

## Evidence package contract

When the catalog is available, every linked evidence item contains:

```json
{
  "source_identity": {
    "catalog_linked": true,
    "catalog_version": "sc_...",
    "source_id": "src_...",
    "document_kind": "structured_critical_text",
    "edition_status": "print_edition",
    "volume_group": "II/5",
    "groups": [],
    "relations": []
  }
}
```

Agent index responses retain a compact subset: source ID, catalog version,
document kind, volume group, version groups and relation targets. Full evidence
retains confidence, provenance and relation notes.

The catalog does not change ranking. It supplies identity and interpretation
boundaries after retrieval.

## Research rules

- Preserve `source_id` and `edition_status` in notes and claim records.
- Do not merge manuscript, editorial manuscript and print evidence into one
  undifferentiated quotation.
- `digital_parallel_to_ocr` means same volume coverage, not page alignment.
- A source-level relation is navigation for comparison. Verify passages
  directly before claiming textual dependence or wording change.
- APPARAT remains editorial evidence even when it belongs to a version group.
- Add new works or versions in `source_catalog.yaml`; do not encode them as
  query-specific Python conditions.
