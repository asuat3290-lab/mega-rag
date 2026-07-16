# MEGA² RAG — 回归测试说明

## 运行

```bash
python test_retrieval_regression.py           # 默认摘要输出
python test_retrieval_regression.py --verbose # 完整 debug 输出
```

- PASS → exit 0
- FAIL_ALG → exit 1
- XFAIL_DATA → exit 0 (数据覆盖不足，非算法错误)

## 测试用例

### 1. subsumption 中文查询

```
Query: 黑格尔法哲学批判中马克思怎么讨论subsumption
Expected: I/2 TEXT p.115 进入 top 3, snippet 包含 Subsumtion
Why: 验证核心概念词检索 + 作品范围约束 + snippet 围绕核心词
Failure:
  - priority_terms 为空 → glossary / lexical fallback
  - snippet 不含 Subsumtion → snippet_extractor before 窗口或 matched_term
  - p.115 不在 top 3 → rerank volume boost 或 scope_constrained_rerank
```

### 2. 归摄中文查询

```
Query: 黑格尔法哲学批判中马克思怎么讨论归摄
Expected: I/2 TEXT p.115 进入 top 3, snippet 包含 Subsumtion
Why: 验证中文概念词 → 德语展开 + 作品范围
Failure: 同上
```

### 3. Subsumtion 德语精确查询

```
Query: Subsumtion Hegelschen Rechtsphilosophie
Expected: I/2 TEXT p.115 进入 top 5, snippet 包含 Subsumtion
Why: 验证无 glossary 命中时的 lexical fallback
Failure: priority_terms 为空 → extract_lexical_terms 问题
```

### 4. 意识形态 in 德意志意识形态

```
Query: 德意志意识形态中如何讨论意识形态概念
Expected: I/5 TEXT p.147 进入 top 3, snippet 包含 Ideologie
Why: 验证核心概念 + 著名作品
Failure: target volume 未检测 → WORK_VOLUME_MAP
```

### 5. 1844 手稿 / 异化劳动

```
Query: 1844手稿中马克思怎么讨论异化劳动
Expected: I/2 TEXT 的经济学哲学手稿原文页（PDF p.298-305）进入 top 5
Why: 验证书目映射、目标卷召回与原文页约束，防止其他文本中的泛同形词造成假通过
Failure:
  - scoped 无结果 → do_scoped_search LIKE 词遗漏
  - scoped 结果未注入 → merge 逻辑
  - scope_constrained 未生效 → in_scope_text < 3
```

### 6. 剩余价值 in 资本论手稿

```
Query: 资本论手稿中关于剩余价值的论述
Expected: TEXT 页面包含 Mehrwert, II/3 或 II/10 进入 top 5
Why: 验证核心概念 + 宽泛作品范围 (无特定 Band)
Failure: priority_terms 不含 Mehrwert → glossary expansion
```

### 7. APPARAT 意图查询

```
Query: 德意志意识形态的编者注和异文说明
Expected: APPARAT 结果靠前 (并非全部 TEXT)
Why: 验证 intent=apparat_question 时 APPARAT 不被过度压制
Failure: TEXT 仍大面积压过 APPARAT → intent classifier 或 rerank weights
```

### 8. 通用查询

```
Query: 马克思论国家
Expected: 返回含 Staat / Staatsgewalt 的 TEXT 页面, 来自多个卷
Why: 验证无特定意图/作品时的平衡结果
Failure: 结果过于集中单一卷 → balance 问题
```

## 何时运行

- 修改 `glossary.yaml` 后
- 修改 `rerank.py` 后
- 修改 `webui.py` 检索逻辑后
- 修改 `query_analyzer.py` / `snippet_extractor.py` 后
- 重建索引后

## 添加新测试

在 `test_retrieval_regression.py` 的 `tests` 列表中添加：

```python
{
    "query": "...",
    "expected_terms_in_snippet": ["..."],
    "expected_volume": ("I", "2"),    # optional
    "min_text_in_top": 2,              # optional
    "description": "...",
}
```
