#!/usr/bin/env python3
"""General query understanding for the MEGA² retrieval pipeline."""
import re
from typing import Dict, List

from glossary_loader import (
    get_glossary_canonical_term,
    get_glossary_entry,
    get_glossary_exact_terms,
    get_glossary_expansions,
    get_glossary_qualification_groups,
    normalize_key,
)


_STOPWORDS = set("""
der die das den dem des ein eine einen einer einem und oder in zu zur zum
von mit auf sich ist nicht als auch es sie er war wird werden sein diese
ihr ihre ihnen kann noch haben vor nur durch bis nach schon unter wurde
worden bei aber über
the a an and or in on to for of with by from at is are was were be been
this that these those
""".split())

_GENERIC_ACADEMIC = set("""
kritik philosophie hegel marx engels werk text schrift band ausgabe mega
theorie begriff lehre geschichte darstellung frage problem analyse
critique philosophy theory concept history work text volume edition
""".split())

_AUTHOR_INTENT_PATTERNS = [
    "怎么讨论", "如何讨论", "如何论述", "怎么论述", "如何理解", "怎么理解",
    "是什么", "何谓", "含义", "关系", "如何展开", "如何形成", "怎么形成",
    "如何发展", "怎么发展", "概念", "范畴", "论述", "讨论", "观点", "理论", "思想",
    "Begriff", "discusses", "discuss", "critique", "argument", "passage",
    "what is", "was ist", "Kritik", "Theorie", "Auffassung", "Darstellung",
]

_STRONG_APPARAT_INTENT_PATTERNS = [
    "编者注", "编者说明", "Apparat", "apparat", "异文", "版本",
    "出处", "注释", "校勘", "考证", "编辑", "排印",
    "variant", "editorial", "apparatus", "Entstehung", "Überlieferung",
    "Originalhandschrift",
]

_WEAK_APPARAT_INTENT_PATTERNS = ["手稿", "manuscript", "Handschrift"]

WORK_VOLUME_MAP = {
    "黑格尔法哲学批判": ("I", "2"),
    "博士论文": ("I", "1"),
    "神圣家族": ("I", "2"),
    "论犹太人问题": ("I", "2"),
    "1844手稿": ("I", "2"),
    "1844年手稿": ("I", "2"),
    "经济学哲学手稿": ("I", "2"),
    "关于费尔巴哈的提纲": ("I", "5"),
    "德意志意识形态": ("I", "5"),
    "哲学的贫困": ("I", "6"),
    "共产党宣言": ("I", "6"),
    "资本论": ("II", None),
    "Grundrisse": ("II", "1"),
    "大纲": ("II", "1"),
    "政治经济学批判": ("II", "2"),
    "剩余价值理论": ("II", "3"),
    "自然辩证法": ("I", "26"),
    "反杜林论": ("I", "27"),
    "家庭私有制和国家的起源": ("I", "29"),
    "费尔巴哈论": ("I", "30"),
}


def _append_unique(values: list, value: str) -> None:
    key = normalize_key(value)
    if value and all(normalize_key(existing) != key for existing in values):
        values.append(value)


def _all_spans(text: str, value: str) -> List[tuple[int, int]]:
    """Return all case-insensitive spans in one normalized coordinate space."""
    haystack = str(text or "").casefold()
    needle = str(value or "").strip().casefold()
    if not needle:
        return []
    output = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return output
        output.append((index, index + len(needle)))
        start = index + max(1, len(needle))


def _concept_outside_work_title(question: str, term: str,
                                work_terms: List[str], glossary: Dict) -> bool:
    """Keep a concept only when it occurs outside a detected work-title span."""
    work_spans = []
    for work in work_terms:
        entry = get_glossary_entry(work, glossary)
        names = [work] + list(entry.get("aliases", []))
        names.extend(get_glossary_exact_terms(work, glossary))
        for name in names:
            work_spans.extend(_all_spans(question, name))
    if not work_spans:
        return True

    entry = get_glossary_entry(term, glossary)
    names = [term] + list(entry.get("aliases", []))
    names.extend(get_glossary_exact_terms(term, glossary))
    occurrences = [span for name in names for span in _all_spans(question, name)]
    if not occurrences:
        return True
    return any(
        not any(work_start <= start and end <= work_end
                for work_start, work_end in work_spans)
        for start, end in occurrences
    )

