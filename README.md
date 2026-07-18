# MEGA RAG

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
python evidence_library.py status --json
python evidence_library.py import research_exports
python test_evidence_library.py
```

修改 `glossary.yaml`、`query_analyzer.py`、`rerank.py`、`webui.py`、`text_layer.py`，或重建索引后，必须运行回归测试。8 项快速算法回归使用 `test_retrieval_regression.py`；20 项论文研究型对照及防回退基线使用 `eval_retrieval.py --fail-on-regression`。运维和恢复步骤见 [docs/operations.md](docs/operations.md)。检索架构见 [docs/retrieval_architecture.md](docs/retrieval_architecture.md)，文献层级规则见 [docs/text_layer_classification.md](docs/text_layer_classification.md)，passage 构建与恢复见 [docs/passage_index.md](docs/passage_index.md)，研究评估方法见 [docs/research_evaluation.md](docs/research_evaluation.md)，Codex/论文研究包见 [docs/research_export.md](docs/research_export.md)。

研究包的长期积累、人工审核和论文/Codex 导出见 [docs/evidence_library.md](docs/evidence_library.md)。

当前 20 项基线中混合检索为 20/20，尚无证据表明需要为全部 11,647 条 MEGAdigital 记录补向量；保持 FTS 和目标卷权威文本注入即可，待出现可复现的语义召回缺口后再做选择性向量试验。

## 数据边界

- `metadata.db`、`cache.db`、`vectors.lancedb/`、备份和日志是本机运行数据，不进入 Git。
- `config.yaml` 是本机配置，不进入 Git；仓库只保留 `config.example.yaml`。
- `research_exports/` 是本地研究输出，不进入 Git；Markdown/JSON 中的未验证页码必须在正式引用前核查。
- `research_library.db` 保存人工审核；`research_library_exports/` 保存论文证据集，均不进入 Git。
- MEGAdigital 与 OCR 记录不相互覆盖，优先级由检索重排处理。
- `index_health.py` 是只读检查；出现 `ERROR` 时不要继续写库，先按运维文档恢复。
