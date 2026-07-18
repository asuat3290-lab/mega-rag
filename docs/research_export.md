# 研究包导出

`research_export.py` 将本地检索结果导出为 Markdown 和 JSON，供 Codex、论文写作流程或人工文献核查继续使用。导出阶段只调用 SQLite FTS5、LanceDB 和本地 Ollama `bge-m3`，不会调用 DeepSeek Flash 或 Pro。

导出端与 Web UI 共用全局混合召回、核心词形逐项召回、目标卷注入、规则重排、词义组覆盖和记录级 snippet。它不会因为跳过 Flash / Pro 而退回旧候选池；不同 `senses` 仍会保留检索来源与分组标记。

## 使用方式

### Web UI

1. 输入研究问题并选择文献过滤、检索模式和重排方法。
2. 点击“导出研究包”。
3. 界面返回 Markdown 和 JSON 两个文件。

导出按钮会独立执行一次本地检索，不依赖 Flash 证据卡片是否已生成，因此断网时只要 Ollama 和本地索引可用，仍可导出。Ollama 不可用时，现有检索代码会退回全文召回。

### CLI

```powershell
python research_export.py "德意志意识形态中马克思如何谈论现实的人"
```

常用选项：

```powershell
python research_export.py "一般劳动是什么" `
  --top-k 12 `
  --route main_text `
  --retrieval-mode original_first `
  --rerank rule `
  --format both
```

输出目录依次取：

1. `--output-dir`；
2. `config.yaml` 中的 `paths.research_exports`；
3. 项目下的 `research_exports/`。

运行时导出目录已加入 `.gitignore`。

## 文件职责

Markdown 面向人工阅读和 Codex 上下文输入，包含：

- 研究问题与查询分析；
- 稳定证据编号 `E001`、`E002` 等；
- 来源、文献层级、命中术语与可靠性；
- 德语证据上下文；
- 引用定位状态和复核警告；
- 留给研究者填写的主张、直译与研究笔记。

JSON 面向程序处理，保留：

- `index_version`、`query_hash`、`package_id`；
- 页级和 passage 级记录 ID；
- `source_collection`、`source_url`、内容 hash；
- TEXT / APPARAT、文本层级及分类依据；
- RRF、最终得分和重排 debug 信号；
- 引文 hash、字符数和粗略 token 估算。

默认只导出围绕核心词截取的上下文，不复制整页全文，以降低后续模型输入成本。

## 页码与引用边界

研究包严格区分三种定位：

1. `megadigital_text_page`：MEGAdigital 提供 `page_label`，可生成类似 `MEGA² II/5, TEXT, S. 117` 的引用草案，但正式脚注仍应核对版本信息。
2. `megadigital_source_page`：只有数字平台内部页序，正式引用前必须核对印刷页。
3. `pdf_physical_page`：OCR PDF 的物理扫描页，只用于定位，不能直接作为 MEGA 印刷页码写入论文。

`locator_verified=false` 的证据会带有明确警告。系统不会把 PDF 物理页伪装成可直接使用的学术页码。

## 文献层级边界

- `structured_author_text`：MEGAdigital 结构化作者文本，可自动标记为作者原文。
- `editorial_apparatus`：APPARAT 与编者考证，只能归于编者。
- `editorial_material`：编者导言或编辑说明。
- `unclassified_textband`：位于 Textband，但尚未自动验证为作者原文。
- `unclassified_or_paratext`：未分类或目录、索引等副文本。

这些标签不替代人工校勘。尤其是 `unclassified_textband`，Codex 不应仅凭 TEXT 卷位置将其称为马克思或恩格斯原文。

## 交给 Codex 研究

建议把 Markdown 研究包附加到独立的研究任务，并使用如下约束：

```text
仅依据研究包中的 E### 证据回答。每个实质性判断注明证据编号；
区分作者原文、编者材料和未分类 Textband；locator_verified=false
的材料只能作为定位线索，正式脚注必须复核。证据不足时明确说明。
```

完成分析后，研究者可把确认后的主张、直译和页码写回 JSON 的 `review` 字段，形成可审计的“检索证据 -> 人工核查 -> 论文表述”链条。

## 成本

导出本身没有 API token 费用。Markdown 顶部的 `Approximate evidence tokens` 是后续将整个研究包交给模型时的粗略输入量，不是已经发生的费用。通常应先让 Codex读取证据索引，再只展开与论点有关的 `E###` 项目。

## 验证

```powershell
python test_research_export.py
python research_export.py "一般劳动是什么" --top-k 8
```
