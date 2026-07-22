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
