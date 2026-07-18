#!/usr/bin/env python3
"""Gradio controls for the persistent MEGA evidence library."""

from __future__ import annotations

from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((SCRIPT_DIR / "config.yaml").read_text(encoding="utf-8")) or {}


def _package_path(package_file=None, use_latest=False):
    if package_file and not use_latest:
        candidate = getattr(package_file, "path", package_file)
        if candidate:
            return Path(str(candidate))
    export_dir = Path(
        CONFIG.get("paths", {}).get("research_exports", SCRIPT_DIR / "research_exports")
    )
    packages = sorted(export_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    return packages[-1] if packages else None


def _rows(status_filter="all", search="", limit=100):
    from evidence_library import list_evidence, library_status

    status = None if status_filter in (None, "", "all") else status_filter
    items = list_evidence(status=status, search=search or None, limit=int(limit))
    table = []
    for item in items:
        ready = bool(item.get("locator_verified") or item.get("locator_verified_by_user"))
        table.append(
            [
                item["library_ref"],
                item["review_status"],
                item.get("citation_override") or item.get("citation_stub") or "-",
                "是" if ready else "待核验",
                item.get("text_layer_label") or item.get("text_layer") or "-",
                item.get("thesis_section") or "-",
                ", ".join(item.get("tags") or []) or "-",
                (item.get("questions") or [""])[0],
            ]
        )
    current = library_status()
    counts = current["status"]
    summary = (
        f"共 {current['evidence']} 条；未审核 {counts['unreviewed']}，"
        f"已接受 {counts['accepted']}，待核验 {counts['needs_verification']}，"
        f"已驳回 {counts['rejected']}；当前显示 {len(table)} 条。"
    )
    return table, summary


def import_package(package_file=None, use_latest=False):
    try:
        from evidence_library import import_research_package, library_status

        path = _package_path(package_file, use_latest)
        if not path:
            return "没有找到可导入的研究包", [], ""
        stats = import_research_package(path)
        current = library_status()
        message = (
            f"已导入 {path.name}：新增 {stats['evidence_inserted']} 条，"
            f"复用 {stats['evidence_reused']} 条；证据库现有 "
            f"{current['evidence']} 条证据。"
        )
        table, summary = _rows("all", "", 100)
        return message, table, summary
    except Exception as exc:
        return f"导入失败: {type(exc).__name__}: {exc}", [], ""


def import_selected(package_file):
    return import_package(package_file, False)


def import_latest():
    return import_package(None, True)


def refresh(status_filter, search, limit):
    try:
        table, summary = _rows(status_filter, search, limit)
        return summary, table
    except Exception as exc:
        return f"读取失败: {type(exc).__name__}: {exc}", []


def _detail(item):
    context = str(item.get("german_context") or "").replace("```", "`` `")
    ready = bool(item.get("locator_verified") or item.get("locator_verified_by_user"))
    citation = item.get("citation_override") or item.get("citation_stub") or "-"
    questions = "；".join(item.get("questions") or []) or "-"
    warnings = "；".join(item.get("warnings") or []) or "无"
    return f"""
### {item['library_ref']} · {citation}

- 状态：`{item['review_status']}`
- 引用定位可用：`{'是' if ready else '否，正式引用前需核验'}`
- 文本层级：{item.get('text_layer_label') or item.get('text_layer') or '-'}
- 可靠性：`{item.get('reliability_class') or 'unknown'}`
- 来源问题：{questions}
- 警告：{warnings}

```text
{context}
```
"""


def load_item(identifier):
    empty = ("",) * 10
    if not str(identifier or "").strip():
        return ("请输入证据编号，例如 L000001", "") + empty
    try:
        from evidence_library import get_evidence

        item = get_evidence(identifier)
        return (
            f"已读取 {item['library_ref']}，修订版本 {item['revision']}",
            _detail(item),
            item["review_status"],
            item.get("claim_supported") or "",
            item.get("claim_not_supported") or "",
            item.get("literal_translation") or "",
            item.get("research_notes") or "",
            item.get("thesis_section") or "",
            ", ".join(item.get("tags") or []),
            item.get("verified_print_page") or "",
            bool(item.get("locator_verified_by_user")),
            item.get("verified_by") or "",
        )
    except Exception as exc:
        return (f"读取失败: {type(exc).__name__}: {exc}", "") + empty


def save_item(
    identifier,
    review_status,
    claim_supported,
    claim_not_supported,
    literal_translation,
    research_notes,
    thesis_section,
    tags,
    verified_print_page,
    locator_verified,
    verified_by,
):
    if not str(identifier or "").strip():
        return "请输入证据编号", ""
    try:
        from evidence_library import update_review

        def optional(value):
            value = str(value or "").strip()
            return value or None

        item = update_review(
            identifier,
            {
                "status": review_status,
                "claim_supported": optional(claim_supported),
                "claim_not_supported": optional(claim_not_supported),
                "literal_translation": optional(literal_translation),
                "research_notes": optional(research_notes),
                "thesis_section": optional(thesis_section),
                "tags": tags or "",
                "verified_print_page": optional(verified_print_page),
                "locator_verified_by_user": bool(locator_verified),
                "verified_by": optional(verified_by),
            },
        )
        return (
            f"已保存 {item['library_ref']}，修订版本 {item['revision']}",
            _detail(item),
        )
    except Exception as exc:
        return f"保存失败: {type(exc).__name__}: {exc}", ""


def export_items(status_filter, search, thesis_section, tag):
    try:
        from evidence_library import export_library

        result = export_library(
            status=status_filter,
            search=search or None,
            thesis_section=thesis_section or None,
            tag=tag or None,
        )
        summary = result["export"]["summary"]
        if not summary["evidence_count"]:
            return "没有符合条件的证据可导出", None, None
        message = (
            f"已导出 {summary['evidence_count']} 条证据，其中 "
            f"{summary['citation_ready_count']} 条定位可用，约 "
            f"{summary['rough_evidence_tokens']} tokens。"
        )
        return message, result["paths"].get("markdown"), result["paths"].get("json")
    except Exception as exc:
        return f"导出失败: {type(exc).__name__}: {exc}", None, None


def attach_evidence_library(app):
    """Append the evidence-library workbench to an existing Gradio Blocks app."""
    import gradio as gr

    with app:
        gr.Markdown("---\n## 🗂 论文证据库")
        gr.Markdown(
            "研究包导入后可在此人工审核。来源与德语上下文保持不变；"
            "审核意见、直译、论文位置和页码核验会记录修订历史。"
        )
        with gr.Tabs():
            with gr.Tab("导入与清单"):
                with gr.Row():
                    package_file = gr.File(
                        label="研究包 JSON（可选）",
                        file_types=[".json"],
                        type="filepath",
                    )
                    with gr.Column():
                        import_selected_btn = gr.Button("导入所选研究包")
                        import_latest_btn = gr.Button("导入最新研究包", variant="secondary")
                import_status = gr.Textbox(label="导入状态", interactive=False)
                with gr.Row():
                    filter_status = gr.Dropdown(
                        label="审核状态",
                        choices=[
                            ("全部", "all"),
                            ("未审核", "unreviewed"),
                            ("已接受", "accepted"),
                            ("待核验", "needs_verification"),
                            ("已驳回", "rejected"),
                        ],
                        value="all",
                    )
                    search = gr.Textbox(label="库内搜索")
                    limit = gr.Slider(20, 500, value=100, step=20, label="显示数量")
                    refresh_btn = gr.Button("刷新")
                summary = gr.Textbox(label="证据库状态", interactive=False)
                table = gr.Dataframe(
                    headers=[
                        "编号",
                        "状态",
                        "来源",
                        "定位",
                        "层级",
                        "论文位置",
                        "标签",
                        "来源问题",
                    ],
                    datatype=["str"] * 8,
                    value=[],
                    interactive=False,
                    label="证据清单",
                )

            with gr.Tab("审核证据"):
                with gr.Row():
                    identifier = gr.Textbox(
                        label="证据编号", placeholder="例如 L000001"
                    )
                    load_btn = gr.Button("读取证据")
                review_message = gr.Textbox(label="审核状态", interactive=False)
                detail = gr.Markdown("选择证据后显示德语上下文。")
                review_status = gr.Dropdown(
                    label="处理状态",
                    choices=[
                        ("未审核", "unreviewed"),
                        ("已接受", "accepted"),
                        ("待核验", "needs_verification"),
                        ("已驳回", "rejected"),
                    ],
                    value="unreviewed",
                )
                claim = gr.Textbox(label="该证据支持的主张", lines=2)
                claim_not = gr.Textbox(label="该证据不能支持的主张", lines=2)
                translation = gr.Textbox(label="德语直译", lines=4)
                notes = gr.Textbox(label="研究笔记", lines=4)
                with gr.Row():
                    section = gr.Textbox(label="论文位置")
                    tags = gr.Textbox(label="标签（逗号分隔）")
                with gr.Row():
                    print_page = gr.Textbox(label="人工核验后的印刷页码")
                    locator_verified = gr.Checkbox(
                        label="已人工核验引用定位", value=False
                    )
                    verified_by = gr.Textbox(label="核验人")
                save_btn = gr.Button("保存审核", variant="primary")

            with gr.Tab("导出论文证据集"):
                with gr.Row():
                    export_status_filter = gr.Dropdown(
                        label="导出状态",
                        choices=[
                            ("已接受", "accepted"),
                            ("待核验", "needs_verification"),
                            ("未审核", "unreviewed"),
                            ("全部", "all"),
                        ],
                        value="accepted",
                    )
                    export_section = gr.Textbox(label="限定论文位置")
                    export_tag = gr.Textbox(label="限定标签")
                    export_btn = gr.Button("导出证据集")
                export_status = gr.Textbox(label="导出状态", interactive=False)
                with gr.Row():
                    export_markdown = gr.File(
                        label="Markdown 证据集", interactive=False
                    )
                    export_json = gr.File(label="JSON 证据集", interactive=False)

        import_selected_btn.click(
            fn=import_selected,
            inputs=[package_file],
            outputs=[import_status, table, summary],
        )
        import_latest_btn.click(
            fn=import_latest,
            outputs=[import_status, table, summary],
        )
        refresh_btn.click(
            fn=refresh,
            inputs=[filter_status, search, limit],
            outputs=[summary, table],
        )
        load_btn.click(
            fn=load_item,
            inputs=[identifier],
            outputs=[
                review_message,
                detail,
                review_status,
                claim,
                claim_not,
                translation,
                notes,
                section,
                tags,
                print_page,
                locator_verified,
                verified_by,
            ],
        )
        save_btn.click(
            fn=save_item,
            inputs=[
                identifier,
                review_status,
                claim,
                claim_not,
                translation,
                notes,
                section,
                tags,
                print_page,
                locator_verified,
                verified_by,
            ],
            outputs=[review_message, detail],
        )
        export_btn.click(
            fn=export_items,
            inputs=[export_status_filter, search, export_section, export_tag],
            outputs=[export_status, export_markdown, export_json],
        )
        app.load(
            fn=refresh,
            inputs=[filter_status, search, limit],
            outputs=[summary, table],
        )
    return app
