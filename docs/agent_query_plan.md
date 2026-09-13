# Agent 使用 QueryPlan 的低 token 工作流

## 推荐顺序

```text
capabilities
  -> plan
  -> 必要时由 Agent 补充 refinement
  -> term-probe / register（可选）
  -> search(detail=index)
  -> evidence(E###, detail=snippet|full)
  -> verify（只有需要语义判断时）
```

前三步全部可以 0 API token 完成。不要一开始把大量原文交给主模型。

## 复合问题

当问题同时要求原文重建、概念关系、反作用因素或当代应用时，不应把整题直接交给一次 search。使用：

~~~text
research-plan
  -> 检查 coverage_status 和 subquestions
  -> 必要时补 research refinement
  -> research-run(detail=index)
  -> 检查每个 branch 和 missing_requirements
  -> 只展开选中的 E### 证据
~~~

~~~powershell
python mega_agent.py research-plan "马克思怎样讨论利润率下降，人工智能是否会降低利润率，如何用机器理论说明"
python mega_agent.py research-run "同一问题" --top-k-per-branch 5 --max-evidence 12 --detail index
~~~

ResearchPlan 会把 MEGA 能回答的理论问题与需要外部资料的当代经验问题分开。一个分支检索成功，不会自动把整个问题标为充分。


## JSON CLI

```powershell
cd D:\mega_rag

python mega_agent.py capabilities
python mega_agent.py status
python mega_agent.py plan "马克思如何讨论资本集中的历史形成"
python mega_agent.py term-probe "马克思如何讨论资本集中的历史形成"
python mega_agent.py register "马克思如何讨论资本集中的历史形成" --top-k 8
python mega_agent.py search "马克思如何讨论资本集中的历史形成" --detail index --top-k 8
```

每次成功调用只在 stdout 输出一个 JSON 对象。诊断日志写入 stderr。响应同时包含：

```json
{
  "ok": true,
  "protocol": "mega-research-workbench/v1",
  "schema_version": "mega-agent-v1",
  "operation": "plan"
}
```

## 由 Agent 补充德语词

先保存一个 refinement 文件，例如：

```json
{
  "core_terms": ["Plebs"],
  "historical_variants": [],
  "related_non_equivalent": ["Pöbel", "Lumpenproletariat"],
  "supporting_terms": ["Pauperismus", "Übervölkerung"],
  "generic_terms": ["Marx"],
  "target_works": [],
  "target_volumes": [],
  "target_topics": [],
  "relation_pairs": [["Plebs", "Pauperismus"]]
}
```

然后执行：

```powershell
python mega_agent.py plan "马克思如何讨论贱民" --refinement-file refinement.json
python mega_agent.py search "马克思如何讨论贱民" --refinement-file refinement.json --detail index
```

Agent 不应把自己的理论判断写进 refinement。refinement 只允许提供检索词角色、卷册范围和关系词对。

## 可选 hybrid

```powershell
python mega_agent.py plan "一个尚未进入术语表的新问题" --hybrid
python mega_agent.py search "一个尚未进入术语表的新问题" --hybrid --detail index
```

hybrid 会显式调用 Flash，默认 local 不会。API 不可用时回退 local。实际 token 可在响应中查看：

```json
{
  "usage": {
    "api_tokens": 0,
    "planner": {
      "mode": "local",
      "usage": {"total_tokens": 0}
    }
  }
}
```

## MCP 工具

`mega_mcp.py` 提供：

- `mega_capabilities`
- `mega_status`
- `mega_plan`
- `mega_research_plan`
- `mega_research_run`
- `mega_term_probe`
- `mega_register`
- `mega_search`
- `mega_verify`

`mega_plan` 和 `mega_search` 的 `planner_mode` 可取 `local`、`hybrid`、`agent_supplied`。MCP 中的 refinement 使用 JSON 字符串参数。

`mega_research_plan` 和 `mega_research_run` 使用同一 planner_mode；返回的总体状态要求所有必要 MEGA 分支都充分，并单列外部经验材料缺口。

## 如何读取 search 结果

优先检查：

1. `summary.retrieval_adequacy.status`；
2. `evidence[].candidate_class`；
3. `evidence[].evidence_eligible`；
4. `verified_author_text` 和 `text_layer`；
5. `locator_verified`；
6. `matched_term` 与 `matched_priority_terms`。
7. `authorship_status` 与 `edition_status`；
8. `preview_only` 与 `quote_eligible`。

只有 `evidence_eligible=true` 的 TEXT 候选才适合进入作者观点分析。`verified_author_text=false` 时仍须保留来源警告。

## 如何读取 verify 结果

`verify` 同时返回两类状态：

```json
{
  "status": {
    "retrieval": "partial",
    "judgment": "model_reviewed",
    "answerability": "partially_answerable",
    "strong_claim_warning": "..."
  },
  "overall": {
    "verdict": "qualified"
  }
}
```

- `retrieval` 说明材料是否充分。
- `judgment` 说明是否已进行语义证据审查。
- `answerability` 说明当前结果能否回答。
- `overall.verdict` 才是对命题的证据判断。

不要把 `retrieval_gap`、`coverage_gap` 或 `insufficient` 自动翻译成“该命题错误”。

## Agent 的节省 token 原则

1. 默认先请求 `detail=index`，通常只返回来源、短预览和信号。
2. 只对选中的 `E###` 请求 `snippet` 或 `full`。
3. `plan` 和 `term-probe` 不返回大量正文。
4. 只有需要支持/限定/反证判断时才调用 `verify`。
5. 只有需要成文综合时才启用 Pro。
