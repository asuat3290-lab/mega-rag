# Passage v2 索引

## 目标

Passage v2 在现有页级 `chunks` 和 LanceDB 向量之外增加页内检索单元。它不删除页级索引，也不额外调用 API。主要用途是缩短交给重排器和证据卡片的文本，同时保留 MEGA 卷册、页码、文献层级和精确字符位置。

## 当前状态

2026-07-16 全量构建结果：

- 合格页面：81,552
- passage：109,729
- `passages_fts`：109,729
- 页面覆盖率：100%
- 孤儿记录：0
- dirty：false
- complete：true
- 配置哈希：`b3250dd7fa83a69f`
- passage 平均长度：1,694 字符，平均 265 个近似词元
- 原页级记录平均长度：2,164 字符

1,256 条过短、空白或 OCR 状态为 failed 的页面不建立 passage，仍可通过原页级索引处理。

## 切分规则

默认配置：

```text
target_tokens = 260
min_tokens = 80
max_tokens = 420
overlap_tokens = 50
schema = passage-v2
```

这里的 token 是按空白划分的近似词元，不是某个模型的计费 token。切分器优先选择目标长度附近的空段落边界；没有可靠段落边界时按词元位置切分。每条 passage 保存 `char_start` 和 `char_end`，并保证：

```python
page_text[char_start:char_end] == passage_text
```

## 增量与中断恢复

passage ID 由以下信息稳定生成：

```text
page_id + page content hash + chunk config + passage number + character offsets
```

再次运行时，只处理以下页面：

- 尚无 passage 的页面；
- `content_hash` 已变化的页面；
- 切分配置发生变化的页面。

构建开始时设置 `passages_fts_dirty=1` 和 `passages_complete=0`。如果关机、部分构建或进程中断，Web UI 不读取半成品 passage，而是自动回退到原页级 FTS。下次运行同一命令会继续缺失页面；全部覆盖后只重建一次 passage FTS，并设置 `dirty=0`、`complete=1`。没有页面变化时命令走 no-op 快速路径，不重建 FTS，也不重新计算索引版本。

## 检索融合

```text
passage BM25
  + page-level bge-m3 vector result
  → 同页向量分数加强最佳 passage
  → semantic-only 页面保留页级回退
  → scoped target-volume recall 按 page_id 去重
  → rerank / text layer / snippet
```

当前没有为 109,729 条 passage 单独生成向量。页级向量负责语义召回，passage FTS 负责精确定位，这是当前机器上成本和效果较平衡的方案。

## 命令

```powershell
# 状态
python passage_index.py --status

# 增量构建或断点续跑
python passage_index.py --build

# 修改切分参数后全量重切
python passage_index.py --build --rebuild --target-tokens 260 --overlap-tokens 50

# 兼容原 build_index.py 命令
python build_index.py --chunk-pages
python build_index.py --chunk-pages --rechunk

# 验证
python test_passage_index.py
python index_health.py
python test_retrieval_regression.py
```

不要手工删除 `passages_fts`。出现 dirty 或数量不一致时，直接重新运行 `python passage_index.py --build`；系统会补齐后重建 FTS。

全量完成后的可恢复快照：ackups/full_index_20260716_220015，索引版本：e6b0ad86814862f4。
