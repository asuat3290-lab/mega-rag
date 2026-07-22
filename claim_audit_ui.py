#!/usr/bin/env python3
"""Gradio workbench for evidence-constrained claim auditing."""
from __future__ import annotations

import json

from claim_audit import audit_claim, render_claim_audit_markdown, write_claim_audit


def run_claim_audit(
    idea,
    budget,
    route,
    use_flash,
    use_pro,
    use_cache,
    progress=None,
):
    if not str(idea or "").strip():
        return "请输入要核验的观点", "", "", None, None
    messages = []

    def update(message):
        messages.append(str(message))
        if progress is not None:
            progress(None, desc=str(message))

    try:
        audit = audit_claim(
            idea,
            budget=budget,
            route=route,
            use_flash=bool(use_flash),
            use_pro=bool(use_pro),
            use_cache=bool(use_cache),
            progress=update,
        )
        paths = write_claim_audit(audit, output_format="both")
        runtime = audit.get("runtime", {})
        summary = audit.get("summary", {})
        status = (
            f"完成：{summary.get('atomic_claims', 0)} 个原子主张，"
            f"{summary.get('evidence_count', 0)} 条证据；"
            f"结论 {summary.get('verdict', 'insufficient')}；"
            f"本次 API token {runtime.get('api_tokens_this_run', 0)}；"
            f"耗时 {runtime.get('elapsed_seconds_this_run', 0)} 秒；"
            f"缓存 {'命中' if runtime.get('cache_hit') else '未命中'}。"
        )
        return (
            status,
            render_claim_audit_markdown(audit),
            json.dumps(audit, ensure_ascii=False, indent=2),
            paths.get("markdown"),
            paths.get("json"),
        )
    except Exception as exc:
        return f"核验失败：{type(exc).__name__}: {exc}", "", "", None, None


def attach_claim_audit(app):
    """Append the claim-audit workbench to an existing Gradio Blocks app."""
    import gradio as gr

    with app:
        gr.Markdown("---\n## 观点核验")
        idea = gr.Textbox(
            label="待核验观点",
            placeholder="例如：马克思在成熟经济学中已经放弃了异化概念。",
            lines=4,
        )
        with gr.Row():
            budget = gr.Dropdown(
                label="核验预算",
                choices=[("简洁", "brief"), ("标准", "standard"), ("深入", "deep")],
                value="brief",
            )
            route = gr.Dropdown(
                label="证据范围",
                choices=[("作者正文优先", "main_text"), ("全部", "all"), ("校勘材料", "apparat")],
                value="main_text",
            )
            use_flash = gr.Checkbox(label="Flash 证据判断", value=True)
            use_pro = gr.Checkbox(label="Pro 综合", value=False)
            use_cache = gr.Checkbox(label="启用缓存", value=True)
            run_button = gr.Button("核验观点", variant="primary")
        status = gr.Textbox(label="核验状态", interactive=False, lines=2)
        with gr.Row():
            markdown_file = gr.File(label="Markdown 核验报告", interactive=False)
            json_file = gr.File(label="JSON 核验报告", interactive=False)
        with gr.Tabs():
            with gr.Tab("核验报告"):
                report = gr.Markdown("等待核验。")
            with gr.Tab("结构化 JSON"):
                structured = gr.Code(label="JSON", language="json", interactive=False)

        run_button.click(
            fn=run_claim_audit,
            inputs=[idea, budget, route, use_flash, use_pro, use_cache],
            outputs=[status, report, structured, markdown_file, json_file],
        )
    return app
