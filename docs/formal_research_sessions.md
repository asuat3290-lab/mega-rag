# Formal MEGA Research Sessions

Formal research is a persistent, fail-closed workflow for Agents. It complements the free `search` and `research-run` commands; it does not remove them.

## Two lanes

- Exploratory lane: `plan`, `search`, `register`, and `research-run` remain available for discovery and debugging. Their output is not a completion certificate.
- Formal lane: `research-session` records the plan, retrieval artifact, expanded evidence, report validation, and completion receipt. An Agent must reach `COMPLETE` before claiming that a requested research task is finished.

## State model

```text
PLANNED / NEEDS_REFINEMENT
  -> RETRIEVED
  -> EVIDENCE_QUALIFIED or RETRIEVAL_GAP
  -> EVIDENCE_EXPANDED
  -> REPORT_VALIDATED
  -> COMPLETE
```

Every transition is stored in `research_sessions.db`. A restart or shutdown does not lose the session. `status` returns both the current state and one structured `next_action`.

The system constrains completion, not exploration. An Agent may alter terms, volumes, branches, and selected evidence. It cannot skip these invariants:

1. unresolved semantic planning cannot enter formal synthesis;
2. an author-argument task needs qualified `TEXT`, not only APPARAT;
3. evidence used in claims must first be explicitly expanded;
4. quotations must pass the existing quote and locator checks;
5. a report may reference only evidence expanded in the current session;
6. only a validated report receives a tamper-evident completion receipt.

## CLI workflow

Start with the local, zero-token planner:

```powershell
python mega_agent.py research-session start "马克思如何批判施蒂纳的唯一者"
```

If the state is `NEEDS_REFINEMENT`, the calling Agent should exercise its own semantic judgment:

```powershell
python mega_agent.py research-session continue SESSION_ID `
  --focus "Stirner,Der Einzige,Eigenheit" `
  --context "Sankt Max,Einziger" `
  --volumes "I/5" `
  --intent author_argument
```

For a compound question, pass a research-plan refinement instead of applying one flat hint set to every branch:

```powershell
python mega_agent.py research-session continue SESSION_ID `
  --refinement-file research_refinement.json
```

When `EVIDENCE_QUALIFIED` is reached, expand only the evidence selected for actual use:

```powershell
python mega_agent.py research-session expand SESSION_ID E004 E006 --detail full
```

Write a structured report and finalize it:

```json
{
  "claims": [
    {
      "claim_id": "C1",
      "claim_type": "text_supported",
      "evidence_refs": ["E004"],
      "quotes": []
    }
  ]
}
```

```powershell
python mega_agent.py research-session finalize SESSION_ID --report report.json
python mega_agent.py research-session status SESSION_ID
```

A successful finalization returns `completion_allowed: true`, state `COMPLETE`, a receipt ID, and a receipt JSON path. A failed finalization returns exit code `4`; ordinary report validation still uses exit code `3`.

Use `--auto` at `start` or `continue` only when bounded Flash planning is acceptable. The default local path uses no planning API tokens.

## MCP operations

External Agents can use the same state machine through:

- `mega_research_session_start`
- `mega_research_session_continue`
- `mega_research_session_expand`
- `mega_research_session_finalize`
- `mega_research_session_status`

The Agent should follow `session.next_action.operation` rather than asking the user whether it should perform an ordinary expected next step. User input is needed only for a genuine interpretive choice, missing external evidence, or a risk decision.

## Completion receipt

The receipt binds:

- session ID and question;
- index version;
- source artifact path and SHA-256;
- report path and SHA-256;
- expanded evidence identities;
- report-validation result;
- completion scope and locator warning.

This is a reproducibility and tamper-detection record, not a digital signature. OCR evidence without a verified locator can support a qualified analysis, but it cannot silently become a citation-ready quotation.

## Artifact revisions and branch completeness

Every retrieval-producing `continue` creates an immutable entry in `artifact_revisions`. The session exposes `active_revision`, the parent revision, run ID, artifact path, refinement hash, gate result, and status. Rerunning retrieval intentionally clears the expanded-evidence selection because local `E###` identifiers belong to one artifact revision.

Formal completion is branch-scoped. For every non-empirical branch whose retrieval status is `adequate` or `partial`:

1. at least one evidence item from that branch must be expanded in the active revision;
2. the structured report must cite at least one of those expanded items;
3. an `author_argument` branch must use qualified `TEXT`, not APPARAT or an editorial introduction;
4. a theoretical bridge must be labelled as inference unless the retrieved passage states the relation directly.

The completion receipt binds the active revision and its revision count in addition to the source artifact and report hashes.

## Readiness and provisional OCR

Evidence exposes separate readiness dimensions:

- `semantic_ready`: the required concept or relation is present;
- `scope_ready`: the passage belongs to the requested work or volume;
- `provenance_ready`: the source layer is verified as author text or the requested editorial layer;
- `claim_eligible`: semantic, scope, and text-type requirements permit qualified analysis;
- `quote_eligible`: the expanded text, locator, and source authority permit a verbatim quotation.

A run whose usable evidence is entirely unverified OCR may open synthesis for a qualified interpretation, but it returns `provisional_source_layer_requires_disclosure`. The Agent must disclose the provisional status and must not turn an OCR physical page into a verified MEGA citation.

## Exit codes and token accounting

- Exit code `5`: synthesis gate closed or session continuation blocked; refine or repair retrieval.
- Exit code `4`: formal finalization failed.
- Exit code `3`: standalone report validation failed.

`usage.api_tokens` and `usage.workbench_api_tokens` cover only API calls made by the workbench. Local FTS, vector retrieval, Sachregister navigation, and Agent-supplied planning consume zero workbench API tokens. The calling Agent's own prompt and reasoning tokens are outside this counter and remain `agent_model_tokens: null` unless supplied by the caller.
