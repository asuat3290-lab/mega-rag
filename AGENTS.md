# MEGA Research Workbench Agent Contract

This repository is a philological research tool, not a generic text-generation project. Agents must preserve the distinction between retrieval candidates, qualified evidence, inference, and external empirical claims.

## Mandatory flow

1. Use `python mega_agent.py research-plan "QUESTION"` for compound, diachronic, comparative, or contemporary-application questions.
2. Use `python mega_agent.py research-run "QUESTION" --detail index --save` for final research retrieval. Plain `search` is a diagnostic lookup, not a substitute for a multi-branch run.
3. Inspect `synthesis_gate` before drafting. When `synthesis_allowed=false`, repair the plan/retrieval; do not write a positive answer from the candidates.
4. Expand only selected records with `evidence --detail snippet|full` before quoting.
5. Use `evidence_uid` across searches and packages. `E001` style IDs are local to one package/run and must never be merged without remapping.
6. Draft claims as structured JSON and run `python mega_agent.py report-check REPORT.json SOURCE.json` before producing prose.

## Claim contract

Each claim must contain:

```json
{
  "claim_id": "C001",
  "claim_type": "text_supported | text_supported_qualification | philological_finding | theoretical_inference | comparative_inference | corpus_level_claim | empirical_hypothesis | research_claim",
  "text": "...",
  "evidence_refs": ["ev_..."],
  "external_evidence_refs": [],
  "quotes": [
    {"evidence_ref": "ev_...", "text": "exact German source text"}
  ]
}
```

- `text_supported` claims require qualified MEGA evidence.
- `theoretical_inference` must be labelled as interpretation, not Marx's literal statement.
- `empirical_hypothesis` about present conditions requires external evidence; MEGA alone cannot establish it.
- Quotes require `quote_eligible=true` and must be exact substrings of the retrieved German context.
- APPARAT/editorial wording must be attributed to editors.
- Unclassified OCR Textband may guide interpretation but needs source verification before formal attribution or citation.

## Cost discipline

Use local planning, term probe, Sachregister navigation, and index previews first. Call an external planning model only for unresolved terminology. Send only selected snippets to a synthesis model, and cache by query, plan, index version, and model.

## Source identity contract

- Preserve `source_id`, `edition_status`, and version-group membership in every cross-version analysis.
- Use `source-catalog-show SOURCE_ID` before claiming that two results are manuscript/print stages or editions of one work.
- `digital_parallel_to_ocr` and `apparatus_for` are source-level navigation relations, not passage alignment.
- Never merge wording from manuscript, editorial manuscript, print edition, and APPARAT into one undifferentiated quotation.
- Add reviewed textual-history relations to `source_catalog.yaml`; do not create query-specific source rules.
## Advisory focus and process tracing

- For a flat multi-term lookup, pass `--focus` for concepts that must occur in qualified evidence, `--context` for recall-only terms, and `--volumes` for soft source scope.
- Do not place broad context phrases in `--focus`; explicit focus is an evidence contract. If no qualified result contains it, the synthesis gate closes and retrieval must be repaired.
- Search automatically consults Sachregister. Treat register entries as navigation only and expand the resolved TEXT/APPARAT source before making a claim.
- Use one optional `--trace-id` across plan, search/research-run, and evidence expansion. Finish with `protocol-report TRACE_ID` to expose zero-hit focus terms, excessive sequential search, omitted refinement, and evidence expansion counts.
- Protocol warnings are advisory. They do not block exploratory work or serendipitous evidence selection. Only evidence qualification, quotation, attribution, and synthesis gates are hard constraints.

Full examples and MCP field names are documented in `docs/agent_research_protocol.md`.
## Formal completion rule

- Use free `search` only for exploration or debugging.
- For a user-facing research deliverable, use `research-session start`, follow `session.next_action`, expand selected evidence, and finalize a structured report.
- Do not claim that formal research is complete unless the session state is `COMPLETE` and `completion_allowed` is true.
- If a session returns `NEEDS_REFINEMENT`, supply domain-aware focus/context/volume hints or a branch-level research refinement; do not ask the user for permission to perform this routine repair.
- APPARAT may guide interpretation, but an `author_argument` session cannot complete with APPARAT-only evidence.

## Branch refinement, readiness, and revisions

- A calling Agent's explicit `intent` is authoritative after schema validation. In particular, `author_argument` must not be downgraded by local lexical classification.
- Explicit focus terms are an evidence contract. The planner may add a bounded, auditable set of historical `c/k` spelling variants such as `produktive/productive Arbeit`; it must not add unrelated concepts.
- Refine compound questions per subquestion. Do not apply one flat focus/context list to every branch when the branches ask for textual reconstruction, conceptual comparison, and theoretical architecture separately.
- Read the three readiness axes independently: `semantic_ready` means the passage matches the concept, `scope_ready` means it belongs to the requested work/volume, and `provenance_ready` means its source layer is verified. `claim_eligible` does not imply `quote_eligible`.
- Every adequate or partial non-empirical branch used in a formal report must have at least one evidence item expanded in the active session revision and cited by the report. Success in one branch cannot cover another branch.
- Every formal `continue` that reruns retrieval creates a new artifact revision and clears the previous expanded-evidence selection. Expand against the active revision before finalization.
- A closed `research-run` synthesis gate or blocked `research-session continue` exits with code `5`. Treat it as a required research repair, not as a successful empty answer.
- `usage.api_tokens` and `usage.workbench_api_tokens` count only workbench-side API calls. `agent_model_tokens` is `null` unless the calling Agent reports its own model usage; do not describe a local workbench run as zero total research tokens without that qualification.
