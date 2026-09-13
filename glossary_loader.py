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


def _is_latin_word_char(value: str) -> bool:
    """Return whether a character can continue a Latin-script word."""
    if not value:
        return False
    return value.isdigit() or unicodedata.name(value, "").startswith("LATIN")


def _iter_term_spans(text: str, term: str):
    """Yield matches, enforcing word boundaries for Latin/German terms.

    Chinese concepts can occur inside a longer Chinese question, while a
    source-language term such as ``Verkehr`` must not match ``Verkehrung``.
    """
    if not term:
        return
    offset = 0
    while True:
        start = text.find(term, offset)
        if start < 0:
            return
        end = start + len(term)
        left_ok = (
            not _is_latin_word_char(term[0])
            or start == 0
            or not _is_latin_word_char(text[start - 1])
        )
        right_ok = (
            not _is_latin_word_char(term[-1])
            or end == len(text)
            or not _is_latin_word_char(text[end])
        )
        if left_ok and right_ok:
            yield start, end
        offset = start + 1


def _remove_boilerplate_phrases(text: str) -> str:
    """Remove multi-character query framing without damaging concepts."""
    output = text
    phrases = {
        phrase for phrase in _QUERY_BOILERPLATE
        if len(normalize_key(phrase)) >= 2
    }
    for phrase in sorted(phrases, key=len, reverse=True):
        output = output.replace(phrase, " ")
    return output


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


def _normalize_aliases(value) -> list:
    output = []
    seen = set()
    for raw in _as_list(value):
        alias = str(raw or "").strip()
        key = normalize_key(alias)
        if alias and key not in seen:
            seen.add(key)
            output.append(alias)
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
                "senses": [], "aliases": [], "recall": [], "_legacy": True,
                "_legacy_raw": value,
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
            "aliases": _normalize_aliases(value.get("aliases")),
            "recall": [str(item).strip() for item in _as_list(value.get("recall"))
                       if str(item).strip()],
            "_legacy": False,
            "_legacy_raw": None,
        }
    return normalized


def get_glossary_entry(term: str, glossary: dict) -> dict:
    """Case-insensitive, NFKC-normalized glossary lookup."""
    wanted = normalize_key(term)
    for key, entry in glossary.items():
        aliases = entry.get("aliases", []) if isinstance(entry, dict) else []
        if normalize_key(key) == wanted or any(
                normalize_key(alias) == wanted for alias in aliases):
            if isinstance(entry, dict):
                output = dict(entry)
                output["canonical_term"] = str(key)
                return output
            return {"de": [entry], "_legacy": True, "senses": []}
    return {}


def get_glossary_canonical_term(term: str, glossary: dict) -> str:
    """Return the canonical glossary key for a key or alias."""
    entry = get_glossary_entry(term, glossary)
    return str(entry.get("canonical_term") or term)


def get_glossary_expansions(term: str, glossary: dict) -> list:
    """Return broad recall terms while preserving structured phrases.

    Legacy strings keep their historical token-splitting behaviour for recall.
    Evidence qualification uses :func:`get_glossary_qualification_groups`
    instead, so broad recall tokens cannot silently become direct evidence.
    """
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
        for raw in _as_list(entry.get("recall")):
            add(raw)
    for sense in entry.get("senses", []):
        for raw in _as_list(sense.get("de")):
            add(raw)
    return output


def get_glossary_exact_terms(term: str, glossary: dict) -> list:
    """Return phrase-preserving evidence terms for a glossary concept.

    Structured entries are authoritative. Legacy entries deliberately return
    no exact terms because their whitespace-separated syntax cannot encode
    phrase boundaries reliably. They remain usable for recall and are surfaced
    by validation as migration candidates.
    """
    entry = get_glossary_entry(term, glossary)
    if not entry or entry.get("_legacy"):
        return []
    output = []
    seen = set()
    for raw in _as_list(entry.get("de")):
        value = str(raw or "").strip()
        key = normalize_key(value)
        if value and key not in seen:
            seen.add(key)
            output.append(value)
    for sense in entry.get("senses", []):
        for raw in _as_list(sense.get("de")):
            value = str(raw or "").strip()
            key = normalize_key(value)
            if value and key not in seen:
                seen.add(key)
                output.append(value)
    return output


