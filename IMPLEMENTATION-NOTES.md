# MegaRAG — historical implementation notes

> Local corpus counts and evaluation results below are not reproducible benchmarks
> bundled with this repository. Windows launcher scripts mentioned below are not
> included in this code-only snapshot. Start with [README.md](README.md).

本地优先的 MEGA² 文献检索系统。OCR 与权威 MEGAdigital 文本保留来源标记，SQLite FTS5 提供低成本全文召回，LanceDB + bge-m3 提供可选语义召回，DeepSeek 只处理筛选后的证据。

## 快速使用

1. 将 `config.example.yaml` 复制为 `config.yaml`，填写本机 D 盘路径。
2. 确认 Ollama 已运行且存在 `bge-m3`：`ollama list`。
3. 检查索引：`python index_health.py`。
4. 启动完整研究工作台：双击 `启动MEGA研究工作台.bat`；旧检索入口仍可使用。
5. 后台补向量可双击 `继续补建向量.bat`；关机前双击 `暂停补建向量.bat`。

## 常用命令

```powershell
python build_index.py --build --no-embed
python build_index.py --repair-vectors
python build_index.py --vector-status
python passage_index.py --status
python test_passage_index.py
python index_health.py
python test_retrieval_regression.py
python eval_retrieval.py --modes page,passage,hybrid --top-k 10
python research_export.py "一般劳动是什么" --top-k 12
python test_research_export.py
python mega_agent.py status
python mega_agent.py research-plan "马克思怎样讨论利润率下降，人工智能是否会降低利润率，如何用机器理论说明"
python mega_agent.py research-run "同一复合问题" --detail index
python mega_agent.py search "利润率下降" --detail index --save
python mega_agent.py verify "你的观点" --budget brief
python test_claim_audit.py
python test_agent_service.py
python evidence_library.py status --json
python evidence_library.py import research_exports
python test_evidence_library.py
```

完整的非技术使用方法、系统架构、实现文件、成本控制和故障处理见 [docs/product_guide.md](docs/product_guide.md)。

修改 `glossary.yaml`、`query_analyzer.py`、`concept_retrieval.py`、`rerank.py`、`webui.py`、`text_layer.py`，或重建索引后，必须运行回归测试。10 项快速算法回归使用 `test_retrieval_regression.py`；20 项论文研究型对照及防回退基线使用 `eval_retrieval.py --fail-on-regression`。运维和恢复步骤见 [docs/operations.md](docs/operations.md)。检索架构见 [docs/retrieval_architecture.md](docs/retrieval_architecture.md)，文献层级规则见 [docs/text_layer_classification.md](docs/text_layer_classification.md)，passage 构建与恢复见 [docs/passage_index.md](docs/passage_index.md)，研究评估方法见 [docs/research_evaluation.md](docs/research_evaluation.md)，Codex/论文研究包见 [docs/research_export.md](docs/research_export.md)。

研究包的长期积累、人工审核和论文/Codex 导出见 [docs/evidence_library.md](docs/evidence_library.md)。

当前 20 项基线中混合检索为 20/20，尚无证据表明需要为全部 11,647 条 MEGAdigital 记录补向量；保持 FTS 和目标卷权威文本注入即可，待出现可复现的语义召回缺口后再做选择性向量试验。

## 数据边界

- `metadata.db`、`cache.db`、`vectors.lancedb/`、备份和日志是本机运行数据，不进入 Git。
- `config.yaml` 是本机配置，不进入 Git；仓库只保留 `config.example.yaml`。
- `research_exports/` 是本地研究输出，不进入 Git；Markdown/JSON 中的未验证页码必须在正式引用前核查。
- `research_library.db` 保存人工审核；`research_library_exports/` 保存论文证据集，均不进入 Git。
- MEGAdigital 与 OCR 记录不相互覆盖，优先级由检索重排处理。
- `index_health.py` 是只读检查；出现 `ERROR` 时不要继续写库，先按运维文档恢复。

## 专家型 QueryPlan 与 Agent 接口

研究型检索现在先生成结构化 QueryPlan，再进入全局检索、目标卷注入、Sachregister 导航、候选资格判定和证据输出。默认 `local` 模式不调用 API；`--hybrid` 会显式使用 Flash，`--auto` 则只在本地计划不足且没有已晋升规划记忆时使用 Flash。

```powershell
cd D:\mega_rag
python mega_agent.py status
python mega_agent.py plan "马克思如何讨论资本集中" --no-probe --no-register
python mega_agent.py search "马克思如何讨论资本集中" --detail index
python mega_agent.py verify "马克思主要用异化描述资本关系" --local-only
python mega_agent.py research-plan "复合研究问题"
python mega_agent.py research-run "复合研究问题" --detail index
```

外部 Agent 可以先调用 `plan`，必要时通过 `--refinement-file plan.json` 补充德语词形和目标卷，再调用 `search --detail index`。只展开少量选中的 `E###` 证据，可以显著降低 token 消耗。完整架构和协议见 [docs/expert_retrieval_architecture.md](docs/expert_retrieval_architecture.md) 与 [docs/agent_query_plan.md](docs/agent_query_plan.md)。

复合问题应先调用 `research-plan` 检查问题覆盖，再用 `research-run` 逐分支检索。系统会把 MEGA 原文重建、概念桥接、反作用因素和当代经验要求分开，并禁止把 `detail=index` 的短预览直接当作正式引文。详见 [docs/research_orchestration.md](docs/research_orchestration.md)。

修改 QueryPlan、检索分支、候选资格或观点核验逻辑后，应运行：

