# MEGA 专家型检索架构

## 目标

本系统不是普通摘要型 RAG。它首先要找到可核查的德语原文，区分作者正文、编者材料和索引导航，再判断现有证据是否足以回答问题。默认 local 流程完全本地运行；用户显式选择 `hybrid`，或使用 `auto` 且规划策略判定本地计划不足时，才调用规划 API；`verify` 的模型审查仍由用户控制。

## 当前流水线

```text
中文/德文问题
  -> glossary + 德文 lexical fallback
  -> QueryPlan（词的角色、意图、作品范围、强量词）
  -> 全局 BM25 + embedding + RRF
  -> 核心词/历史拼写逐词召回
  -> 目标卷 TEXT 范围召回
  -> Sachregister 导航与可靠印刷页解析
  -> 结构关系词与相关概念分支
  -> 统一 rerank
  -> 候选资格分层（ordinal gate）
  -> snippet / preview
  -> 检索充分性判断
  -> evidence package / verify / Web UI / Agent
```

## QueryPlan

`query_plan.py` 把查询词分为五类，避免所有词被当成同义词：

- `core_terms`：问题真正要求的概念或表达。
- `historical_variants`：MEGA 中可能出现的历史拼写，例如 `Concentration`。
- `related_non_equivalent`：相关但不等同的概念，例如 `Centralisation` 不能冒充 `Konzentration`。
- `supporting_terms`：结构关系和共同语境，例如 `Akkumulation`、`Konkurrenz`。
- `generic_terms`：只用于扩大召回的泛词，例如 `Kapital`、`Marx`、`Kritik`。

计划同时记录：

- `intent` 与供旧检索器使用的 `routing_intent`；
- 目标著作、卷册和主题；
- 关系词对；
- 必需信号和负面/噪声信号；
- `predominant`、`universal`、`exclusive` 等强量词；
- 每个召回分支及其用途。

规则保存在 `query_rules.yaml`。它不是一个封闭的中德词典，而是少量可审计的概念关系和历史拼写规则。没有规则时，系统继续使用 glossary 和德文 lexical fallback。

## 四种规划模式

### local（默认）

- 不调用 API，规划开销为 0 token。
- 使用 glossary、德文词法回退、规则和本地语料探测。
- 适合大多数重复检索和批量研究。

### hybrid（显式启用）

- 先生成本地计划、词项统计和 Sachregister 提示。
- 再让 Flash 只补充德语词、历史拼写、相关概念和范围。
- 输出仍须经过 `merge_query_plan()` 的严格字段校验。
- Flash 不回答研究问题，不判断证据，也不能改变文本来源。
- 单次规划上限为 700 completion tokens；实际 token 由 Agent 响应中的 `usage.planner` 报告。
- Flash 不可用时自动回退到 local，并返回 `hybrid_fallback_local`。

### agent_supplied

- Codex、OpenCode 或其他 Agent 先调用 `plan`，检查本地计划。
- Agent 可提交一个 JSON refinement 补充德语检索词和范围。
- refinement 经过白名单字段、长度、数量、角色和意图校验。
- 该模式本身不调用外部模型，因此工作台侧开销仍为 0 API token。

## Sachregister

`sachregister.py` 从现有 `metadata.db` 构建独立的 `sachregister.db`。当前快照包含 1,086 个明确识别的 Sachregister 页面，覆盖 48 卷。

重要边界：

1. Sachregister 永远是导航材料，`evidence_eligible=false`。
2. 索引条目不能证明马克思或恩格斯提出了某一命题。
3. 页码只在能与明确的 `MEGAdigital page_label` 对应时解析为正文候选。
4. OCR PDF 物理页没有可靠印刷页映射时，系统不会猜测页码偏移。
5. 最终引用必须落到解析后的 TEXT 段落，而不是 Sachregister 页面。

重建与检查：

```powershell
python sachregister.py build
python sachregister.py status
python sachregister.py search Konzentration Concentration Centralisation
```

## 术语探测

`term_probe.py` 在进入昂贵检索前统计精确词项：

- 总命中页；
- TEXT / APPARAT 分布；
- 卷册和文本层级；
- OCR / MEGAdigital 来源；
- 少量居中样例。

这能区分：

- 语料中没有该词；
- 只有 APPARAT 命中；
- 目标卷有数据但召回池漏掉；
- 历史拼写比现代拼写更常见。

