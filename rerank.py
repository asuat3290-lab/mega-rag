#!/usr/bin/env python3
"""
MEGA² RAG — 统一重排序
分数越高 = 越相关。所有 boost 正方向，所有 penalty 负方向。
"""
import sys
from typing import List, Dict, Optional


MODE_WEIGHTS = {
    "original_first": {"TEXT": 1.30, "APPARAT": 0.80},
    "apparat_first":  {"TEXT": 0.90, "APPARAT": 1.25},
    "balanced":       {"TEXT": 1.05, "APPARAT": 1.00},
    "philology":      {"TEXT": 1.00, "APPARAT": 1.20},
}

_NOISE_PATTERNS = [
    'Verzeichnis der Abkürzungen', 'Siglen und Zeichen', 'Diakritische Zeichen',
    'Münzen und Gewichte', 'Münzen, Maße', 'Editorische Hinweise',
    'Variantenverzeichnis', 'Abkürzungsverzeichnis', 'Sachregister',
]


def _is_noise(text: str) -> bool:
    txt200 = text[:200] if text else ""
    return any(p.lower() in txt200.lower() for p in _NOISE_PATTERNS)


def _text_layer_adjustment(text_layer: str, intent: str) -> float:
    """Conservative layer signal; unknown Textband pages are never auto-promoted."""
    layer = text_layer or "unclassified"
    if intent == "author_argument":
        return {
            "author_text": 0.04,
            "editorial_intro": -0.18,
            "editorial_note": -0.20,
            "table_of_contents": -0.35,
            "front_matter": -0.40,
            "register": -0.25,
            "illustration_list": -0.35,
        }.get(layer, 0.0)
    if intent == "apparat_question":
        return {
            "editorial_note": 0.08,
            "editorial_intro": 0.03,
            "apparatus": 0.03,
        }.get(layer, 0.0)
    return 0.0


def rerank_rule(results: List[Dict],
                query: str = "",
                mode: str = "balanced",
                glossary_terms: List[str] = None,
                query_profile: Dict = None) -> List[Dict]:
    """
    通用启发式重排。所有分数正方向。
    """
    if not results:
        return results

    if glossary_terms is None:
        glossary_terms = []
    if query_profile is None:
        query_profile = {}

    weights = MODE_WEIGHTS.get(mode, MODE_WEIGHTS["balanced"])
    intent = query_profile.get("intent", "general_search")
    target_abt = query_profile.get("target_abteilung")
    target_band = query_profile.get("target_band")

    query_words = set(query.lower().split()) if query else set()

    for i, r in enumerate(results):
        text_lower = r.get('text', '').lower()

        # ---- 1. RRF 基础分 (检索质量，0~1 区间) ----
        rrf_base = r.get('_score', 1.0 / (i + 1))

        # ---- 2. 精确词命中 ----
        exact_hits = sum(1 for w in query_words if len(w) > 2 and w in text_lower)
        exact_score = min(exact_hits / max(len(query_words), 1), 1.0)

        # ---- 3. 术语表词命中 ----
        gloss_hits = sum(1 for t in glossary_terms if t.lower() in text_lower)
        gloss_score = min(gloss_hits / max(len(glossary_terms), 1), 1.0) if glossary_terms else 0

        # ---- 4. TEXT/APPARAT 基础权重 ----
        text_type = r.get('type', r.get('source_type', 'APPARAT'))
        type_boost = weights.get(text_type, 1.0)

        # ---- 5. 意图加权 (通用) ----
        intent_boost = 0.0
        if intent == "author_argument":
            if text_type == "TEXT":
                intent_boost = 0.20
            elif text_type == "APPARAT":
                intent_boost = -0.15
        elif intent == "apparat_question":
            if text_type == "APPARAT":
                intent_boost = 0.15
            # TEXT 不惩罚
        # general_search: 不加额外偏置

        # ---- 6. OCR 质量 ----
        from ocr_quality import quality_boost
        ocr_boost = quality_boost(r.get('ocr_quality', 'medium'))

        # ---- 7. 噪音惩罚 ----
        noise_penalty = 0.30 if _is_noise(text_lower) else 0.0

        # ---- 8. 著作→卷加权 (组合信号) ----
        abt_match = target_abt and r.get('abteilung', '') == target_abt
        band_match = target_band and r.get('band', '') == target_band
        target_vol_match = abt_match and band_match if target_band else abt_match
        is_target_text = target_vol_match and text_type == "TEXT"

        volume_boost = 0.0
        out_of_scope_penalty = 0.0
        source_collection = r.get("source_collection", "ocr")
        source_boost = 0.18 if source_collection == "megadigital" else 0.0
        text_layer = str(r.get("text_layer") or "unclassified")
        layer_adjustment = _text_layer_adjustment(text_layer, intent)

        # 温和的卷加权（范围约束由 scope_constrained_rerank 负责）
        if target_vol_match:
            volume_boost += 0.15  # 目标卷基础加权
        if is_target_text:
            volume_boost += 0.10  # 目标卷 TEXT 微调
        if intent == "author_argument" and not target_vol_match and text_type == "APPARAT":
            out_of_scope_penalty = 0.10  # 非目标卷 APPARAT 轻度扣分

        # ---- 综合分数 (所有信号正方向) ----
        final = (rrf_base * 0.50 +
                 exact_score * 0.10 +
                 gloss_score * 0.05 +
                 type_boost * 0.08 +
                 intent_boost +
                 ocr_boost * 0.04 -
                 noise_penalty +
                 volume_boost +
                 source_boost +
                 layer_adjustment -
                 out_of_scope_penalty)

        r['rrf_score'] = rrf_base
        r['bm25_score'] = round(rrf_base, 4)
        r['vector_score'] = round(rrf_base, 4)
        r['metadata_boost'] = round(type_boost, 3)
        r['intent_boost'] = round(intent_boost, 3)
        r['volume_boost'] = round(volume_boost, 3)
        r['source_boost'] = round(source_boost, 3)
        r['layer_adjustment'] = round(layer_adjustment, 3)
        r['out_of_scope_penalty'] = round(out_of_scope_penalty, 3)
        r['final_score'] = round(final, 4)
        r['_debug'] = {
            "rrf_base": round(rrf_base, 4),
            "type_boost": round(type_boost, 3),
            "volume_boost": round(volume_boost, 3),
            "source_boost": round(source_boost, 3),
            "text_layer": text_layer,
            "layer_adjustment": round(layer_adjustment, 3),
            "intent_boost": round(intent_boost, 3),
            "exact_score": round(exact_score, 3),
            "gloss_score": round(gloss_score, 3),
            "ocr_boost": round(ocr_boost, 3),
            "noise_penalty": round(noise_penalty, 3),
            "out_of_scope_penalty": round(out_of_scope_penalty, 3),
            "target_volume_match": target_vol_match,
            "is_target_text": is_target_text,
            "final_score": round(final, 4),
        }

    # 降序：分数越高越靠前
    return sorted(results, key=lambda x: x.get('final_score', 0), reverse=True)


