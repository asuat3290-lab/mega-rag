# MEGA RAG

本地优先的 MEGA² 文献检索系统。OCR 与权威 MEGAdigital 文本保留来源标记，SQLite FTS5 提供低成本全文召回，LanceDB + bge-m3 提供可选语义召回，DeepSeek 只处理筛选后的证据。

## 快速使用

1. 将 `config.example.yaml` 复制为 `config.yaml`，填写本机 D 盘路径。
2. 确认 Ollama 已运行且存在 `bge-m3`：`ollama list`。
3. 检查索引：`python index_health.py`。
4. 启动界面：双击 `启动MEGA文献检索.bat`。
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
```

修改 `glossary.yaml`、`query_analyzer.py`、`rerank.py`、`webui.py`、`text_layer.py`，或重建索引后，必须运行回归测试。运维和恢复步骤见 [docs/operations.md](docs/operations.md)。检索架构见 [docs/retrieval_architecture.md](docs/retrieval_architecture.md)，文献层级规则见 [docs/text_layer_classification.md](docs/text_layer_classification.md)，passage 构建与恢复见 [docs/passage_index.md](docs/passage_index.md)。

## 数据边界

- `metadata.db`、`cache.db`、`vectors.lancedb/`、备份和日志是本机运行数据，不进入 Git。
- `config.yaml` 是本机配置，不进入 Git；仓库只保留 `config.example.yaml`。
- MEGAdigital 与 OCR 记录不相互覆盖，优先级由检索重排处理。
- `index_health.py` 是只读检查；出现 `ERROR` 时不要继续写库，先按运维文档恢复。