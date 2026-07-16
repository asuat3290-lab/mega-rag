# MEGA² RAG — 检索架构说明

## 完整流水线

```
用户查询 (中文 / 德语 / 中英混合)
  │
  ├─[1] glossary expansion ──────────────────────────
  │    glossary_loader.py → 中文→德语术语映射
  │    兼容旧格式 (str) 和新格式 ({de: [...], type: concept})
  │    fallback: extract_lexical_terms() 从查询提取德语词
  │
  ├─[2] query_profile ───────────────────────────────
  │    query_analyzer.py → 区分:
  │      core_terms    (概念词: subsumption, 异化, Entfremdung)
  │      work_terms    (著作名: 黑格尔法哲学批判, 德意志意识形态)
  │      author_terms  (作者名: 黑格尔, 马克思)
  │      generic_terms (泛词: Kritik, Philosophie)
  │      intent        (author_argument / apparat_question / general_search)
  │      target_volume (Abteilung + Band, 从 WORK_VOLUME_MAP 检测)
  │
  ├─[3] priority_terms ──────────────────────────────
  │    build_priority_terms() → 德语检索词按优先级排列:
  │      core expansions > lexical_core > work > author > generic
  │    eg: [Subsumtion, subsumieren, subsumiert, Hegelschen, ...]
  │
  ├─[4] global retrieval ────────────────────────────
  │    do_search() → BM25 (SQLite FTS5) + Embedding (bge-m3, Ollama)
  │    → RRF 融合 → top_k × 3 candidate pool
  │
  ├─[5] scoped retrieval injection ──────────────────
  │    仅当 target_volume EXISTS + intent == author_argument:
  │    do_scoped_search() → 目标卷内 LIKE 召回 (非 FTS5)
  │    用 priority_terms[:10] 逐词 LIKE 搜索
  │    → text_only TEXT 页面 → ID 去重注入 global pool
  │
  ├─[6] rerank ─────────────────────────────────────
  │    rerank.py rerank_rule():
  │      RRF 基础分 (50%) + 精确命中 + glossary 命中
  │      + TEXT/APPARAT type boost + intent boost
  │      + volume boost (温和) - noise penalty
  │      → unified score (越高越相关)
  │
  ├─[7] scope_constrained_rerank ────────────────────
  │    仅当 in_scope_text >= 3:
  │    in_scope_text → in_scope_apparat → out_scope_text → out_scope_apparat
  │    不硬过滤，仅重排优先级
  │
  ├─[8] snippet_extractor ──────────────────────────
  │    extract_best_snippet() → 围绕 priority_terms 上下文
  │    返回 {snippet, preview (围绕 matched_term), matched_term}
  │
  ├─[9] Flash / Pro / Web UI ────────────────────────
  │    build_snippet_for_flash() → deepseek-chat 证据卡片
  │    _call_pro() → deepseek-reasoner 学术分析
  │    Gradio UI → 检索模式选择 / 重排 / 缓存
  └──────────────────────────────────────────────────
```

## 各层职责

### 1. glossary expansion

- `glossary_loader.py` 加载 `glossary.yaml`
- 兼容旧格式 (`subsumption: "Subsumtion subsumieren..."`) 和新格式 (`{de: [...], type: concept}`)
- `expand_with_glossary()` 返回 `(expanded_query, matched_terms, hints)`
- 未命中时 `extract_lexical_terms()` 从查询提取德语词

### 2. query_profile

- `query_analyzer.py` 的 `analyze_query()` 分类所有匹配词
- `WORK_VOLUME_MAP` 检测著作名，映射到 `(Abteilung, Band)`
- `_classify_intent()` 基于模式匹配判断用户意图

### 3. priority_terms

- 德语检索词，按优先级排列
- 驱动 snippet 截取和 scoped search

### 4. global retrieval

- BM25: SQLite FTS5 + `fts5_or()` 过滤中文
- Embedding: bge-m3 via Ollama → LanceDB
- RRF: k=60 融合

### 5. scoped retrieval injection

- `do_scoped_search()`: SQL LIKE（非 FTS5，避免 OCR 低质量页丢失）
- 仅当明确的 target_volume + author_argument 意图时激活
- 注入去重后进入 global pool

### 6. rerank

- RRF base (50%) + signal modulation
- 分数方向统一：越高越相关
- 加入 `_debug` 信号用于诊断

### 7. scope_constrained_rerank

- 分桶：in_scope_text / in_scope_apparat / out_scope_text / out_scope_apparat
- `in_scope_text >= 3` 时激活
- 不硬过滤 out_scope 结果

### 8. snippet_extractor

- `extract_best_snippet()` 返回结构化：`{snippet, preview, matched_term}`
- preview 围绕 matched_term 居中 (50+150 字符)
- Flash 使用完整 snippet
