# Codex、OpenCode 与其他 Agent 接入

## 目标

`mega_agent.py` 是稳定的机器接口。它让 Agent 先拿到短证据索引，再按 E### 选择性展开，避免把十几页 OCR 或完整研究包一次塞进上下文。核心检索完全本地运行；只有 `verify` 默认使用 Flash，Pro 必须显式开启。

隐私边界：`search` 和 `verify --local-only` 不发送语料；启用 Flash/Pro 会把用户问题和筛选后的德语 snippet 发送到 `config.yaml` 指定的外部模型端点。敏感研究材料应使用本地模式或自行部署兼容端点。

## JSON 契约

- `stdout`：始终只有一个 UTF-8 JSON 对象；
- `stderr`：进度和诊断日志；
- 成功：退出码 0、`ok=true`；
- 参数、文件或运行错误：退出码非零、`ok=false` 和结构化 `error`；
- 协议版本：`mega-agent-v1`；
- 索引结果带 `index_version`，便于 Agent 记录可复现状态。

查看能力：

```powershell
cd D:\mega_rag
python mega_agent.py capabilities
python mega_agent.py status
```

## 推荐的低 token 流程

复合问题先做问题级规划：

~~~powershell
python mega_agent.py research-plan "复合研究问题"
python mega_agent.py research-run "复合研究问题" --detail index
~~~

research-plan 检查语义覆盖但不访问索引；research-run 才逐个执行必要 MEGA 分支。默认 local 模式为 0 API token。当代经验分支会保留为 missing requirement，不会被 MEGA 理论片段伪装成现实事实。


第一步只取证据索引并保存完整包：

```powershell
python mega_agent.py search "马克思如何讨论利润率下降趋势" --top-k 8 --detail index --save
```

`index` 只返回引用、层级、可靠性、命中词和短预览，并固定 `preview_only=true`、`quote_eligible=false`。Agent 检查 E###、`authorship_status`、`edition_status`、`locator_verified` 和警告后，再展开少数条目：

```powershell
python mega_agent.py evidence "D:\mega_rag\research_exports\research_....json" E001 E003 --detail full
```

可用细节级别：

- `index`：最省 token，适合首轮筛选；
- `snippet`：每条最多约 1,400 字符德语上下文；
- `full`：返回研究包中的完整结构化证据。

只有需要判断“观点成立到什么程度”时才调用：

```powershell
python mega_agent.py verify "你的观点" --budget brief --detail index --save
```

`--local-only` 保证 0 API token，但只返回候选、不会假装完成语义判决。`--pro` 会增加一次综合调用。

## Agent 应遵守的研究规则

```text
先调用 mega_agent.py search --detail index；只展开最相关的 E###。
每个实质性判断都保留证据编号。只有 verified_author_text=true 的材料可以直接表述为
马克思或恩格斯原文。APPARAT/编者材料必须明确归于编者。locator_verified=false 的
页码只能作为定位线索。检索未命中不能写成“马克思从未讨论”。观点核验优先 brief，
证据或范围不足后再升级 standard，不要默认调用 Pro。
```

## Python 接口

```python
from agent_service import (
    research_plan_agent, research_run_agent,
    search_agent, verify_agent,
)

research_plan = research_plan_agent("复合研究问题")
research_run = research_run_agent(
    "复合研究问题",
    detail="index",
    max_evidence=12,
)

evidence_index = search_agent(
    "一般劳动是什么",
    route="main_text",
    top_k=8,
    detail="index",
    save=True,
)

audit = verify_agent(
    "马克思把一般劳动理解为纯粹生理劳动",
    budget="brief",
    detail="index",
    use_pro=False,
)
```

这些函数返回与 CLI 相同的 envelope，适合本机脚本、Codex skill 或 OpenCode tool wrapper。

## 可选 MCP

核心项目不强制安装 MCP。需要 stdio MCP 时，在 D 盘 Python 环境中安装可选依赖：

```powershell
pip install -r D:\mega_rag\requirements-mcp.txt
python D:\mega_rag\mega_mcp.py
```

客户端配置的通用形式：

```json
{
  "mcpServers": {
    "mega-rag": {
      "command": "python",
      "args": ["D:\\mega_rag\\mega_mcp.py"]
    }
  }
}
```


复合研究对应 MCP 工具 `mega_research_plan` 和 `mega_research_run`；普通检索继续使用 `mega_plan`、`mega_search` 和 `mega_verify`。
若 Agent 客户端不支持 MCP，直接调用 JSON CLI 即可，功能不受影响。

## 故障定位

- JSON 无法解析：确认读取 `stdout` 而不是把 `stderr` 合并进去；CLI 已固定 UTF-8。
- 返回 `ready=false`：先运行 `python index_health.py`。
- 中文召回弱：查看 `priority_terms`；确认后将稳定中德映射加入 `glossary.yaml`。
- 只有编者材料：正文论述使用 `--route main_text --retrieval-mode original_first`。
- 返回内容过长：使用 `--detail index`，然后只展开 2 至 4 个 E###。

## 强制综合门控

`search` 返回候选并不代表可以回答。Agent 必须检查 `synthesis_gate.synthesis_allowed`，跨包合并必须使用 `evidence_uid`，不能复用包内 `E001` 编号。复合问题使用 `research-run`，普通 `search` 只用于诊断或定位。

最终报告先写成结构化 claims JSON，再执行：

```powershell
python mega_agent.py report-check report.json source_package_or_run.json
```

校验未通过时命令返回非零退出码。完整约束见 `docs/evidence_report_contract.md` 和仓库根目录 `AGENTS.md`。
