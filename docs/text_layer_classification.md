# MEGA² 文献层级分类

## 为什么需要这一层

MEGA² 的 `TEXT` 表示 Textband，不等于“这一页全部是马克思或恩格斯原文”。Textband 还可能包含编者导言、编辑说明、目录和其他卷首材料。旧字段 `is_main_text` 只保留卷别语义，不能单独用于作者归属判断。

分类器 v1 采取保守策略：只自动确认可靠的结构类型，不能可靠判断的 OCR Textband 页面保持 `textband_unclassified`。这类页面仍可检索，但系统和模型不得仅凭 `TEXT` 将其称为作者原文。

## 数据字段

`chunks` 表新增四个字段：

- `text_layer`: 层级类别。
- `text_layer_confidence`: 0 到 1 的自动分类置信度。
- `text_layer_provenance`: 分类依据或人工覆盖来源。
- `text_layer_version`: 分类器版本。

当前类别：

| text_layer | 含义 | 自动用途 |
| --- | --- | --- |
| `author_text` | 结构化来源已确认的作者文本 | 可自动标为作者原文；v1 仅用于 MEGAdigital 结构化文本 |
| `apparatus` | APPARAT 校勘与编者考证 | 版本、异文、编者注查询优先 |
| `editorial_intro` | Textband 中的编者导言 | 正文论述型查询降权 |
| `editorial_note` | 编辑说明 | 正文论述型查询降权，编者问题可保留 |
| `table_of_contents` | 目录 | 证据检索显著降权 |
| `front_matter` | 卷首材料 | 证据检索显著降权 |
| `register` | 索引 | 正文论述型查询降权 |
| `illustration_list` | 插图目录 | 证据检索显著降权 |
| `textband_unclassified` | Textband 页面但无法自动判断具体层级 | 可检索，不自动认定为作者原文 |

## v1 审计结果

2026-07-16 对 82,808 条记录完成分类：

- `textband_unclassified`: 36,301
- `apparatus`: 33,125
- `author_text`: 11,647
- `editorial_intro`: 937
- `table_of_contents`: 458
- `editorial_note`: 266
- `front_matter`: 74

详细抽样与按卷统计见 `docs/text_layer_audit.md` 和 `docs/text_layer_audit.json`。

## 操作命令

```powershell
# 只读审计并刷新报告
python audit_text_layers.py

# 只计算将要修改的行，不写数据库
python apply_text_layers.py

# 写入分类字段；执行前应确认已有一致性备份
python apply_text_layers.py --apply

# 固定样例、索引健康和检索防回退
python test_text_layer.py
python index_health.py
python test_retrieval_regression.py --verbose
```

`apply_text_layers.py` 只更新新增元数据字段，不改 `chunk_text`、FTS 内容或向量。更新会生成新的 `index_version`，旧查询缓存不会误用。

## 人工覆盖

自动分类不覆盖 `text_layer_provenance` 以 `manual:` 开头的记录。人工核验后可按以下约定写入：

```text
text_layer = author_text | editorial_intro | editorial_note | ...
text_layer_confidence = 1.0
text_layer_provenance = manual:<核验人>/<日期>/<简短依据>
text_layer_version = manual-v1
```

人工覆盖前应记录目标 `chunk id`、卷册、页码和判定依据，并先备份数据库。不要批量把所有 `TEXT` 页面改成 `author_text`。

## 检索行为

- `author_argument`: 高置信编者导言、编辑说明、目录和卷首材料降权。
- `apparat_question`: APPARAT 与编辑说明保持较高权重。
- `textband_unclassified`: 不加作者身份奖励，也不因不确定性被删除。
- Flash 证据输入和 Web UI 原始结果都显示 `text_layer` 标签。
- Pro 只能沿用证据卡片中的层级，不能自行把未分类 Textband 改称作者原文。

## 回滚

完整迁移前快照位于 `backups/full_index_20260716_203126`。分类字段是增量元数据；若需要恢复，应按 `docs/operations.md` 的停服务、校验快照、替换数据库、再运行健康检查的顺序操作。