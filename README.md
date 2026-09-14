# MegaRAG

### 检索马克思的文本，也检索文本的版本与边界。

**Local-first, provenance-aware retrieval for MEGA² research.**

面向马克思研究、概念史与版本比较的本地检索工作台。它把词项命中、来源身份、TEXT / APPARAT 和可引用性分开，让 Agent 不必把一个相似片段直接当作作者的论证。

**开发阶段 · 语料自备 · 不附带 MEGA² 原文或预建索引。**

[研究场景](#一个值得这样检索的问题) · [无语料试验](#先做无语料试验) · [来源目录](docs/source_catalog.md) · [贡献](CONTRIBUTING.md)

**在其他 Windows 电脑安装：**见 [ZIP 安装包说明](docs/windows-install.md)。
支持独立环境、目标电脑路径配置、UI / CLI / MCP 启动；需要 Python 3.13，语料自备。
源码用户也可直接运行 `py -3.13 portable_install.py --profile full`。

## 一个值得这样检索的问题

> 马克思在不同手稿和《资本论》中使用 geistige Produktion，是否是在讨论同一个概念？

普通相似度检索容易给出一批相关段落，却没有回答：覆盖了哪些作品？命中的是原词还是相关概念？说话者是马克思还是编辑？

MegaRAG 提供分开的检查路径：

| 要核对的问题 | 能力 |
|---|---|
| 哪些作品、版本和语言真正进入索引？ | 有界 source catalog coverage；保留 unknown 和已知缺口 |
| 原词、词形变体还是语义相关？ | `exact_phrase` / `lexical_variant` / `semantic_related` 分层 |
| 原文还是编者说明？ | TEXT / APPARAT、来源 ID、版本组和定位信息 |
| 搜索预览能直接引用吗？ | 展开选中 evidence，检查来源与 quote eligibility |
| 没搜到是否等于不存在？ | `no_match_in_indexed_scope`，不推出全语料缺席结论 |

```mermaid
flowchart LR
    Q[问题与词项] --> C[语料 / 版本覆盖]
    C --> P[本地规划与检索]
    P --> E[命中分层与来源定位]
    E --> X[展开原文与资格检查]
    X --> R[结构化研究输出]
```

这是工作流示例，不是已经完成该概念史研究的结果。OCR 仍可能出错，来源关系也不等于段落对齐。

## 先做无语料试验

需要 Python 环境。下面的合成测试只用临时数据，不下载原文、不调用模型：

```powershell
git clone https://github.com/asuat3290-lab/mega-rag.git
cd mega-rag
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install PyYAML==6.0.2
Copy-Item config.example.yaml config.yaml
.\.venv\Scripts\python.exe -m unittest test_philology_coverage -v
```

测试导入会读取 `config.yaml`，但本测试把数据库操作指向合成临时库；复制示例配置不等于这些示例路径已有数据。不要覆盖已有个人配置。公开源码可直接 clone。完整工作台依赖在 [requirements.txt](requirements.txt)，安装与语料处理是单独步骤；不要把合成测试通过理解为真实检索效果已经验证。

## 接入自己的本地索引

1. 阅读 [操作指南](docs/operations.md) 与 [来源目录](docs/source_catalog.md)。
2. 安装完整依赖，将 `config.example.yaml` 复制为本地 `config.yaml`，填写你自己的路径。
3. 仅导入你有权使用的材料；建立并检查索引，保留来源和版本信息。
4. 已有索引就绪后，先做覆盖和词项调查：

```powershell
python index_health.py
python mega_agent.py source-catalog-coverage --work "Das Kapital" --language de --page-size 20
python mega_agent.py term-probe "geistige Produktion" --language de --page 1 --page-size 20
```

未建立索引时，这些命令不代表已有可检索内容。覆盖目录若尚未生成，按文档对自己的库建立派生目录。正式复合研究还须遵循 [Agent 研究协议](docs/agent_research_protocol.md)，不能用一次 term probe 代替概念史论证。

## 本地优先，不强制每次检索调用模型

- SQLite FTS5 提供词法召回；LanceDB / bge-m3 为可选语义检索路径。
- 本地规划与证据展开可供已运行的 Agent 使用；模型辅助路径需显式配置。
- 工作台 API usage 不包含调用它的 Agent 自身消耗。
- 德中比较取决于实际导入的语言与版本。中文查询不证明库中有中文原文。
- 不提供普遍适用的召回率承诺；历史本地评测数值不是随仓库附带的可复现 benchmark。

## 与 research-kb / Max 配合

MegaRAG 侧重找到材料并说明其检索与来源边界；[research-kb / Max](https://github.com/asuat3290-lab/research-kb) 侧重保存候选论证、版本、审批与 Agent 交接。它们是独立组件，不是安装任一仓库就自动配置好的全套产品。

[架构](docs/retrieval_architecture.md) · [来源层级](docs/text_layer_classification.md) · [证据报告契约](docs/evidence_report_contract.md) · [历史工程说明](IMPLEMENTATION-NOTES.md) · [上传范围](UPLOAD-SCOPE.md)

欢迎优先贡献：小型合法语料的检索评测、历史拼写测试、版本归属修正、可复现安装问题。不要提交受版权限制的整卷原文、私人研究输出、密钥或本地数据库。

## 许可与使用

项目代码与自有文档采用 [MIT License](LICENSE)，欢迎使用、修改、分发和提交 Pull Request。MEGA² / MEGAdigital 文本、OCR 输入与第三方依赖不因此重新授权；请阅读 [第三方与语料边界](THIRD-PARTY-NOTICES.md)。软件按现状提供，不保证研究结论正确。