def rerank_bge(results: List[Dict], query: str, top_k: int = 20) -> List[Dict]:
    try:
        from sentence_transformers import CrossEncoder
        model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
        pairs = [[query, r.get('text', '')[:400]] for r in results[:top_k * 2]]
        scores = model.predict(pairs, show_progress_bar=False)
        for i, r in enumerate(results[:len(scores)]):
            r['bge_rerank_score'] = float(scores[i])
        results.sort(key=lambda x: x.get('bge_rerank_score', 0), reverse=True)
        return results[:top_k]
    except (ImportError, Exception) as e:
        import warnings
        warnings.warn(f"bge reranker 不可用 ({e})，回退 rule")
        return rerank_rule(results, query)


def apply_scope_constrained_rerank(results: List[Dict], query_profile: Dict,
                                     min_in_scope: int = 3) -> List[Dict]:
    """
    范围约束重排。仅在明确作品范围 + 正文论述意图时启用。
    不完全删除 out_scope，而是优先排列 in_scope_text。
    """
    if not query_profile:
        return results
    target_abt = query_profile.get("target_abteilung")
    target_band = query_profile.get("target_band")
    intent = query_profile.get("intent", "")

    debug_info = {
        "scope_constrained_applied": False,
        "reason": "",
    }

    # 前置条件检查
    if not target_abt:
        debug_info["reason"] = "no target_volume"
        return _tag_results(results, "disabled", debug_info)
    if intent != "author_argument":
        debug_info["reason"] = f"intent={intent}, not author_argument"
        return _tag_results(results, "disabled", debug_info)

    # 分桶
    in_scope_text = []
    in_scope_apparat = []
    out_scope_text = []
    out_scope_apparat = []
    other = []

    for r in results:
        abt = r.get('abteilung', '')
        band = r.get('band', '')
        txt_type = r.get('type', r.get('source_type', 'APPARAT'))
        in_scope = (abt == target_abt)
        if target_band:
            in_scope = in_scope and (band == target_band)

        if in_scope and txt_type == "TEXT":
            in_scope_text.append(r)
            r['scope_bucket'] = "in_scope_text"
        elif in_scope and txt_type == "APPARAT":
            in_scope_apparat.append(r)
            r['scope_bucket'] = "in_scope_apparat"
        elif not in_scope and txt_type == "TEXT":
            out_scope_text.append(r)
            r['scope_bucket'] = "out_scope_text"
        elif not in_scope and txt_type == "APPARAT":
            out_scope_apparat.append(r)
            r['scope_bucket'] = "out_scope_apparat"
        else:
            other.append(r)
            r['scope_bucket'] = "other"

    debug_info["in_scope_text"] = len(in_scope_text)
    debug_info["in_scope_apparat"] = len(in_scope_apparat)
    debug_info["out_scope_text"] = len(out_scope_text)
    debug_info["out_scope_apparat"] = len(out_scope_apparat)

    if len(in_scope_text) < min_in_scope:
        debug_info["reason"] = f"insufficient in_scope_text ({len(in_scope_text)} < {min_in_scope})"
        return _tag_results(results, "insufficient_data", debug_info)

    # 构建新排序：保持各桶内部原顺序
    reranked = []
    # 1. in_scope_text 优先
    reranked.extend(in_scope_text)
    # 2. in_scope_apparat
    reranked.extend(in_scope_apparat)
    # 3. out_scope_text
    reranked.extend(out_scope_text)
    # 4. out_scope_apparat
    reranked.extend(out_scope_apparat)
    # 5. 其他
    reranked.extend(other)

    debug_info["scope_constrained_applied"] = True
    debug_info["reason"] = f"applied, in_scope_text={len(in_scope_text)}"
    return _tag_results(reranked, "applied", debug_info)


def _tag_results(results, status, debug_info):
    for r in results:
        r['scope_constrained'] = status
        r['_scope_debug'] = debug_info
    return results


def rerank(results: List[Dict], query: str = "", mode: str = "balanced",
           method: str = "rule", glossary_terms: List[str] = None,
           query_profile: Dict = None) -> List[Dict]:
    if method == "none":
        return results
    elif method == "bge":
        return rerank_bge(results, query)
    else:
        results = rerank_rule(results, query, mode, glossary_terms, query_profile)
        # 范围约束重排（rule rerank 后应用）
        if query_profile:
            results = apply_scope_constrained_rerank(results, query_profile)
        return results
