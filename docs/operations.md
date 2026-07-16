# MEGA RAG 运维与恢复

## 一、每日状态检查

在 `D:\mega_rag` 运行：

```powershell
C:\Python313\python.exe index_health.py
```

关键条件：

- `OCR files` 等于 `Progress`，且 `untracked` 为 0。
- `Chunks / FTS` 两个数字相等。
- `duplicate` 和 `stale` 向量均为 0。
- `stored` 与 `computed` 索引版本一致。
- `SQLite` 为 `ok`。

`ocr_chunks_missing_vectors` 只表示语义索引尚未追平；FTS 全文检索仍可使用。

## 二、断点续跑

全文索引先追平：

```powershell
C:\Python313\python.exe build_index.py --build --no-embed
```

再补向量：

```powershell
C:\Python313\python.exe build_index.py --repair-vectors
```

两条命令都可中断后重跑。全文进度由 `index_progress` 记录；向量修复按 LanceDB 中实际缺失 ID 计算，不会重做已完成记录。

不使用命令行时，可双击：

- `继续补建向量.bat`：隐藏窗口后台续跑并记录 PID。
- `暂停补建向量.bat`：停止被跟踪的进程。
- `查看索引进度.bat`：查看 OCR、FTS、进度表和向量行数。

后台日志为 `vector_repair.stdout.log` 和 `vector_repair.stderr.log`。

## 三、安全停止

1. 前台运行时，在终端按 `Ctrl+C`。
2. 后台运行时，双击 `暂停补建向量.bat`。
3. 等待 Python 进程退出后再关机。
4. 下次开机先运行 `index_health.py`，再执行相同续跑命令。

不要在 Python 正在写 `metadata.db` 或 `vectors.lancedb` 时强制删除文件或复制不一致快照。

## 四、维护命令

```powershell
# 精确查看向量覆盖
C:\Python313\python.exe build_index.py --vector-status

# 清理历史重复向量；执行前应有快照
C:\Python313\python.exe build_index.py --dedupe-vectors

# 为旧记录补内容哈希
C:\Python313\python.exe build_index.py --backfill-hashes
```

## 五、备份

一致性备份必须包含：

- `metadata.db`（优先用 SQLite backup API）
- `cache.db`
- `vectors.lancedb/`
- OCR 项目的 `ocr_progress.db`
- `config.yaml`、`glossary.yaml` 与 Python 源码

当前 P0 前快照位于：

`D:\mega_rag\backups\pre_p0_20260716_140347`

恢复前先停止 Web UI、索引和 OCR 进程。不要只恢复 SQLite 而保留不匹配的旧向量库。

## 六、回归验证

```powershell
C:\Python313\python.exe -m compileall -q .
C:\Python313\python.exe index_health.py
C:\Python313\python.exe test_retrieval_regression.py
```

有 `FAIL_ALG` 时不得把当前改动视为可发布。`XFAIL_DATA` 只说明测试所需语料尚未覆盖。

## 七、API 密钥

密钥只放环境变量：

```powershell
[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', 'your-key', 'User')
```

不要把密钥写入 `config.yaml`、日志或 Git。