#!/usr/bin/env python3
"""
MEGA² RAG — OCR 质量评估（启发式，0 依赖）
输出: high / medium / low / failed
"""
import re


# 德语常用词 (top 50 most frequent)
_GERMAN_WORDS = set("""
der die das und in den von zu mit sich auf ist nicht des dem als ein auch eine
werden aus nach für wie im das er sie es war dass ein eine einem einen einer
bei zur aber noch haben vor nur durch bis über nach schon unter war wurde
worden sein diese dieser dieses ihre ihr ihnen kann oder wenn dann
""".split())


def assess_quality(text: str) -> dict:
    """
    返回: {quality, alpha_ratio, german_word_ratio, suspect_score, text_length, line_count}
    """
    if not text or len(text.strip()) < 5:
        return {"quality": "failed", "alpha_ratio": 0, "german_word_ratio": 0,
                "suspect_score": 1.0, "text_length": 0, "line_count": 0}

    length = len(text)
    lines = [l for l in text.split('\n') if l.strip()]
    line_count = len(lines)

    # alpha_ratio: 字母字符占比
    alpha_chars = sum(1 for c in text if c.isalpha() or c in 'äöüßÄÖÜẞ')
    alpha_ratio = alpha_chars / max(length, 1)

    # german_word_ratio: 德语常用词占比
    words = re.findall(r'[a-zäöüß]+', text.lower())
    if not words:
        return {"quality": "failed", "alpha_ratio": alpha_ratio, "german_word_ratio": 0,
                "suspect_score": 1.0, "text_length": length, "line_count": line_count}

    german_hits = sum(1 for w in words if w in _GERMAN_WORDS)
    german_word_ratio = german_hits / len(words)

    # suspect_score: 综合可疑度 (0=干净, 1=高度可疑)
    suspect = 0.0
    if alpha_ratio < 0.4:
        suspect += 0.4
    if alpha_ratio < 0.2:
        suspect += 0.3
    if german_word_ratio < 0.02:
        suspect += 0.2
    if len(words) < 10:
        suspect += 0.2
    # 过多非德文字符 (乱码特征)
    non_german = sum(1 for c in text if ord(c) > 127 and c not in 'äöüßÄÖÜẞ–—„"''…')
    if non_german / max(length, 1) > 0.3:
        suspect += 0.3

    suspect = min(suspect, 1.0)

    # 分类
    if suspect > 0.7:
        quality = "failed"
    elif suspect > 0.4:
        quality = "low"
    elif suspect > 0.15:
        quality = "medium"
    else:
        quality = "high"

    return {
        "quality": quality,
        "alpha_ratio": round(alpha_ratio, 3),
        "german_word_ratio": round(german_word_ratio, 3),
        "suspect_score": round(suspect, 3),
        "text_length": length,
        "line_count": line_count,
    }


def quality_boost(quality: str) -> float:
    """检索时低质量页降权系数"""
    return {"high": 1.0, "medium": 0.95, "low": 0.85, "failed": 0.5}.get(quality, 0.9)
