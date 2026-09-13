#!/usr/bin/env python3
"""
MEGA² RAG — 通用片段提取
返回结构化片段：snippet (完整上下文)、preview (围绕关键词居中)、matched_term
"""
import re
from typing import List, Dict


TEXT_LAYER_LABELS = {
    "author_text": "作者原文（结构化文本）",
    "apparatus": "校勘与编者考证",
    "editorial_intro": "编者导言",
    "editorial_note": "编辑说明",
    "table_of_contents": "目录",
    "front_matter": "卷首材料",
    "register": "索引",
    "illustration_list": "插图目录",
    "textband_unclassified": "Textband 未分类（不可自动视为作者原文）",
    "unclassified": "未分类",
}


def text_layer_label(record: Dict) -> str:
    """Return a conservative, user-facing label for document provenance."""
    layer = str(record.get("text_layer") or "unclassified")
    return TEXT_LAYER_LABELS.get(layer, layer)


def is_verified_author_text(record: Dict) -> bool:
    """Only structured author text is automatically verified in classifier v1."""
    return str(record.get("text_layer") or "") == "author_text"


def format_source_label(record: Dict) -> str:
    """Keep PDF physical pages distinct from MEGAdigital page metadata."""
    abteilung = record.get("abteilung", "?")
    band = record.get("band", "?")
    text_type = record.get("type", record.get("source_type", "?"))
    page = record.get("page", record.get("page_no", "?"))
    passage_suffix = ""
    if record.get("record_type") == "passage":
        passage_suffix = f", passage {int(record.get('passage_no', 0)) + 1}"
    if record.get("page_kind") == "megadigital_page":
        page_label = record.get("page_label") or ""
        suffix = f"; text p. {page_label}" if page_label else ""
        return f"MEGAdigital MEGA {abteilung}/{band}, {text_type}, source p. {page}{suffix}{passage_suffix}"
    return f"MEGA {abteilung}/{band}, {text_type}, PDF p. {page}{passage_suffix}"

def _align_context_boundaries(text: str, start: int, end: int,
                              anchor: int | None = None,
                              max_extra: int = 260) -> tuple[int, int, bool]:
    """Expand a character window to nearby paragraph/sentence boundaries."""
    sentence_endings = ".!?"
    closing_marks = "\"')]}\u00bb\u2019\u201c\u201d\u203a"

    left_floor = max(0, start - max_extra)
    left_scan_end = max(start, min(anchor if anchor is not None else start, len(text)))
    left_region = text[left_floor:left_scan_end]
    left_candidates = []
    paragraph = left_region.rfind("\n\n")
    if paragraph >= 0:
        left_candidates.append(paragraph + 2)
    for index, char in enumerate(left_region):
        if char not in sentence_endings:
            continue
        cursor = index + 1
        while cursor < len(left_region) and left_region[cursor] in closing_marks:
            cursor += 1
        if cursor == len(left_region) or left_region[cursor].isspace():
            while cursor < len(left_region) and left_region[cursor].isspace():
                cursor += 1
            left_candidates.append(cursor)
    left_complete = start == 0 or bool(left_candidates)
    if left_candidates:
        start = left_floor + max(left_candidates)

    right_ceiling = min(len(text), end + max_extra)
    right_region = text[end:right_ceiling]
    right_candidates = []
    paragraph = right_region.find("\n\n")
    if paragraph >= 0:
        right_candidates.append(paragraph)
    for index, char in enumerate(right_region):
        if char not in sentence_endings:
            continue
        cursor = index + 1
        while cursor < len(right_region) and right_region[cursor] in closing_marks:
            cursor += 1
        if cursor == len(right_region) or right_region[cursor].isspace():
            right_candidates.append(cursor)
            break
    right_complete = end == len(text) or bool(right_candidates)
    if right_candidates:
        end += min(right_candidates)

    return start, end, left_complete and right_complete

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
        "context_boundary_complete": False,
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
        start, end, boundary_complete = _align_context_boundaries(
            full_text, start, end, anchor=best_pos
        )
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
            "context_boundary_complete": boundary_complete,
        }

    return result


def _count_term_hits(text_lower: str, center: int, terms: List[str]) -> int:
    """统计围绕 center 的窗口中命中多少个不同 term"""
    window = text_lower[max(0, center - 100):min(len(text_lower), center + 100)]
    return sum(1 for t in terms if t and t.lower() in window)

def priority_terms_for_record(record: Dict, priority_terms: List[str]) -> List[str]:
    """Prefer the exact variant that recalled this record, then global terms."""
    ordered = []
    seen = set()
    candidates = list(record.get("_matched_variants", []))
    for group in record.get("_matched_concept_groups", []):
        candidates.extend(group.get("terms", []))
    candidates.extend(priority_terms)
    for value in candidates:
        term = str(value or "").strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            ordered.append(term)
    return ordered



def build_snippet_for_flash(results: List[Dict],
                             priority_terms: List[str],
                             max_items: int = 8) -> List[str]:
    """为 Flash 模型批量生成证据文本片段 (使用完整 snippet)"""
    items = []
    for i, r in enumerate(results[:max_items]):
        legacy_tag = "TEXT卷" if r.get('is_main_text') else "APPARAT卷"
        layer = text_layer_label(r)
        src = format_source_label(r)
        group_labels = [
            group.get("label", "")
            for group in r.get("_matched_concept_groups", [])
            if group.get("label")
        ]
        group_tag = f" [检索词义组: {'；'.join(group_labels)}]" if group_labels else ""

        full_text = r.get('text', '')
        extracted = extract_best_snippet(full_text, priority_terms_for_record(r, priority_terms))
        items.append(f"[{i+1}] {src} [{legacy_tag}] [文献层级: {layer}]{group_tag}\n{extracted['snippet']}")
    return items


def build_snippet_for_display(results: List[Dict],
                               priority_terms: List[str]) -> None:
    """原地修改 results，添加 display_snippet / preview / matched_term"""
    for r in results:
        full_text = r.get('text', '')
        extracted = extract_best_snippet(full_text, priority_terms_for_record(r, priority_terms))
        r['display_snippet'] = extracted['snippet']
        r['display_preview'] = extracted['preview']
        r['matched_term'] = extracted['matched_term']
        r['context_boundary_complete'] = extracted['context_boundary_complete']
        r['text_layer_label'] = text_layer_label(r)
