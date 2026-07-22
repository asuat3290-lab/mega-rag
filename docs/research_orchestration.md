# 复合研究问题编排

## 目标

普通检索只回答“哪些德语词和卷册应该被搜索”。复合研究问题还需要回答：

- 问题包含哪些相互独立的主张；
- 每个主张需要 MEGA 原文、编者材料、语料统计还是当代经验材料；
- 所有必要分支是否都已获得足够证据；
- 哪些结论是原文直接支持，哪些只是理论推论或当代应用。

因此，系统在既有 QueryPlan 上增加 ResearchPlan，不替换原有 plan、search 和 verify。

## 流水线

~~~text
用户问题
  -> ResearchPlan：问题覆盖、分支类型、证据要求
  -> 每个 MEGA 分支的 QueryPlan：德语词形、目标卷、关系词
  -> 全局检索 + 目标卷召回 + Sachregister 导航
  -> 候选资格判定与分支充分性
  -> 多分支证据公平分配、去重
  -> 总体充分性与 claim-evidence matrix
  -> Agent 只展开选中的证据
~~~

### QueryPlan

query_plan.py 负责词项层：

- 核心词、历史词形、相关但不等价概念；
- 支撑关系词和通用词；
- 目标著作、目标卷和主题；
- 具体检索分支。

### ResearchPlan

research_plan.py 负责问题层。它把问题拆为以下证据类型：

- textual_reconstruction：重建马克思或恩格斯在原文中的论述；
- concept_relation / concept_bridge：证明概念之间的关系，并把解释性桥接明确标为推论；
- diachronic_comparison：要求多个有日期的时期或卷册；
- philology：同时使用作者文本与 APPARAT；
- counter_evidence：主动查找限制条件、反作用因素或反例；
- quantitative：要求语料级统计，不用普通 top-k 片段冒充词频结论；
- modern_application：区分 MEGA 理论重建与当代应用；
- external_empirical：明确要求 MEGA 以外的当代事实来源。

coverage_status=incomplete 表示仍有实质概念没有德语映射或研究分支，系统不应直接给出完整结论。

### 受控语义补全

默认 local 模式完全不调用 API。已知概念来自 glossary.yaml 和 query_rules.yaml；纯德语问题由 lexical fallback 直接处理。

遇到未知或歧义概念时有两种方式：

1. Agent 提交受约束的 refinement JSON；
2. 显式使用 --hybrid，由 Flash 只补研究分支和检索角色。

补全器不能回答研究问题，也不能把理论判断写成检索事实。

## 多分支执行

research_orchestrator.py 逐个执行所有需要 MEGA 的分支。每个分支保留：

- 检索充分性状态；
- 证据要求；
- 命中的证据 ID；
- term probe 和 Sachregister 导航摘要；
- 未满足原因。

证据预算采用轮转分配。只要预算不少于必要分支数，早期分支就不能耗尽全部名额，反作用因素、版本考证等后置分支至少能保留一条候选。

## 两级充分性

### 分支级

沿用现有 retrieval_adequacy：

- adequate
- partial
- retrieval_gap
- coverage_gap
- insufficient
- error

### 问题级

总体状态由全部必要分支共同决定：

- adequate：问题覆盖完整，所有 MEGA 分支充分，无外部证据缺口；
- partial：MEGA 分支可用，但仍缺当代经验材料，或某分支仅部分充分；
- retrieval_gap：至少一个必要 MEGA 分支未找到足够证据；
- incomplete_plan：问题本身仍有未映射概念。

一个成功分支不能掩盖其他失败分支。

## 主张与证据边界

输出中的 claim_evidence_matrix 区分：

- text_supported
- text_supported_qualification
- philological_finding
- comparative_inference
- theoretical_inference
- corpus_level_claim
- empirical_hypothesis

当代人工智能、产业利润率、企业行为等事实不能只靠 MEGA 证明。MEGA 可以提供理论范畴和机制，外部资料负责证明现实前提。

## 来源与引文门槛

每条完整证据增加：

- authorship_status
- edition_status
- source_quote_eligible
- attribution_note
- evidence.quote_eligible

edition_status 会区分手稿/草稿版本、编辑后的印刷版本、通信版本和其他批判版文本。APPARAT 与编者导言不会被标成作者原文。

--detail index 只返回短预览，并强制：

~~~json
{
  "preview_only": true,
  "quote_eligible": false
}
~~~

Agent 必须再请求 snippet 或 full，且来源本身满足页码、文本层和权威数字文本条件，才可把文字作为正式引文候选。最终论文引用仍应人工核对版本和页码。

## CLI

先规划复杂问题：

~~~powershell
python mega_agent.py research-plan "马克思怎样讨论利润率下降，人工智能是否会降低利润率，如何用机器理论说明"
~~~

执行全部 MEGA 分支：

~~~powershell
python mega_agent.py research-run "马克思怎样讨论利润率下降，人工智能是否会降低利润率，如何用机器理论说明" --top-k-per-branch 5 --max-evidence 12 --detail index
~~~

存在未知概念时可让外部 Agent 提交 JSON：

~~~powershell
python mega_agent.py research-plan "复杂问题" --refinement-file research_refinement.json
python mega_agent.py research-run "复杂问题" --refinement-file research_refinement.json --detail index --save
~~~

只有明确允许一次小模型规划调用时才使用：

~~~powershell
python mega_agent.py research-plan "复杂问题" --hybrid
~~~

普通单概念检索继续使用原命令：

~~~powershell
python mega_agent.py plan "利润率下降"
python mega_agent.py search "利润率下降" --detail index
~~~

## Agent 的低 token 策略

1. 复合问题先调用 research-plan，不要直接把整题交给一次 search。
2. 默认使用 local 和 detail=index，API token 为 0。
3. 查看 coverage_status、各分支状态和引文警告。
4. 只对要进入论证的少量 E### 请求 snippet 或 full。
5. 当代事实另行检索外部权威来源，并标为经验前提。
6. 最终写作按 claim-evidence matrix 组织，不把概念桥接说成马克思的直接原话。

## 扩展入口

新增概念：

- 在 glossary.yaml 增加德语词形和相关概念；
- 涉及稳定关系结构、目标卷或必查反作用因素时，在 query_rules.yaml 增加规则；
- research_branches 只添加该概念普遍需要的研究分支，不能为单个提问写特例。

新增问题类型或证据类型：

- 修改 research_plan.py 的类型和证据要求；
- 修改 research_orchestrator.py 的 claim type 与 allowed use；
- 增加对应回归测试。

## 回归测试

~~~powershell
python -B -m unittest test_research_plan test_research_orchestrator test_research_plan_model test_agent_service -v
python test_query_plan.py
python test_auxiliary_retrieval.py
python test_retrieval_quality.py
python test_retrieval_regression.py
~~~

重点防止以下回退：

- 复合问题遗漏某个语义部分；
- 未知中文概念被误认为已经映射；
- 纯德语问题必须依赖中文术语表；
- 第一个检索分支耗尽全部证据预算；
- 一个成功分支让总体状态错误变成 adequate；
- 索引预览被 Agent 当作正式引文；
- APPARAT 或编者文本被归于马克思、恩格斯。


## Evidence gate

Each branch now reports semantic relevance, provenance eligibility, and citation readiness separately. The run-level `synthesis_gate` preserves unresolved branches and external empirical requirements. Evidence carries a stable `evidence_uid`; package-local `E###` values are for display only.

An agent-authored report must pass `mega_agent.py report-check` before prose export. This prevents ad-hoc merging of multiple searches, duplicate evidence IDs, unsupported contemporary claims, and quotations from previews or unverified sources.
