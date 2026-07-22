#!/usr/bin/env python3
"""Gradio entry point for evidence-bounded multi-branch MEGA research."""
from __future__ import annotations

import json

from agent_service import research_plan_agent, research_run_agent


def _plan_markdown(payload: dict) -> str:
    plan = payload.get("research_plan", {})
    lines = [
        "### 问题覆盖",
        "",
        f"- 类型：{plan.get('question_type', '-')}",
        f"- 覆盖状态：{plan.get('coverage_status', '-')}",
        f"- 覆盖率：{plan.get('question_coverage', 0)}",
        f"- 外部经验材料：{'需要' if plan.get('external_evidence_required') else '不需要'}",
        "",
        "| 分支 | 类型 | 证据范围 | 证据要求 |",
        "|---|---|---|---|",
    ]
    for item in plan.get("subquestions", []):
        corpora = ", ".join(item.get("required_corpus", []))
        lines.append(
            f"| {item.get('question', '')} | {item.get('type', '')} | "
            f"{corpora} | {item.get('required_evidence', '')} |"
        )
    if plan.get("unmapped_concepts"):
        lines.extend([
            "",
            "### 未映射概念",
            "",
            ", ".join(plan["unmapped_concepts"]),
        ])
    return "\n".join(lines)


def _run_markdown(payload: dict) -> str:
    status = payload.get("status", {})
    lines = [
        "### 检索状态",
        "",
        f"- 总体：{status.get('overall', '-')}",
        f"- 可回答性：{status.get('answerability', '-')}",
        f"- 问题覆盖：{status.get('coverage_status', '-')}",
        "",
        "| 分支 | 类型 | MEGA 状态 | 证据 |",
        "|---|---|---|---|",
    ]
    for branch in payload.get("branches", []):
        lines.append(
            f"| {branch.get('question', '')} | {branch.get('type', '')} | "
            f"{branch.get('mega_retrieval_status', '')} | "
            f"{', '.join(branch.get('evidence_ids', [])) or '-'} |"
        )
    missing = status.get("missing_requirements", [])
    if missing:
        lines.extend(["", "### 尚缺材料", ""])
        lines.extend(f"- {value}" for value in missing)

    lines.extend(["", "### 证据索引", ""])
    for item in payload.get("evidence", []):
        quote_state = "可进入引文核查" if item.get("quote_eligible") else "仅用于筛选"
        lines.extend([
            f"**{item.get('evidence_id', '-')} · {item.get('citation', '-')}**",
            "",
            f"{item.get('preview', '')}",
            "",
            f"来源：{item.get('authorship_status', '-')} / "
            f"{item.get('edition_status', '-')} / {quote_state}",
            "",
        ])
    return "\n".join(lines)


def plan_research_ui(question, use_hybrid):
    if not str(question or "").strip():
        return "请输入研究问题", "", "", None
    try:
        payload = research_plan_agent(
            question,
            planner_mode="hybrid" if use_hybrid else "local",
        )
        plan = payload["research_plan"]
        status = (
            f"规划完成：{len(plan.get('subquestions', []))} 个分支；"
            f"覆盖状态 {plan.get('coverage_status', '-')}; "
            f"API token {payload.get('usage', {}).get('api_tokens', 0)}。"
        )
        return (
            status,
            _plan_markdown(payload),
            json.dumps(payload, ensure_ascii=False, indent=2),
            None,
        )
    except Exception as exc:
        return f"规划失败：{type(exc).__name__}: {exc}", "", "", None


def run_research_ui(
    question,
    top_k_per_branch,
    max_evidence,
    detail,
    use_hybrid,
):
    if not str(question or "").strip():
        return "请输入研究问题", "", "", None
    try:
        payload = research_run_agent(
            question,
            top_k_per_branch=int(top_k_per_branch),
            max_evidence=int(max_evidence),
            detail=detail,
            save=True,
            planner_mode="hybrid" if use_hybrid else "local",
        )
        status_data = payload.get("status", {})
        status = (
            f"检索完成：总体 {status_data.get('overall', '-')}; "
            f"{len(payload.get('branches', []))} 个分支；"
            f"{len(payload.get('evidence', []))} 条去重证据；"
            f"API token {payload.get('usage', {}).get('api_tokens', 0)}。"
        )
        return (
            status,
            _run_markdown(payload),
            json.dumps(payload, ensure_ascii=False, indent=2),
            payload.get("artifact_paths", {}).get("json"),
        )
    except Exception as exc:
        return f"检索失败：{type(exc).__name__}: {exc}", "", "", None


def attach_research_orchestrator(app):
    """Append the compound-research surface to an existing Gradio Blocks app."""
    import gradio as gr

    with app:
        gr.Markdown("---\n## 复合研究")
        question = gr.Textbox(
            label="研究问题",
            placeholder="例如：马克思怎样讨论利润率下降，如何联系机器理论分析当代人工智能？",
            lines=4,
        )
        with gr.Row():
            top_k = gr.Slider(
                label="每分支候选数", minimum=1, maximum=10, step=1, value=5
            )
            max_evidence = gr.Slider(
                label="总证据上限", minimum=4, maximum=30, step=1, value=12
            )
            detail = gr.Dropdown(
                label="返回细节",
                choices=[("证据索引", "index"), ("德文片段", "snippet")],
                value="index",
            )
            use_hybrid = gr.Checkbox(label="Flash 辅助规划", value=False)
        with gr.Row():
            plan_button = gr.Button("分析问题")
            run_button = gr.Button("检索全部分支", variant="primary")
        status = gr.Textbox(label="研究状态", interactive=False, lines=2)
        artifact = gr.File(label="JSON 研究记录", interactive=False)
        with gr.Tabs():
            with gr.Tab("研究概览"):
                report = gr.Markdown("等待研究。")
            with gr.Tab("结构化 JSON"):
                structured = gr.Code(label="JSON", language="json", interactive=False)

        plan_button.click(
            fn=plan_research_ui,
            inputs=[question, use_hybrid],
            outputs=[status, report, structured, artifact],
        )
        run_button.click(
            fn=run_research_ui,
            inputs=[question, top_k, max_evidence, detail, use_hybrid],
            outputs=[status, report, structured, artifact],
        )
    return app

