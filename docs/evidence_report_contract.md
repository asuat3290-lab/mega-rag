# Evidence Qualification And Report Contract

## Why the contract exists

A high-ranked page is not automatically evidence. The workbench now separates four stages that were previously easy for an external agent to collapse:

1. **Recall candidate**: a page was returned by lexical/vector/register retrieval.
2. **Semantic evidence**: a complete concept phrase, a bounded lexical group, or a declared structural relation matched.
3. **Provenance-eligible evidence**: the source layer fits the query intent, such as TEXT for an author-argument question or APPARAT for an editorial question.
4. **Citation-ready evidence**: authoritative digital author/editorial text has a verified page label and can be quoted from a full snippet.

`retrieval.debug.adequacy.axes` reports semantic, provenance, and citation status separately. This prevents a relevant but unverified OCR page from being described as “no evidence”, while also preventing it from silently becoming a formal quotation.

## Glossary contract

Structured glossary entries preserve phrases and senses:

```yaml
术语:
  type: concept
  aliases: ["查询别名"]
  de:
    - "vollständiger deutscher Ausdruck"
  recall:
    - "breiter Navigationsausdruck"
  related:
    - "相关但不等同的概念"
```

The query planner creates two different term families:

- `de` contains phrase-preserving equivalents that may qualify evidence;
- `recall` contains broader navigation terms that can retrieve candidates but cannot, by themselves, prove that the concept occurs;
- qualification groups preserve phrases or require multiple lexical terms within a bounded window.

Concepts occurring only inside a detected work title are demoted to recall context. For example, a title containing a concept word does not make that word the research subject; the concept becomes core again only when it appears outside the title span.

Legacy string entries remain readable, but are recall-only and make `plan_status.valid_for_evidence=false` until migrated. `python glossary_loader.py` reports every remaining legacy entry.

## Package gate

Every package contains:

- `artifact_type`: `research_evidence_package` or `diagnostic_candidate_package`;
- `synthesis_gate.synthesis_allowed`;
- `summary.candidate_count`;
- `summary.qualified_evidence_count`;
- `summary.citation_ready_count`.

A diagnostic package can be saved for debugging, but agents must not synthesize a positive answer from it. The standalone `research_export.py` command exits with code 3 when the gate is closed.

## Stable evidence identity

`E001` is package-local. Every item also has:

- `evidence_uid`: stable across packages and query order;
- `package_evidence_ref`: unambiguous within one package/run;
- `evidence_role`: qualified evidence or provisional candidate.

Agents must use `evidence_uid` when merging searches. The persistent evidence library uses the same identity algorithm.

## Structured report validation

Agent reports should use a JSON `claims` list. Validate it before prose generation:

```powershell
python mega_agent.py report-check report.json research_run_xxx.json
```

The validator rejects a closed source synthesis gate, unknown/duplicated evidence identities, textual claims that contain any unqualified evidence reference, non-verbatim quotations, and quotations from records that are not quote-eligible. Contemporary empirical claims without external sources remain valid only as explicitly warned hypotheses.

The validation command uses no API tokens and returns exit code 3 on failure.