例如在 `II/10 TEXT` 的当前索引中，`Konzentration`、`Concentration` 和 `Centralisation` 的分布明显不同，因此系统不把它们合并为一个同义词。

## 候选资格与充分性

`retrieval_quality.py` 不继续叠加全局权重，而是按证据资格稳定分层：

1. 目标范围内的直接作者文本；
2. 其他直接作者文本；
3. 目标范围内的结构性作者文本；
4. 相关但不等同的概念语境；
5. APPARAT/编者/副文本；
6. 只有泛词命中的页面；
7. 没有必需信号的页面。

每条结果保留：

- `candidate_class`；
- `evidence_eligible`；
- 命中的核心、历史、相关、结构和泛词；
- 目标卷是否匹配；
- 召回来源和 Sachregister 定位来源。

检索整体状态包括：

- `adequate`：有足够直接正文候选；
- `partial`：只有少量直接证据、结构证据，或问题含强量词；
- `retrieval_gap`：term probe 有 TEXT 命中，但结果池未召回；
- `coverage_gap`：Sachregister 有导航线索，正文索引尚无直接命中；
- `insufficient`：当前没有可用作者证据。

“未检出”不等于“马克思从未这样说”。

## 强量词

“主要”“总是”“仅仅”“从未”等命题不能由几条命中段落证明。系统会保留相关段落，但把 answerability 降为 `partially_answerable`，并要求进一步进行：

- 全语料词频或分期比较；
- 竞争概念的对照统计；
- 负样本抽查；
- 语义用法人工编码。

因此，`verify` 不会再把“找到了若干 Entfremdung 段落”直接升级为“马克思主要用 Entfremdung 描述资本关系”。

## 关键文件

- `query_plan.py`：本地计划、严格 refinement 合并、版本号。
- `query_rules.yaml`：历史拼写、概念关系、强量词和范围规则。
- `query_plan_model.py`：可选 Flash 规划精炼器。
- `sachregister.py` / `sachregister.db`：索引导航层。
- `term_probe.py`：精确词项覆盖审计。
- `research_export.py`：分支召回、研究包和证据序列化。
- `retrieval_quality.py`：候选资格与充分性。
- `claim_audit.py`：观点核验、状态分离和模型校准。
- `agent_service.py` / `mega_agent.py` / `mega_mcp.py`：Agent 协议。

## 版本与缓存

- 主索引使用 `index_version`。
- QueryPlan 使用稳定的 `planner_version`。
- Sachregister 使用 `register_version`。
- claim audit 缓存键同时包含三类版本；规则或 Sachregister 更新后不会误用旧核验缓存。

## 当前限制

- OCR Textband 中仍有未自动确认的 `textband_unclassified` 页面，正式引用前应复核扫描件。
- 并非每卷都有 MEGAdigital 印刷页标签；OCR 物理页不能自动转成正式 MEGA 页码。
- Sachregister 目前以页面级索引为主，复杂双栏条目可能混入相邻词条页码，候选资格层负责再次核验正文信号。
- 语义“主要”“典型”“转折点”等比较性问题仍需要语料级统计或人工编码，不能只靠普通 top-k RAG。

## Agent-first 规划策略

`planner_policy.py` 现在先判断本地计划是否充分，而不是默认调用模型。策略输出保存在 `planning_advice`：

- `local`：精确德语查询、已有结构化术语和普通单概念查询，本地完成，0 API token。
- `refinement_recommended`：跨时期、概念关系、强断言或多分支问题，建议让调用工作台的 Agent 补充计划。
- `refinement_required`：存在有意义的未知概念、缺少核心词或证据资格组，不能把本地宽检索结果直接当作答案依据。

外部 Codex、OpenCode 等应优先读取 `planning_advice`，然后通过 `agent_supplied` 提交结构化 refinement。没有外部 Agent 时，可使用 `--auto`：本地计划充分则保持 local；否则先查找已晋升的规划记忆，再回退到受约束的 Flash 规划。

规划器只接收紧凑 QueryPlan、精确词项统计和少量 Sachregister 导航，不接收检索所得的原文证据，因此规划调用不会消耗大段文献 token。

## 规划缓存与安全记忆

`query_plan_model.py` 和 `research_plan_model.py` 的 Flash 结果使用缓存键：

```text
normalized question
+ index_version
+ local planner_version
+ prompt_version
+ model
```

重复问题在版本未变化时直接复用结构化 refinement，API token 为 0。缓存只解决重复计算，不等于长期学习。