def analyze_query(question: str, expanded_query: str = "",
                  matched_terms: List[str] = None,
                  glossary_entries: Dict = None) -> Dict:
    """Classify concept, work, author, lexical and intent signals."""
    matched_terms = matched_terms or []
    glossary_entries = glossary_entries or {}
    core_terms: List[str] = []
    work_terms: List[str] = []
    author_terms: List[str] = []
    generic_terms: List[str] = []

    canonical_matched_terms: List[str] = []
    for term in matched_terms:
        canonical = get_glossary_canonical_term(term, glossary_entries)
        _append_unique(canonical_matched_terms, canonical)
        entry = get_glossary_entry(canonical, glossary_entries)
        entry_type = str(entry.get("type", "")).casefold()
        de_terms = " ".join(get_glossary_expansions(canonical, glossary_entries))
        if entry_type in {"concept", "topic"} or (
                not entry_type and _is_concept_term(canonical, de_terms)):
            _append_unique(core_terms, canonical)
        elif entry_type == "work" or (not entry_type and _is_work_term(canonical)):
            _append_unique(work_terms, canonical)
        elif entry_type == "author" or (not entry_type and _is_author_term(canonical)):
            _append_unique(author_terms, canonical)
        else:
            _append_unique(generic_terms, canonical)
    matched_terms = canonical_matched_terms

    for work_name in WORK_VOLUME_MAP:
        if work_name in question:
            _append_unique(work_terms, work_name)

    # A concept that appears only inside a work title is scope metadata, not
    # the subject of the question. Keep it as low-priority recall context, but
    # do not let it qualify evidence. A second occurrence outside the title
    # remains a genuine core concept.
    title_embedded_terms = []
    retained_core_terms = []
    for term in core_terms:
        if _concept_outside_work_title(question, term, work_terms, glossary_entries):
            retained_core_terms.append(term)
        else:
            _append_unique(title_embedded_terms, term)
            _append_unique(generic_terms, term)
    core_terms = retained_core_terms

    intent = _classify_intent(question)

    # Always inspect Latin/German additions. When glossary matches exist, remove
    # their known tokens so only model-added or user-entered lexical terms remain.
    lexical_all = extract_lexical_terms(question, expanded_query)
    known_tokens = set()
    for term in matched_terms:
        for expansion in get_glossary_expansions(term, glossary_entries):
            known_tokens.update(
                token.casefold() for token in
                re.findall(r"[A-Za-zäöüßÄÖÜẞ]{3,}", expansion)
            )
    if matched_terms:
        lexical_core = [
            value for value in lexical_all["lexical_core"]
            if value.casefold() not in known_tokens
        ]
        lexical_generic = [
            value for value in lexical_all["lexical_generic"]
            if value.casefold() not in known_tokens
        ]
    else:
        lexical_core = lexical_all["lexical_core"]
        lexical_generic = lexical_all["lexical_generic"]
    for value in lexical_core:
        _append_unique(core_terms, value)
    for value in lexical_generic:
        _append_unique(generic_terms, value)

    core_expansions: List[str] = []
    recall_terms: List[str] = []
    concept_groups = []
    qualification_groups = []
    for term in core_terms:
        exact_terms = get_glossary_exact_terms(term, glossary_entries)
        recall = get_glossary_expansions(term, glossary_entries)
        for expansion in exact_terms or recall:
            _append_unique(core_expansions, expansion)
        for expansion in recall:
            _append_unique(recall_terms, expansion)
        groups = get_glossary_qualification_groups(term, glossary_entries)
        qualification_groups.extend(groups)
        entry = get_glossary_entry(term, glossary_entries)
        for sense in entry.get("senses", []):
            concept_groups.append({
                "id": str(sense.get("id", "")),
                "label": str(sense.get("label", sense.get("id", ""))),
                "terms": [str(value) for value in sense.get("de", []) if str(value)],
                "source_term": term,
            })

    # User/model supplied lexical residue remains a first-class signal even
    # when another glossary concept matched. Multi-term lexical input is a
    # proximity group, not a bag of independent direct-evidence terms.
    if lexical_core:
        qualification_groups.append({
            "id": "lexical_core",
            "label": "lexical core",
            "alternatives": list(lexical_core),
            "source_term": None,
            "source": "lexical_fallback",
            "equivalent": True,
            "match_mode": "all_near" if len(lexical_core) > 1 else "any",
            "window_chars": 220,
        })

    # Preserve every work scope.  The compatibility pair below still exposes
    # the first scope to older callers, while QueryPlan can route each work
    # independently.
    target_volumes = []
    for work_name in work_terms:
        abt_band = WORK_VOLUME_MAP.get(work_name)
        if abt_band:
            abteilung, band = abt_band
            candidate = {
                "label": "/".join(filter(None, [abteilung, band])),
                "abteilung": abteilung,
                "band": band,
            }
            if candidate not in target_volumes:
                target_volumes.append(candidate)
    if not target_volumes:
        for term in matched_terms:
            entry = get_glossary_entry(term, glossary_entries)
            abt_hint = entry.get("abteilung_hint")
            band_hint = entry.get("band_hint")
            if isinstance(abt_hint, str):
                band = str(band_hint) if band_hint is not None else None
                target_volumes.append({
                    "label": "/".join(filter(None, [abt_hint, band])),
                    "abteilung": abt_hint,
                    "band": band,
                })
                break
    first_scope = target_volumes[0] if target_volumes else {}
    target_abteilung = first_scope.get("abteilung")
    target_band = first_scope.get("band")

    return {
        "core_terms": core_terms,
        "core_expansions": core_expansions,
        "recall_terms": recall_terms,
        "qualification_groups": qualification_groups,
        "concept_groups": concept_groups,
        "work_terms": work_terms,
        "author_terms": author_terms,
        "generic_terms": generic_terms,
        "title_embedded_terms": title_embedded_terms,
        "lexical_core": lexical_core,
        "intent": intent,
        "target_abteilung": target_abteilung,
        "target_volumes": target_volumes,
        "target_band": target_band,
    }


