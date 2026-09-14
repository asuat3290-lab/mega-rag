# Windows 安装包：0.1.0a1（开发预览）

这是可解压安装的代码包，不是自包含 EXE。目标：Windows 10/11、64 位
Python 3.13。无需管理员权限。Python、依赖、Ollama、模型和语料不随 ZIP 分发。

## 第一次安装

1. 从可信渠道安装 64 位 Python 3.13，启用 Python Launcher（`py` 命令）。
2. 将 ZIP 解压到本人有写权限的固定目录，例如用户文档中的 `MegaRAG`。
   不要放到 Program Files，也不要直接在 ZIP 预览中运行。
3. 双击 `Install-Windows.cmd`。默认 full 模式通过 pip 下载工作台和 MCP 依赖，
   创建本目录 `.venv`、`data/corpus` 和 `config.yaml`，执行安装检查。
   不安装系统服务、不修改其他 Agent 配置、不读取密钥、不导入语料或调用模型。
4. 只有显示 `INSTALLATION VERIFIED` 才算安装检查通过。失败时保留错误输出，
   不要将文件已存在当作安装成功。可在相同位置重跑；现有 config 不会被覆盖。

首次完整安装的依赖体积远大于 ZIP，需预留约 1–2 GiB 空间（不含语料、向量和模型）。
启动器关闭 Gradio/Hugging Face 的使用遥测；主动启用模型或下载功能仍会联网。

若仅试验合成样本（不含 UI / 完整 Agent CLI / MCP）：

```powershell
py -3.13 portable_install.py --profile minimal
```

升级至完整环境：`py -3.13 portable_install.py --profile full`。
可用 `--dry-run` 查看安装计划；它不创建文件、不联网。

## 原文与索引是另一步

安装包不附带 MEGA²、马恩全集中文版、个人论文或预建数据库。
将有权使用的 OCR 文本放入 `data/corpus`，按原有 [索引说明](operations.md)
保留作品、卷册、TEXT/APPARAT 和页码布局，再显式执行：

```powershell
py -3.13 portable_run.py build-index --build --no-embed
py -3.13 portable_run.py health
py -3.13 portable_run.py cli source-catalog-build
py -3.13 portable_run.py cli status
```

这些索引命令会写本机数据，应在确认语料和目标路径后执行；安装器不会代为运行。
健康检查在空库时报告缺失是正常情况，不代表自动具备研究能力。
完成来源目录及索引准备后，双击 `Start-Workbench.cmd`，在浏览器访问
`http://127.0.0.1:7860`。只绑定本机，不开放公网，也不提供远程多人权限管理。
语义检索另需 Ollama 与 bge-m3；模型辅助另需自行配置 Provider 和环境变量，可能产生费用。

## 在另一台电脑供 Agent 调用

CLI：`py -3.13 portable_run.py cli --help`。
MCP：让目标电脑的 Agent 启动以下进程（用实际绝对路径替换占位符）：

```json
{
  "mcpServers": {
    "mega-rag": {
      "command": "<INSTALL_DIR>/.venv/Scripts/python.exe",
      "args": ["<INSTALL_DIR>/portable_run.py", "mcp"],
      "cwd": "<INSTALL_DIR>"
    }
  }
}
```

各 Agent 的配置格式可能不同；这里是通用示例，不会自动注册任何客户端。
客户端获得的是该本地安装的材料访问能力，不等于 research-kb / Max 已自动连接。

## 离线安装

需先在同平台、同 Python 架构的联网电脑准备所有依赖 wheel，并核对来源与许可：

```powershell
py -3.13 -m pip download --only-binary=:all: -r requirements.txt -r requirements-mcp.txt -d wheelhouse
py -3.13 portable_install.py --wheelhouse wheelhouse
```

离线模式使用 `--no-index`，缺包就报错，不退回联网。通用 ZIP 本身不含 wheelhouse。
完整依赖安装受包源可用性与平台兼容性影响；不能把最小合成测试通过当成完整安装通过。

## 更新、迁移与卸载

- 将原始 ZIP 复制到另一台电脑后重新安装，**不要复制已安装的 `.venv`**。
- 已安装目录移动后，启动器会拒绝继续；请在新位置重新解压安装，再显式迁移数据。
- 配置记录本次安装的绝对数据路径。迁移配置前逐项改成目标电脑路径。
- 数据迁移前关闭全部 MCP/UI/导入/索引写入进程，使用 SQLite 一致性备份；
  不复制活动裸 DB/WAL，不合并两台电脑同时写入的库。
- 卸载前先关闭进程、从各 Agent 移除该 MCP 注册并备份自己的 data/config，
  再删除本人选择的安装目录。程序没有系统服务和注册表卸载项。

ZIP 的 `.sha256` 用于传输校验，`BUNDLE-MANIFEST.json` 记录包内文件哈希；
它们不是代码签名。MIT 仅覆盖项目自有代码，不覆盖第三方语料。
