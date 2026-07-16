# Research Retrieval Evaluation

This benchmark checks whether the local MEGA² index can return usable evidence
for realistic research questions before any answer model is called. It is fully
local: DeepSeek Flash and Pro are not used, so a benchmark run has no API cost.

## Retrieval paths

`eval_retrieval.py` evaluates the same query against three paths:

1. `page_bm25`: SQLite FTS5 over full OCR or MEGAdigital page records.
2. `passage_bm25`: SQLite FTS5 over the incremental passage index.
3. `hybrid_current`: passage FTS + page-level `bge-m3` vectors + RRF + target-volume
   injection + rule reranking + scope-constrained reranking.

The first two paths are diagnostic baselines. `hybrid_current` represents the
retrieval path used by the Web UI, without Flash evidence cards or Pro analysis.

## Query set

`eval_queries.yaml` contains 20 research-oriented cases covering:

- work-scoped concepts in *The German Ideology* and the critique of Hegel's
  philosophy of right;
- Chinese, mixed-language, and exact German queries;
- the 1844 manuscripts, *Grundrisse*, and Capital manuscripts;
- a deliberate APPARAT query;
- cross-volume correspondence questions about Marx's finances;
- the previously failing Chinese query about `一般劳动`.

Each case records expected German evidence terms, optional MEGA volume and text
type expectations, and the cutoff at which the case passes. Expectations are
used only for evaluation. They are never injected as hidden retrieval filters.

Legacy fields (`zh`, `de_expected`, `preferred_abteilung`, `preferred_band`) are
still accepted, but new cases should use this structure:

```yaml
- id: stable_case_id
  query: "研究问题"
  category: work_concept
  expected_terms: ["German term", "historical spelling"]
  preferred_volumes:
    - {abteilung: "I", band: "5"}
  preferred_type: "TEXT"
  must_hit_volume: true
  must_hit_type: true
  pass_at: 5
  notes: "Why the case exists."
```

Before scoring retrieval, the evaluator checks the index for evidence satisfying
the declared constraints:

- `PASS`: qualifying evidence appears within `pass_at`.
- `FAIL_RETRIEVAL`: qualifying evidence exists in the index but was not retrieved.
- `XFAIL_DATA`: no qualifying evidence exists in the current index.

This prevents missing data from being reported as an algorithm regression.

## Commands

Run the full benchmark:

```powershell
python eval_retrieval.py --queries eval_queries.yaml --top-k 10
```

Run only cheap lexical baselines:

```powershell
python eval_retrieval.py --modes page,passage --top-k 10
```

Print top results and query profiles:

```powershell
python eval_retrieval.py --verbose
```

Fail with a nonzero exit code when the hybrid path has a retrieval failure:

```powershell
python eval_retrieval.py --strict
```

Compare against the committed hybrid pass/fail baseline:

```powershell
python eval_retrieval.py --fail-on-regression
```

Only replace the baseline after reviewing a complete successful report:

```powershell
python eval_retrieval.py --write-baseline
```

Timestamped and latest Markdown/JSON reports are written under `eval_reports/`.
The committed `research_benchmark_baseline.json` is intentionally compact; full
diagnostic reports remain local runtime artifacts.

## Metrics

The report includes evidence Hit@5/Hit@10, MRR, expected-term coverage, target
volume and text-type hits, authoritative MEGAdigital visibility, duplicate-page
rate, average candidate characters, OCR quality, and latency. Candidate character
count is a model-independent proxy for downstream token cost.

## Current baseline (2026-07-16)

| Path | Pass | Hit@5 | Hit@10 | MRR | Mean candidate chars |
|---|---:|---:|---:|---:|---:|
| Page BM25 | 12/20 | 60% | 60% | 0.435 | 2,315 |
| Passage BM25 | 9/20 | 45% | 50% | 0.352 | 1,760 |
| Current hybrid | 20/20 | 90% | 100% | 0.704 | 2,154 |

Passage FTS reduces lexical candidate length by about 24%, but it is not a
standalone recall improvement. The current hybrid pipeline restores recall with
vector and scope signals while keeping candidate text below the page baseline.

Five benchmark cases have qualifying MEGAdigital evidence. None isolates a
semantic-only failure caused by those 11,647 records lacking embeddings. The
current evidence therefore supports postponing full MEGAdigital embedding. A
selective embedding pilot should be reconsidered only when multiple benchmark
failures have digital corpus coverage and fail both lexical baselines and the
hybrid path.