def extract_lexical_terms(question: str, expanded_query: str = None) -> Dict:
    """Extract German/English lexical fallback terms."""
    text = (expanded_query or "") + " " + question
    words = re.findall(r"[a-zA-ZäöüßÄÖÜẞ]{3,}", text)
    seen = set()
    core_lex = []
    generic_lex = []
    for word in words:
        lowered = word.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        if lowered in _STOPWORDS:
            continue
        if lowered in _GENERIC_ACADEMIC:
            generic_lex.append(word)
        else:
            core_lex.append(word)
    return {"lexical_core": core_lex, "lexical_generic": generic_lex}


def build_priority_terms(query_profile: Dict,
                         glossary_entries: Dict = None) -> List[str]:
    """Build phrase-preserving priorities: core > lexical > work > author."""
    profile = query_profile
    glossary_entries = glossary_entries or {}
    terms: List[str] = []

    def add(value: str):
        cleaned = str(value or "").strip().rstrip(",;.")
        if len(cleaned) > 1:
            _append_unique(terms, cleaned)

    for value in profile.get("core_expansions", []):
        add(value)
    for value in profile.get("lexical_core", []):
        add(value)
    for category in ("work_terms", "author_terms", "generic_terms"):
        for term in profile.get(category, []):
            for expansion in get_glossary_expansions(term, glossary_entries):
                add(expansion)
    return terms


def _is_concept_term(cn_term: str, de_terms: str) -> bool:
    concept_indicators = [
        "异化", "剩余", "阶级", "资本", "价值", "劳动", "形态", "意识",
        "生产", "商品", "货币", "利润", "私有", "剥削", "subsumption",
        "归摄", "alienation", "共同体", "国家", "贱民",
    ]
    return any(indicator in cn_term.casefold() for indicator in concept_indicators)


def _is_work_term(cn_term: str) -> bool:
    indicators = [
        "批判", "意识形态", "手稿", "提纲", "宣言", "资本论", "哲学",
        "导言", "论战", "Grundrisse",
    ]
    return any(indicator in cn_term for indicator in indicators)


def _is_author_term(cn_term: str) -> bool:
    authors = [
        "马克思", "恩格斯", "黑格尔", "费尔巴哈", "李嘉图", "斯密", "蒲鲁东",
        "Marx", "Engels", "Hegel", "Feuerbach",
    ]
    return cn_term in authors


def _classify_intent(question: str) -> str:
    lowered = question.casefold()
    has_author = any(pattern.casefold() in lowered for pattern in _AUTHOR_INTENT_PATTERNS)
    has_strong_apparat = any(
        pattern.casefold() in lowered for pattern in _STRONG_APPARAT_INTENT_PATTERNS
    )
    has_weak_apparat = any(
        pattern.casefold() in lowered for pattern in _WEAK_APPARAT_INTENT_PATTERNS
    )
    if has_strong_apparat:
        return "apparat_question"
    if has_author:
        return "author_argument"
    if has_weak_apparat:
        return "apparat_question"
    return "general_search"
