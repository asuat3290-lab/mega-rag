# MEGA² 论文证据库

`evidence_library.py` 将一次性的研究包转化为可长期积累、人工审核和论文回填的证据库。它只处理本地 JSON、SQLite 和 Markdown，不调用 DeepSeek，也不产生 API token 费用。

## 设计目标

证据库与检索主索引分离：

- `metadata.db` 和 LanceDB 继续负责召回与排序；
- `research_library.db` 只保存研究包证据、人工审核和审计历史；
- 导入、审核或导出失败不会修改 OCR、FTS5、passage 或向量索引；
- SQLite 采用事务和 WAL，重复导入同一研究包是幂等操作。

数据流如下：

```text
研究问题
  -> 本地混合检索
  -> Markdown/JSON 研究包
  -> 证据库导入与跨包去重
  -> 人工接受 / 驳回 / 待核验
  -> 直译、主张、论文位置、标签与页码核验
  -> Markdown/JSON 论文证据集
  -> Codex 章节写作或论文文献回填
```

## 文件

- `evidence_library.py`：SQLite schema、导入、去重、审核、查询和导出 CLI。
- `evidence_library_ui.py`：Gradio 证据库界面。
- `research_workbench.py`：组合原检索 UI 与证据库 UI。
- `启动MEGA研究工作台.bat`：一键启动并打开浏览器。
- `test_evidence_library.py`：导入、去重、审计和导出的防回退测试。

## 不可变来源与可变审核

`evidence_items` 保存检索时的德语上下文、页面/段落 ID、文本层级、来源 URL、内容哈希和引用定位。首次导入后的非空来源字段不会被后续研究包覆盖。

后续包如果提供了首次缺失的来源信息，可以进行“单调补全”：

- 只填充原来为空的字段；
- 印刷页码出现后，可把平台内部页升级为文本页定位；
- 每次补全写入 `provenance_history`；
- 已存在的来源标题和德语上下文不会被替换。

`reviews` 保存可编辑的研究判断：

- `unreviewed`：未审核；
- `accepted`：可进入论文证据集；
- `needs_verification`：证据相关，但页码、OCR 或归属仍需核验；
- `rejected`：不应使用。

每次审核更新都会增加修订号，并在 `review_history` 中记录旧值与新值。重新导入研究包不会覆盖人工审核。

## 一键使用

双击：

```text
D:\mega_rag\启动MEGA研究工作台.bat
```

浏览器将打开 `http://127.0.0.1:7860`。原有检索界面保持不变，页面下方新增“论文证据库”。

推荐流程：

1. 在检索区输入问题并点击“导出研究包”。
2. 在“论文证据库 -> 导入与清单”点击“导入最新研究包”。
3. 复制清单中的 `L######` 编号，在“审核证据”中读取。
4. 核对德语原文、层级和页码，再填写主张、直译、笔记、论文位置与标签。
5. 选择 `accepted` 或 `needs_verification` 并保存。
6. 在“导出论文证据集”中按状态、论文位置或标签导出。

## CLI

初始化和状态：

```powershell
python evidence_library.py init
python evidence_library.py status --json
```

导入一个文件或整个目录：

```powershell
python evidence_library.py import research_exports\research_*.json
python evidence_library.py import research_exports
```

查看证据：

```powershell
python evidence_library.py list --status unreviewed --limit 50
python evidence_library.py list --search "allgemeine Arbeit"
python evidence_library.py list --section "第二章" --tag "抽象劳动"
```

审核证据：

```powershell
python evidence_library.py review L000001 `
  --status accepted `
  --claim-supported "该段支持的具体判断" `
  --translation "德语直译" `
  --notes "文献学说明" `
  --section "第二章 第一节" `
  --tags "一般劳动,抽象劳动" `
  --print-page 33 `
  --locator-verified `
  --verified-by "研究者姓名"
```

导出已接受证据：

```powershell
python evidence_library.py export --status accepted
python evidence_library.py export --status accepted --section "第二章" --tag "抽象劳动"
```

默认输出目录为 `D:\mega_rag\research_library_exports`。可以在 `config.yaml` 中配置：

```yaml
paths:
  research_library_db: "D:/mega_rag/research_library.db"
  research_library_exports: "D:/mega_rag/research_library_exports"
```

## 引用边界

证据库严格区分：

- MEGAdigital 文本页标签：可作为页码草案，正式脚注仍应核对版本信息；
- MEGAdigital 平台内部页：只是定位线索；
- OCR PDF physical page：扫描文件物理页，不能直接当作 MEGA 印刷页；
- APPARAT、编者导言和编者注：不得归于马克思或恩格斯；
- 未分类 Textband：不能仅凭位于 TEXT 卷就自动认定为作者原文。

`citation_ready=false` 的材料可用于继续查找，但不应直接进入论文脚注。人工核验后应填写印刷页码、勾选“已人工核验引用定位”，并记录核验人。

## 交给 Codex

优先导出 `accepted` 状态的 Markdown 证据集，再把文件交给 Codex。建议约束：

```text
仅依据证据集中的德语上下文补写或修改论文。每个实质性判断保留 L######；
区分作者原文、编者材料和未分类文本；citation ready: no 的材料不得生成正式脚注；
证据不足时明确指出缺口，不得补造引文、页码或作者归属。
```

证据集只保留与问题相关的上下文，因此比把整页或整卷交给模型便宜。导出顶部会显示粗略输入 token 数；导出动作本身没有 API 开销。

## 测试与备份

修改证据库代码后运行：

```powershell
python test_evidence_library.py
python test_research_export.py
python test_retrieval_regression.py
```

第一条测试覆盖：重复导入、跨包去重、人工审核防覆盖、来源单调补全、审核历史、引用边界和 Markdown/JSON 导出。

备份时复制以下文件即可：

```text
D:\mega_rag\research_library.db
D:\mega_rag\research_library.db-wal
D:\mega_rag\research_library.db-shm
```

最好先关闭研究工作台再复制；若程序仍运行，应同时复制数据库、WAL 和 SHM 文件。

## 当前初始化状态

2026-07-18 首次导入现有 4 个研究包后：

- 20 条唯一证据；
- 36 个“研究包—证据”关联；
- 2 条带 MEGAdigital 文本页定位；
- 1 条来源补全历史；
- 20 条均为 `unreviewed`，尚未自动替用户作出接受或驳回判断。
