#!/usr/bin/env python3
"""
MEGA² RAG — 术语表加载器
兼容旧格式（简单 str）和新格式（概念簇 dict）。
"""
import json
import re
import unicodedata
from pathlib import Path

import yaml


_QUERY_BOILERPLATE = (
    "马克思", "恩格斯", "黑格尔", "如何讨论", "怎么讨论", "如何论述",
    "怎么论述", "如何理解", "怎么理解", "是什么", "何谓", "关于",
    "概念", "范畴", "问题", "来看", "从", "在", "中", "的",
)


def normalize_key(value: str) -> str:
    """Normalize lookup keys without discarding Chinese or German umlauts."""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_senses(value) -> list:
    output = []
    for index, raw in enumerate(_as_list(value)):
        if not isinstance(raw, dict):
            continue
        terms = [
            str(item).strip() for item in _as_list(raw.get("de"))
            if str(item).strip()
        ]
        if not terms:
            continue
        output.append({
            "id": str(raw.get("id") or f"sense_{index + 1}"),
            "label": str(raw.get("label") or raw.get("id") or f"sense_{index + 1}"),
            "de": terms,
        })
    return output


def load_glossary(path: str = None) -> dict:
    """加载 glossary，兼容新旧格式。"""
    if path is None:
        path = Path(__file__).parent / "glossary.yaml"
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    terms = raw.get("terms", {})
    normalized = {}
    for key, value in terms.items():
        if isinstance(value, str):
            normalized[str(key)] = {
                "de": [value], "related": [], "abteilung_hint": None,
                "band_hint": None, "query_mode": ["exact"],
                "must_search_exact": False, "type": "", "priority": "normal",
                "senses": [], "_legacy": True,
            }
            continue
        if not isinstance(value, dict):
            continue
        normalized[str(key)] = {
            "de": [str(item).strip() for item in _as_list(value.get("de"))
                   if str(item).strip()],
            "related": _as_list(value.get("related")),
            "abteilung_hint": value.get("abteilung_hint"),
            "band_hint": value.get("band_hint"),
            "query_mode": _as_list(value.get("query_mode")) or ["exact"],
            "must_search_exact": bool(value.get("must_search_exact", False)),
            "type": str(value.get("type", "")),
            "priority": str(value.get("priority", "normal")),
            "senses": _normalize_senses(value.get("senses")),
            "_legacy": False,
        }
    return normalized


def get_glossary_entry(term: str, glossary: dict) -> dict:
    """Case-insensitive, NFKC-normalized glossary lookup."""
    wanted = normalize_key(term)
    for key, entry in glossary.items():
        if normalize_key(key) == wanted:
            if isinstance(entry, dict):
                return entry
            return {"de": [entry], "_legacy": True, "senses": []}
    return {}


def get_glossary_expansions(term: str, glossary: dict) -> list:
    """Return ordered German expansions while preserving structured phrases."""
    entry = get_glossary_entry(term, glossary)
    output = []
    seen = set()

    def add(value: str):
        text = str(value or "").strip()
        key = normalize_key(text)
        if text and key not in seen:
            seen.add(key)
            output.append(text)

    raw_terms = _as_list(entry.get("de"))
    if entry.get("_legacy"):
        for raw in raw_terms:
            for token in str(raw).split():
                add(token)
    else:
        for raw in raw_terms:
            add(raw)
    for sense in entry.get("senses", []):
        for raw in _as_list(sense.get("de")):
            add(raw)
    return output


def needs_model_expansion(question: str, matched_terms: list,
                          glossary: dict) -> tuple[bool, str]:
    """Detect meaningful Chinese residue left by partial glossary matches."""
    if not matched_terms:
        return True, question
    residual = unicodedata.normalize("NFKC", question)
    for term in sorted(matched_terms, key=len, reverse=True):
        residual = re.sub(re.escape(term), " ", residual, flags=re.IGNORECASE)
    for phrase in _QUERY_BOILERPLATE:
        residual = residual.replace(phrase, " ")
    residual = re.sub(r"[\s\W_]+", "", residual, flags=re.UNICODE)
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", residual))
    return len(cjk) >= 2, cjk


def parse_model_expansion(raw: str) -> list:
    """Parse strict JSON or a conservative plain-text fallback from Flash."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    values = []
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            for key in ("phrases", "terms", "german_terms"):
                values.extend(_as_list(payload.get(key)))
            for sense in _as_list(payload.get("senses")):
                if isinstance(sense, dict):
                    values.extend(_as_list(sense.get("terms") or sense.get("de")))
        elif isinstance(payload, list):
            values.extend(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        values.extend(re.split(r"[,;\n]+", text))

    output = []
    seen = set()
    for value in values:
        candidate = str(value or "").strip().strip("\"'`-* ")
        if not candidate or len(candidate) > 80:
            continue
        if not re.fullmatch(r"[A-Za-zÄÖÜäöüẞß][A-Za-zÄÖÜäöüẞß\- ']+", candidate):
            continue
        key = normalize_key(candidate)
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output


def expand_with_glossary(question: str, glossary: dict = None) -> tuple:
    """Return ``(expanded_query, matched_terms, hints)``."""
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

    normalized_question = normalize_key(question)
    candidates = []
    for cn_term, entry in glossary.items():
        normalized_term = normalize_key(cn_term)
        start = normalized_question.find(normalized_term)
        if start >= 0:
            candidates.append((start, start + len(normalized_term), cn_term, entry))

    selected = []
    occupied = []
    for start, end, cn_term, entry in sorted(
            candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(start < used_end and end > used_start
               for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((start, cn_term, entry))

    for _, cn_term, entry in sorted(selected, key=lambda item: item[0]):
        matched_terms.append(cn_term)
        german_additions.extend(get_glossary_expansions(cn_term, glossary))
        if entry.get("abteilung_hint"):
            hints["abteilung_hint"] = entry["abteilung_hint"]
        if entry.get("band_hint"):
            hints["band_hint"] = entry["band_hint"]
        if entry.get("query_mode"):
            hints["query_mode"] = entry["query_mode"]
        if entry.get("must_search_exact"):
            hints["must_search_exact"] = True

    expanded = question + " " + " ".join(german_additions) if german_additions else question
    return expanded, matched_terms, hints


def self_check() -> dict:
    """最小自检函数。"""
    glossary = load_glossary()
    errors = []
    warnings = []
    for term in glossary:
        de_terms = get_glossary_expansions(term, glossary)
        if not de_terms:
            warnings.append(f"术语 '{term}' 没有德语对应词")
        for value in de_terms:
            if not value.strip():
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
    for error in result["error_details"]:
        print(f"  ERROR: {error}")
    for warning in result["warning_details"]:
        print(f"  WARN: {warning}")
