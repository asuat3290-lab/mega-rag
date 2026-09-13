# MEGA Agent 研究协议

本文说明 Codex、OpenCode、Qoder 等外部 Agent 如何低成本调用 MEGA 工作台，同时保留研究裁量权并避免“关键词碰运气”。

## 设计原则

研究策略采用软约束，证据安全采用硬门控：

1. 工作台自动完成可确定的工作：术语表展开、纯德语 lexical fallback、Sachregister 导航、全局与目标卷召回、重排、证据资格判断。
2. Agent 可以用少量结构化提示明确研究判断，不需要填写完整表格。
3. 工作台不禁止自由搜索，但可以在研究结束后报告流程偏差。
4. 只有证据与综合层是硬约束：显式核心词未命中、证据资格不足或来源层级不明时，`synthesis_gate` 会关闭。

## 正式研究模式

`search` 和 `research-run` 保留为自由探索入口。需要向用户交付“已经完成的研究”时，Agent 应改用持久化的 `research-session` 流程；只有状态达到 `COMPLETE` 并取得完成凭证，才能声称正式研究已结束。

```powershell
python mega_agent.py research-session start "研究问题"
python mega_agent.py research-session continue SESSION_ID --focus "核心德语词" --volumes "I/5"
python mega_agent.py research-session expand SESSION_ID E001 --detail full
python mega_agent.py research-session finalize SESSION_ID --report report.json
```

该流程不会限制 Agent 如何选择术语、卷册或证据，但会禁止跳过语义精炼、正文证据、证据展开和报告校验。完整说明见 `docs/formal_research_sessions.md`。

## 低摩擦查询提示

普通定位仍可直接使用自由文本：

```powershell
python mega_agent.py search "Stoffwechsel"
```

当问题包含多个不同角色的词时，Agent 应明确三类信息：

```powershell
python mega_agent.py search `
  "Stoffwechsel Natur Arbeit Boden Agrikultur kapitalistische Produktionsweise" `
  --focus Stoffwechsel `
  --context "Natur,Arbeit,Boden,Agrikultur,kapitalistische Produktionsweise" `
  --volumes "II/1,II/5,II/6" `
  --intent author_argument `
  --detail index
```

- `--focus`：必须出现在合格证据中的核心概念。可以重复传入。
- `--context`：用于扩展召回和判断邻近语境，不得单独冒充核心证据。
- `--volumes`：软范围约束。目标卷正文优先，但不会删除范围外的补充材料。
- `--intent`：决定正文、APPARAT、版本材料等的排序方式。

这些参数会编译成既有 QueryPlan refinement，不会形成第二套不兼容协议。

## Agent 推荐流程

### 单一定位问题

1. 调用 `plan`，检查 `planning_advice`、`term_probe` 和 Sachregister 提示。
2. 如果返回 `refinement_recommended`，由调用 Agent 补充 focus、德语词形和卷册。
3. 调用 `search --detail index --save`。
4. 检查 `focus_diagnostics` 和 `synthesis_gate`。
5. 只展开准备引用的少量证据。

### 复合、历时或比较问题

1. 调用 `research-plan`。
2. Agent 按分支补充 `query_refinement`，而不是连续执行大量互不关联的 search。
3. 调用 `research-run`，检查每一分支的证据充分性。
4. 对选中证据执行 `evidence --detail snippet|full`。
5. 形成结构化 claims，并执行 `report-check`。

## 自动诊断

工作台会自动提供以下诊断：

- `flat_multi_term_query_needs_focus`：多个平铺词没有区分核心词和上下文词。
- `unmapped_lexical_concept_needs_semantic_refinement`：冷门德语概念只有裸词匹配，没有相关概念、支撑词或规则。
- `explicit_focus_not_recalled`：候选池中没有显式核心词。
- `explicit_focus_not_in_qualified_final_evidence`：核心词被召回，但没有进入合格最终证据。
- `display_term_mismatch`：结果包含核心词，但展示窗口或 matched term 被其他词占据。

Sachregister 查询由检索流程自动执行。其结果只用于定位作者原文，不可作为作者证据。

## 研究过程审计

`trace_id` 是可选的。它不会改变排序，也不会阻止任何检索：

```powershell
python mega_agent.py search "..." --focus Begriff --trace-id paper-ch3
python mega_agent.py evidence "D:\mega_rag\research_exports\research_....json" E002 `
  --detail full --trace-id paper-ch3
