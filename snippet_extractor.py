#!/usr/bin/env python3
"""
MEGA² RAG — 通用片段提取
返回结构化片段：snippet (完整上下文)、preview (围绕关键词居中)、matched_term
"""
import re
from typing import List, Dict


def format_source_label(record: Dict) -> str:
    """Keep PDF physical pages distinct from MEGAdigital page metadata."""
    abteilung = record.get("abteilung", "?")
    band = record.get("band", "?")
    text_type = record.get("type", record.get("source_type", "?"))
    page = record.get("page", record.get("page_no", "?"))
    if record.get("page_kind") == "megadigital_page":
        page_label = record.get("page_label") or ""
        suffix = f"; text p. {page_label}" if page_label else ""
        return f"MEGAdigital MEGA {abteilung}/{band}, {text_type}, source p. {page}{suffix}"
    return f"MEGA {abteilung}/{band}, {text_type}, PDF p. {page}"

def extract_best_snippet(full_text: str, priority_terms: List[str],
                         before: int = 250, after: int = 900) -> Dict:
    """
    返回 {"snippet": ..., "preview": ..., "matched_term": ..., "match_offset": ...}
    """
    result = {
        "snippet": (full_text or "")[:before + after],
        "preview": (full_text or "")[:200],
        "matched_term": None,
        "match_offset": -1,
    }

    if not full_text:
        return result

    # 无 priority_terms 时，从文本自身提取词作为 fallback
    if not priority_terms:
        words = list(set(w for w in re.findall(r'[a-zA-ZäöüßÄÖÜẞ]{4,}', full_text)))
        priority_terms = words[:15] if words else ['']

    text_lower = full_text.lower()
    best_pos = -1
    best_priority = 999
    best_term = ""

    for pri_idx, term in enumerate(priority_terms):
        if not term:
            continue
        pos = text_lower.find(term.lower())
        if pos >= 0 and pri_idx < best_priority:
            best_priority = pri_idx
            best_pos = pos
            best_term = term
        elif pos >= 0 and pri_idx == best_priority:
            # 同优先级：选周围命中更多的位置
            cur_hits = _count_term_hits(text_lower, pos, priority_terms)
            prev_hits = _count_term_hits(text_lower, best_pos, priority_terms)
            if cur_hits > prev_hits:
                best_pos = pos
                best_term = term

    if best_pos >= 0:
        start = max(0, best_pos - before)
        end = min(len(full_text), best_pos + after)
        snippet = full_text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(full_text):
            snippet = snippet + "..."

        # Preview 围绕 matched_term 居中 (前 50 字符 + 后 150 字符)
        rel_start = best_pos - start + (3 if start > 0 else 0)
        prev_start = max(0, rel_start - 50)
        prev_end = min(len(snippet), rel_start + 150)
        preview = snippet[prev_start:prev_end]
        if prev_start > 0:
            preview = "..." + preview

        result = {
            "snippet": snippet,
            "preview": preview,
            "matched_term": best_term,
            "match_offset": abs(best_pos - start),
        }

    return result


def _count_term_hits(text_lower: str, center: int, terms: List[str]) -> int:
    """统计围绕 center 的窗口中命中多少个不同 term"""
    window = text_lower[max(0, center - 100):min(len(text_lower), center + 100)]
    return sum(1 for t in terms if t and t.lower() in window)


def build_snippet_for_flash(results: List[Dict],
                             priority_terms: List[str],
                             max_items: int = 8) -> List[str]:
    """为 Flash 模型批量生成证据文本片段 (使用完整 snippet)"""
    items = []
    for i, r in enumerate(results[:max_items]):
        tag = "正文" if r.get('is_main_text') else "编者说明"
        src = format_source_label(r)

        full_text = r.get('text', '')
        extracted = extract_best_snippet(full_text, priority_terms)
        items.append(f"[{i+1}] {src} [{tag}]\n{extracted['snippet']}")
    return items


def build_snippet_for_display(results: List[Dict],
                               priority_terms: List[str]) -> None:
    """原地修改 results，添加 display_snippet / preview / matched_term"""
    for r in results:
        full_text = r.get('text', '')
        extracted = extract_best_snippet(full_text, priority_terms)
        r['display_snippet'] = extracted['snippet']
        r['display_preview'] = extracted['preview']
        r['matched_term'] = extracted['matched_term']