长期经验由 `planning_memory.py` 管理，状态严格分为：

```text
proposed
  -> corpus_validated
  -> promoted

proposed / corpus_validated / promoted
  -> rejected
```

- Agent 或 Flash 的新 refinement 只能自动进入 `proposed`。
- `memory-validate` 只检查核心德语词是否真实命中本地 MEGA TEXT，不判断概念是否同义。
- `memory-promote` 必须由用户显式执行并填写审查说明。
- `--auto` 只复用 `promoted`，绝不复用 proposed 或仅通过词面验证的候选。
- 记忆库损坏或不可用时，查询继续按 local/hybrid 回退，不阻断检索。

## 词项计数口径

`term_probe.py` 的汇总同时返回：

- `summed_term_page_hits`：各词项页面数相加，允许同一页面重复计入；
- `unique_page_hits` / `total_page_hits`：所有词项合并后的唯一索引页面记录；
- `unique_text_page_hits`、`unique_apparat_page_hits`；
- `overlap_page_hits`：逐词累计与唯一页面数的差额。

因此，不再把 `Verkehr`、`Verkehrung` 等多个检索词的累计页面数误称为一个概念的总出现页数。正式词频研究仍须另行定义词形集合、分母、版本见证和去重口径。

## 来源目录与文本版本层

检索载体、MEGA 卷册和文本版本现由 `source_catalog.py` 分层表示。自动构建仅声明可以从索引元数据可靠推出的关系：同卷 TEXT/APPARAT，以及同卷 MEGAdigital/OCR 并行覆盖。跨卷版本史由 `source_catalog.yaml` 明示，并带有 provenance、confidence 与 scope note。

这一层在 rerank 与 snippet 之后进行 enrichment，不参与分数计算。因此它不会为了已有版本规则压制意外证据，也不会把来源图谱变成新的硬过滤器。研究包保留目录版本号，使同一检索在目录更新前后可复现和审计。

## Agent 修订后的完整研究流水线

```text
用户复合问题
  -> 本地 ResearchPlan（分支、证据类型、范围继承）
  -> Agent 对每个分支提交受约束 QueryPlan refinement
  -> 核心词的有限历史拼写展开
  -> 全局召回 + 目标卷召回注入 + Sachregister 导航
  -> 语义、范围、来源三轴资格判断
  -> 分支内证据功能选择（定义、区分、关系、历时）
  -> claim-evidence matrix
  -> 选中证据展开
  -> 结构化 report-check
  -> artifact revision 绑定的完成凭证
```

外部 Agent 负责不可机械化的语义判断：哪些德语词是概念核心、哪些只是上下文、各分支需要哪个卷册、理论桥接应如何表述。工作台负责可重复的执行与安全边界：检索、范围召回、来源分类、证据选择、门禁、修订和报告校验。

### 历史拼写而非概念臆测

Agent 显式提交的核心词会经过有界的正字法变体生成，例如 `Kapital/Capital`、`produktive/productive Arbeit`。生成结果记录在 `generated_historical_variants` 中，并进入显式 focus 资格组。该层不补充新概念，也不把相关词自动当作同义词。

### 分支证据选择

`research_orchestrator.py` 为每个分支检索比最终输出更大的候选池，然后优先保留具有相应文本功能的段落：

- 定义/区分：`Bestimmung`、`Unterscheidung`、`nicht ... sondern` 等；
- 概念关系：核心词与关系语境共现；
- 历时比较：不同目标时期或卷册的代表性正文。

最终仍受候选资格和来源门禁约束。高分 APPARAT、编辑导论或范围外页面不能仅凭 RRF 分数成为作者论证证据。

### 三轴准备度

- `semantic_ready` 只回答“内容是否相关”；
- `scope_ready` 只回答“是否在目标著作/卷册”；
- `provenance_ready` 只回答“是否确认了作者/编者来源层”。

三者共同决定 `claim_ready`。未核验 OCR 可以在语义和范围上合格，并作为 `provisional_claim_evidence` 支持明确披露限制的分析，但不能成为自动逐字引文。运行级警告 `provisional_source_layer_requires_disclosure` 防止 Agent 忽略这一差别。

### 正式会话修订

每次重新检索都会产生 artifact revision，旧修订中的局部 `E###` 与展开状态不会静默沿用。正式报告必须逐分支使用当前修订中已展开的证据；完成凭证绑定活动修订、检索产物和报告哈希。