```powershell
python test_query_plan.py
python test_auxiliary_retrieval.py
python test_retrieval_quality.py
python test_query_plan_model.py
python test_claim_audit.py
python -B -m unittest test_research_plan test_research_orchestrator test_research_plan_model test_agent_service -v
python test_retrieval_regression.py
```

## Evidence and agent safety contract

Retrieval candidates are not automatically evidence. Every search package exposes `synthesis_gate`, phrase-aware qualification, provenance status, citation readiness, and a stable `evidence_uid`. `E001` identifiers are local to one package only.

For compound research, external agents must use:

```powershell
python mega_agent.py research-plan "研究问题"
python mega_agent.py research-run "研究问题" --detail index --save
python mega_agent.py evidence research_run_xxx.json ev_xxx --detail full
python mega_agent.py report-check report.json research_run_xxx.json
```

A closed synthesis gate means the output is a diagnostic candidate package, not a basis for a positive answer. See [docs/evidence_report_contract.md](docs/evidence_report_contract.md) and [AGENTS.md](AGENTS.md).

## Agent-first 与安全学习

简单查询继续使用 local 模式，复杂问题可由外部 Agent 提交结构化 refinement，或使用 `--auto` 按需调用 Flash：

```powershell
python mega_agent.py plan "Subsumtion Hegelschen Rechtsphilosophie" --auto --no-probe --no-register
python mega_agent.py research-plan "马克思某一概念的含义变化" --auto
python mega_agent.py memory-status
python mega_agent.py memory-list --status proposed
```

系统不会把模型输出自动写成正式术语知识。新计划必须经过本地语料验证和人工晋升后，才会被后续 `--auto` 查询复用。完整规则见 [docs/expert_retrieval_architecture.md](docs/expert_retrieval_architecture.md) 和 [docs/agent_integration.md](docs/agent_integration.md)。

## 来源目录与版本关系

来源目录将 OCR 文件夹、MEGAdigital 文档、MEGA 卷册范围和经人工声明的版本关系分开保存：

```powershell
python mega_agent.py source-catalog-build
python mega_agent.py source-catalog-status
python mega_agent.py source-catalog-list --abteilung II --band 5
python mega_agent.py source-catalog-show SOURCE_ID
```

检索结果会携带稳定 `source_id`、`edition_status`、版本组和来源关系。目录只增加身份与解释边界，不改变 BM25、向量召回或 rerank 分数。详细说明见 `docs/source_catalog.md`。

覆盖与词项调查也复用这两条本地只读链路：

```powershell
python mega_agent.py source-catalog-coverage --work "Das Kapital" --language de --page-size 20
python mega_agent.py term-probe "Arbeit" --page 1 --page-size 50 --language de --export probe.json
```

覆盖输出区分作品/版本/卷次、语言、TEXT/APPARAT 和索引记录数，并保留
`unknown`/`mixed` 与完整性未评估状态。词项输出区分
`exact_phrase`、`lexical_variant`、`semantic_related`，保留原文命中、稳定
ID、版本和分页；无命中不等于语料不存在。上述命令不运行检索评测、不调用
模型，也不补建向量。

## Agent focus and protocol audit

External agents can keep free-form exploration while declaring only the evidence-critical parts of a query:

```powershell
python mega_agent.py search "Stoffwechsel Natur Arbeit Boden" `
  --focus Stoffwechsel --context "Natur,Arbeit,Boden" `
  --volumes "II/1,II/5,II/6" --intent author_argument `
  --trace-id paper-ch3 --detail index
python mega_agent.py protocol-report paper-ch3
```

`--focus` is required in qualified evidence; `--context` is recall-only; `--volumes` is a soft scope. Tracing is optional and advisory. Search automatically consults Sachregister, while synthesis and quotation safety remain fail-closed. See [docs/agent_research_protocol.md](docs/agent_research_protocol.md).

Run the focused regressions after changing planning, qualification, scope propagation, CLI/MCP plumbing, or tracing:

```powershell
python -B -m unittest test_agent_focus_flow test_agent_hint_plumbing test_query_scope_profile test_protocol_trace -v
```
## Agent 正式研究会话

自由检索适合探索；正式交付使用可恢复的状态机：

```powershell
python mega_agent.py research-session start "研究问题"
python mega_agent.py research-session continue SESSION_ID --focus "核心词" --volumes "II/1"
python mega_agent.py research-session expand SESSION_ID E001 --detail full
python mega_agent.py research-session finalize SESSION_ID --report report.json
```

只有 `COMPLETE` 状态会签发完成凭证。关机后用 `research-session status SESSION_ID` 继续。详见 [docs/formal_research_sessions.md](docs/formal_research_sessions.md)。

### 正式研究的当前约束

复合问题应在 `research-plan` 后按子问题分别提交 `query_refinement`；Agent 显式指定的 `intent`、核心德语词和目标卷会进入可审计计划，现代 `k` 拼写还会有限补入 MEGA 常见的历史 `c` 拼写。每个可回答分支都必须在当前 artifact revision 中展开并引用自己的证据。`claim_eligible` 仅表示可以进行有限分析，不等于 `quote_eligible`；全部证据为未核验 OCR 时会返回来源披露警告。

门禁关闭或会话继续被阻止时 CLI 返回退出码 `5`。工作台报告的 `api_tokens=0` 只表示工作台未调用外部 API，不包含 Codex、OpenCode、Qoder 等调用 Agent 自身的 token。

修改规划、检索资格、分支编排、来源层或正式会话代码后运行：

```powershell
python -B -m unittest discover -p "test_*.py"
python -B test_retrieval_regression.py
```