def get_glossary_qualification_groups(term: str, glossary: dict) -> list:
    """Build phrase-aware alternatives used to qualify retrieved evidence."""
    entry = get_glossary_entry(term, glossary)
    if not entry or entry.get("_legacy"):
        return []
    canonical = str(entry.get("canonical_term") or term)
    senses = entry.get("senses", [])
    if senses:
        return [
            {
                "id": f"{normalize_key(canonical)}:{sense.get('id')}",
                "label": str(sense.get("label") or sense.get("id")),
                "alternatives": [
                    str(value).strip() for value in _as_list(sense.get("de"))
                    if str(value).strip()
                ],
                "source_term": canonical,
                "source": "glossary_sense",
                "equivalent": True,
            }
            for sense in senses
            if _as_list(sense.get("de"))
        ]
    exact = get_glossary_exact_terms(canonical, glossary)
    if not exact:
        return []
    return [{
        "id": normalize_key(canonical),
        "label": canonical,
        "alternatives": exact,
        "source_term": canonical,
        "source": "glossary",
        "equivalent": True,
    }]


def needs_model_expansion(question: str, matched_terms: list,
                          glossary: dict) -> tuple[bool, str]:
    """Detect meaningful Chinese residue left by partial glossary matches."""
    if not matched_terms:
        return True, question
    residual = unicodedata.normalize("NFKC", question)
    removable = []
    for term in matched_terms:
        entry = get_glossary_entry(term, glossary)
        removable.extend([term] + list(entry.get("aliases", [])))
    for term in sorted(set(removable), key=len, reverse=True):
        residual = re.sub(re.escape(term), " ", residual, flags=re.IGNORECASE)
    residual = _remove_boilerplate_phrases(residual)
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
        names = (
            [cn_term]
            + list(entry.get("aliases", []))
            + get_glossary_exact_terms(cn_term, glossary)
        )
        for matched_name in names:
            normalized_term = normalize_key(matched_name)
            for start, end in _iter_term_spans(normalized_question, normalized_term):
                candidates.append((
                    start, end, cn_term, entry, matched_name
                ))

    selected = []
    occupied = []
    for start, end, cn_term, entry, matched_name in sorted(
            candidates, key=lambda item: (
                -(item[1] - item[0]),
                -{"high": 2, "normal": 1, "low": 0}.get(
                    str(item[3].get("priority", "normal")).casefold(), 1
                ),
                item[0],
            )):
        if any(start < used_end and end > used_start
               for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((start, cn_term, entry, matched_name))

    for _, cn_term, entry, _matched_name in sorted(selected, key=lambda item: item[0]):
        if cn_term not in matched_terms:
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
    """Validate glossary structure, aliases, and exact-term ownership."""
    glossary = load_glossary()
    errors = []
    warnings = []
    aliases = {}
    canonical_keys = {normalize_key(term): term for term in glossary}
    exact_owners = {}
    for term in glossary:
        entry = get_glossary_entry(term, glossary)
        de_terms = get_glossary_expansions(term, glossary)
        if not de_terms:
            warnings.append(f"术语 '{term}' 没有德语对应词")
        for value in de_terms:
            if not value.strip():
                errors.append(f"术语 '{term}' 德语词条为空字符串")
        if entry.get("_legacy"):
            warnings.append(
                f"术语 '{term}' 使用旧格式，只能用于宽召回，不能提供短语级证据资格"
            )
        for alias in entry.get("aliases", []):
            key = normalize_key(alias)
            if key in canonical_keys and canonical_keys[key] != term:
                errors.append(
                    f"别名 '{alias}' 与正式词条 '{canonical_keys[key]}' 冲突"
                )
            if key in aliases and aliases[key] != term:
                errors.append(
                    f"别名 '{alias}' 同时指向 '{aliases[key]}' 和 '{term}'"
                )
            aliases[key] = term
        for exact in get_glossary_exact_terms(term, glossary):
            exact_owners.setdefault(normalize_key(exact), []).append((
                term, str(entry.get("priority", "normal")).casefold()
            ))

    priority_rank = {"high": 2, "normal": 1, "low": 0}
    for exact_key, owners in exact_owners.items():
        if len(owners) < 2:
            continue
        best = max(priority_rank.get(priority, 1) for _, priority in owners)
        winners = [term for term, priority in owners
                   if priority_rank.get(priority, 1) == best]
        if len(winners) > 1:
            warnings.append(
                f"德语精确词 '{exact_key}' 有同优先级归属: {', '.join(winners)}"
            )

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