python mega_agent.py protocol-report paper-ch3
```

协议报告包括：

- search 次数；
- 是否使用 research-run；
- 是否查询 Sachregister；
- 是否提交 refinement；
- 各 focus term 的候选、最终与合格命中数；
- 展开的证据数量；
- 多次串行 search、核心词零命中或冷门概念未精炼等警告。

报告是事后审计，不是事前审批。Agent 可以根据意外发现改变检索方向，但这种偏离会留下可检查记录。

## MCP 对应接口

MCP 提供相同能力：

- `mega_plan`
- `mega_search`
- `mega_research_plan`
- `mega_research_run`
- `mega_evidence`
- `mega_protocol_report`
- `mega_source_catalog_list`
- `mega_source_catalog_coverage`
- `mega_term_probe`

`mega_plan` 与 `mega_search` 接受 `focus_terms`、`context_terms`、`target_volumes`、`intent` 和 `trace_id`。

## 不会自动“越用越聪明”

Agent 或模型产生的新计划只会进入候选 planning memory。长期复用必须经过：

1. 语料存在性验证；
2. 人工检查概念关系、卷册范围和文本层级；
3. 显式 promote。

这样系统可以积累经审核的研究知识，而不会把一次错误检索永久固化。

## 分支级语义精炼

对复合问题，Agent 不应把同一组 `focus_terms` 平铺到所有子问题。应当分别处理：

- 文本重建分支：核心概念、作者或争论对象、目标卷；
- 概念比较分支：双方规定及区别性关系词；
- 体系关系分支：核心概念作为 `focus_terms`，剩余价值、再生产等作为 `context_terms` 或 `relation_pairs`；
- 版本考证分支：显式使用 `apparat_question`，不得沿用正文论述意图。

Agent 提交的 `intent` 经过字段校验后优先于本地词法分类。显式核心词还会获得有限、可审计的历史拼写展开，例如 `produktive Arbeit` 自动补入 `productive Arbeit`；该机制只处理拼写见证，不会自动宣称概念等价。

当 Agent 已提交完整分支、解决未映射概念且各子计划有效时，`planning_advice` 返回 `local`，不再重复要求“拆分复合问题”。

## 证据选择与准备度

研究运行先扩大候选池，再按每个分支的文本功能选择证据。定义与区分问题优先含 `Bestimmung`、`Unterscheidung`、`nicht ... sondern` 等论证性片段；关系问题优先同时覆盖核心概念和结构语境。此选择只决定进入证据包的候选，不替代 Agent 对完整段落的判断。

每条证据应分别检查：

- `semantic_ready`：是否真正命中核心概念；
- `scope_ready`：是否处于目标著作或卷册；
- `provenance_ready`：是否已确认来源层；
- `claim_eligible`：是否可用于有限论证；
- `quote_eligible`：是否可以逐字引用。

`textband_unclassified` 可以成为 `provisional_claim_evidence`，但不能自动变成正式引文。若全部主张证据都来自此类 OCR，运行会给出 `provisional_source_layer_requires_disclosure`。

## 正式交付的分支约束

每个状态为 `adequate` 或 `partial` 的非经验分支，都必须在当前会话修订中至少展开一条证据，并在报告中引用该分支的一条已展开证据。一个分支的成功不能替代另一个分支。重新运行 `continue` 会产生新的 artifact revision，并清空旧修订的展开选择。

门禁关闭或 `continue` 被阻止时，CLI 返回退出码 `5`。Agent 应自动执行计划或检索修复，不应把空结果包装成完成回答。

工作台的 `api_tokens=0` 仅表示本地工作台没有调用外部规划/回答 API；调用工作台的 Agent 自身 token 不在该数字内，除非调用方显式回传，此时才填写 `agent_model_tokens`。

## 覆盖发现与精确词项调查

覆盖发现复用 source catalog，不把文件、卷册或索引记录数当作历史作品数：

```powershell
python mega_agent.py source-catalog-coverage --work "Das Kapital" --language de --page-size 20
```

覆盖行同时给出作品/版本依据、卷次、`language`、`TEXT`/`APPARAT`、索引记录数、定位范围、已知缺口和元数据依据。空语言元数据保持 `unknown`；同一 source unit 出现多个语言，或已知语言与空值并存时，汇总为 `mixed`。未建立独立完整性依据时，完整性为 `not_assessed`。无命中只表示当前过滤后的索引范围没有命中，不表示语料不存在。

词项调查沿用既有 `term-probe`/`mega_term_probe`，但结果分开标注三类：`exact_phrase`（原词/短语）、`lexical_variant`（历史拼写或词形扩展）和 `semantic_related`（配置的相关词，仅作召回线索）。规范化只用于检索；`matched_form`、`context` 和定位中的原文不被改写。每个命中保留稳定 `match_id`、记录/来源 ID、版本、定位、上下文和分页信息；来源分组只用于展示，不删除不同版本或原文命中。

```powershell
python mega_agent.py term-probe "Arbeit" --page 1 --page-size 50 --language de --export probe.json
```

`result_status=no_match_in_indexed_scope` 与 `absence_claim=null` 是无结果的兼容表达。跨记录、跨页拼接不会被当作一个词项命中。检索评测、模型调用和补向量不属于上述只读调查，不能由这些命令隐式触发。
