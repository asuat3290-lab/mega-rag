#!/usr/bin/env python3
"""
MEGA² RAG — 术语表加载器
兼容旧格式（简单 str）和新格式（概念簇 dict）。
"""
import yaml
from pathlib import Path
from typing import Dict, List, Optional


def load_glossary(path: str = None) -> dict:
    """加载 glossary，兼容新旧格式"""
    if path is None:
        path = Path(__file__).parent / "glossary.yaml"
    raw = yaml.safe_load(open(path, encoding='utf-8'))
    terms = raw.get('terms', {})
    normalized = {}
    for key, value in terms.items():
        if isinstance(value, str):
            # 旧格式：简单字符串
            normalized[str(key)] = {"de": [value]}
        elif isinstance(value, dict):
            # 新格式：概念簇
            entry = {"de": [], "related": [], "abteilung_hint": None,
                     "band_hint": None, "query_mode": ["exact"], "must_search_exact": False}
            if 'de' in value:
                entry['de'] = value['de'] if isinstance(value['de'], list) else [value['de']]
            if 'related' in value:
                entry['related'] = value['related']
            if 'abteilung_hint' in value:
                entry['abteilung_hint'] = value['abteilung_hint']
            if 'band_hint' in value:
                entry['band_hint'] = value['band_hint']
            if 'query_mode' in value:
                entry['query_mode'] = value['query_mode']
            if 'must_search_exact' in value:
                entry['must_search_exact'] = value['must_search_exact']
            normalized[str(key)] = entry
    return normalized


def expand_with_glossary(question: str, glossary: dict = None) -> tuple:
    """
    返回: (expanded_query, matched_terms, hints)
    hints: {abteilung_hint, band_hint, query_mode, must_search_exact}
    """
    if glossary is None:
        glossary = load_glossary()

    german_additions = []
    matched_terms = []
    hints = {
        "abteilung_hint": None,
        "band_hint": None,
        "query_mode": ["exact"],
        "must_search_exact": False,
    }

    for cn_term, entry in glossary.items():
        if cn_term in question:
            matched_terms.append(cn_term)
            de_terms = entry.get('de', [])
            if isinstance(de_terms, str):
                german_additions.append(de_terms)
            else:
                german_additions.extend(de_terms)
            # 合并 hints
            if entry.get('abteilung_hint'):
                hints['abteilung_hint'] = entry['abteilung_hint']
            if entry.get('band_hint'):
                hints['band_hint'] = entry['band_hint']
            if entry.get('query_mode'):
                hints['query_mode'] = entry['query_mode']
            if entry.get('must_search_exact'):
                hints['must_search_exact'] = True

    expanded = question + " " + " ".join(german_additions) if german_additions else question
    return expanded, matched_terms, hints


def self_check() -> dict:
    """最小自检函数"""
    glossary = load_glossary()
    errors = []
    warnings = []
    for term, entry in glossary.items():
        de_terms = entry.get('de', [])
        if not de_terms:
            warnings.append(f"术语 '{term}' 没有德语对应词")
        # 检查是否有空词条
        for dt in de_terms:
            if not dt.strip():
                errors.append(f"术语 '{term}' 德语词条为空字符串")
    return {
        "total_terms": len(glossary),
        "errors": len(errors),
        "warnings": len(warnings),
        "error_details": errors[:10],
        "warning_details": warnings[:10],
    }


if __name__ == "__main__":
    result = self_check()
    print(f"Glossary 自检: {result['total_terms']} 词条, "
          f"{result['errors']} 错误, {result['warnings']} 警告")
    if result['error_details']:
        for e in result['error_details']:
            print(f"  ERROR: {e}")
    if result['warning_details']:
        for w in result['warning_details']:
            print(f"  WARN: {w}")
