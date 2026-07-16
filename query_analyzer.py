#!/usr/bin/env python3
"""
MEGA² RAG — 通用查询理解
输入: 用户原始问题 + 术语表匹配结果
输出: 分类后的 query profile (core_terms, work_terms, author_terms, intent, target_volume)
"""
import re
from typing import List, Dict, Optional

# ---- 德语/英语停用词和泛学术词 (低优先级) ----
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

# ---- 意图分类规则 ----
_AUTHOR_INTENT_PATTERNS = [
    '怎么讨论', '如何讨论', '如何论述', '怎么论述', '如何理解', '怎么理解',
    '概念', '范畴', '论述', '讨论', '观点', '理论', '思想',
    'Begriff', 'discusses', 'discuss', 'critique', 'argument', 'passage',
    'Kritik', 'Theorie', 'Auffassung', 'Darstellung',
]

_APPARAT_INTENT_PATTERNS = [
    '编者注', '编者说明', 'Apparat', 'apparat', '异文', '版本', '手稿',
    '出处', '注释', '校勘', '考证', '编辑', '排印',
    'variant', 'manuscript', 'editorial', 'apparatus', 'Handschrift',
    'Entstehung', 'Überlieferung', 'Originalhandschrift',
]

# ---- 著作名 → (Abteilung, Band) ----
WORK_VOLUME_MAP = {
    "黑格尔法哲学批判": ("I", "2"),
    "博士论文": ("I", "1"),
    "神圣家族": ("I", "2"),
    "论犹太人问题": ("I", "2"),
    "1844": ("I", "2"),
    "经济学哲学手稿": ("I", "2"),
    "关于费尔巴哈的提纲": ("I", "5"),
    "德意志意识形态": ("I", "5"),
    "形态": ("I", "5"),
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

# ---- 作者/泛词列表 (低优先级) ----
_GENERIC_TERMS = {
    'Marx', 'marx', 'Engels', 'engels', 'Hegel', 'hegel', 'Feuerbach',
    'Kritik', 'Philosophie', 'Kritik', 'Werk', 'Brief', 'Manuskript',
    'Text', 'Schrift', 'Band', 'Ausgabe', 'MEGA',
}


def analyze_query(question: str, expanded_query: str = "",
                  matched_terms: List[str] = None,
                  glossary_entries: Dict = None) -> Dict:
    """
    返回 query_profile:
    {
        "core_terms": [...],       # 核心概念词，最高优先级
        "work_terms": [...],       # 著作名/卷册提示
        "author_terms": [...],     # 作者名
        "generic_terms": [...],    # 泛词/低优先级
        "intent": "author_argument" | "apparat_question" | "general_search",
        "target_abteilung": str,   # 目标 Abteilung (I/II/III/IV)
        "target_band": str,        # 目标 Band
    }
    """
    if matched_terms is None:
        matched_terms = []
    if glossary_entries is None:
        glossary_entries = {}

    core_terms = []
    work_terms = []
    author_terms = []
    generic_terms = []

    # 1. 从 glossary 匹配词分类
    for term in matched_terms:
        entry = glossary_entries.get(term, {})
        if isinstance(entry, str):
            # 旧格式：简单字符串，用规则推断
            de_str = entry
            if _is_concept_term(term, de_str):
                core_terms.append(term)
            elif _is_work_term(term):
                work_terms.append(term)
            elif _is_author_term(term):
                author_terms.append(term)
            else:
                generic_terms.append(term)
        elif isinstance(entry, dict):
            # 新格式或有 type 字段
            etype = entry.get('type', '')
            de_terms = ' '.join(entry.get('de', []))
            if etype == 'concept' or (not etype and _is_concept_term(term, de_terms)):
                core_terms.append(term)
            elif etype == 'work' or (not etype and _is_work_term(term)):
                work_terms.append(term)
            elif etype == 'author' or (not etype and _is_author_term(term)):
                author_terms.append(term)
            else:
                generic_terms.append(term)

    # 2. 从问题文本检测著作名
    for work_name, (abt, band) in WORK_VOLUME_MAP.items():
        if work_name in question:
            if work_name not in work_terms:
                work_terms.append(work_name)

    # 3. 意图分类
    intent = _classify_intent(question)

    # 4. 无 glossary 命中时，从原始查询提取德语词法词条
    lexical = {}
    if not matched_terms:
        lexical = extract_lexical_terms(question, expanded_query)
        core_terms.extend(lexical.get('lexical_core', []))
        generic_terms.extend(lexical.get('lexical_generic', []))

    # 5. 目标卷册
    target_abteilung = None
    target_band = None
    for work_name in work_terms:
        abt_band = WORK_VOLUME_MAP.get(work_name)
        if abt_band:
            target_abteilung = abt_band[0]
            target_band = abt_band[1]
            break

    return {
        "core_terms": core_terms,
        "work_terms": work_terms,
        "author_terms": author_terms,
        "generic_terms": generic_terms,
        "lexical_core": lexical.get('lexical_core', []),
        "intent": intent,
        "target_abteilung": target_abteilung,
        "target_band": target_band,
    }


def extract_lexical_terms(question: str, expanded_query: str = None) -> Dict:
    """
    从原始查询中提取德语/英语词法词条，按重要性分级。
    用于纯德语查询无 glossary 命中时的 fallback。
    """
    text = (expanded_query or "") + " " + question
    words = re.findall(r'[a-zA-ZäöüßÄÖÜẞ]{3,}', text)
    seen = set()
    core_lex = []
    work_lex = []
    generic_lex = []

    for w in words:
        wl = w.lower()
        if wl in seen:
            continue
        seen.add(wl)

        if wl in _STOPWORDS:
            continue
        elif wl in _GENERIC_ACADEMIC:
            generic_lex.append(w)
        else:
            # 非停用词非泛词 → 可能是概念词或作品词
            core_lex.append(w)

    return {
        "lexical_core": core_lex,
        "lexical_generic": generic_lex,
    }


def build_priority_terms(query_profile: Dict,
                         glossary_entries: Dict = None) -> List[str]:
    """
    按优先级排列德语/检索词：glossary core > lexical core > work > author > generic。
    兼容 glossary 新旧格式，无 glossary 命中时自动回退词法提取。
    """
    p = query_profile
    if glossary_entries is None:
        glossary_entries = {}

    terms = []
    seen = set()

    def _add(word: str):
        w = word.strip().rstrip(',;.')
        if w and w not in seen and len(w) > 1:
            seen.add(w)
            terms.append(w)

    def _expand_from_glossary(cn_term: str):
        entry = glossary_entries.get(cn_term, {})
        if isinstance(entry, str):
            for dw in entry.split():
                _add(dw)
        elif isinstance(entry, dict):
            raw = entry.get('de', [])
            if isinstance(raw, str):
                for dw in raw.split():
                    _add(dw)
            else:
                for item in raw:
                    for dw in str(item).split():
                        _add(dw)

    # 1. glossary-core expansions
    for t in p.get('core_terms', []):
        if t in p.get('lexical_core', []):
            continue  # 是词法词条，已在下面处理
        _expand_from_glossary(t)

    # 2. lexical-core (无 glossary 命中时的德语词)
    for t in p.get('lexical_core', []):
        _add(t)

    # 3. work terms
    for t in p.get('work_terms', []):
        _expand_from_glossary(t)

    # 4. author terms
    for t in p.get('author_terms', []):
        _expand_from_glossary(t)

    # 5. generic terms
    for t in p.get('generic_terms', []):
        _expand_from_glossary(t)

    return terms


def _is_concept_term(cn_term: str, de_terms: str) -> bool:
    """推断中文术语是否为概念词"""
    concept_indicators = ['异化', '剩余', '阶级', '资本', '价值', '劳动', '形态',
                          '意识', '生产', '商品', '货币', '利润', '私有', '剥削',
                          'subsumption', '归摄', 'alienation', '共同体', '国家']
    return any(ind in cn_term.lower() for ind in concept_indicators)


def _is_work_term(cn_term: str) -> bool:
    work_indicators = ['批判', '意识形态', '手稿', '提纲', '宣言', '资本论',
                       '哲学', '批判', '导言', '论战', 'Grundrisse']
    return any(ind in cn_term for ind in work_indicators)


def _is_author_term(cn_term: str) -> bool:
    authors = ['马克思', '恩格斯', '黑格尔', '费尔巴哈', '李嘉图',
               '斯密', '蒲鲁东', 'Marx', 'Engels', 'Hegel', 'Feuerbach']
    return cn_term in authors


def _classify_intent(question: str) -> str:
    q_lower = question.lower()
    has_author = any(p.lower() in q_lower for p in _AUTHOR_INTENT_PATTERNS)
    has_apparat = any(p.lower() in q_lower for p in _APPARAT_INTENT_PATTERNS)
    # author_argument 优先：当同时命中时，"怎么讨论"比"手稿"更表达用户意图
    if has_author:
        return "author_argument"
    if has_apparat:
        return "apparat_question"
    return "general_search"
