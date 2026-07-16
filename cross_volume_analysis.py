#!/usr/bin/env python3
"""
MEGA² RAG — 跨卷分析层
当用户问题涉及跨时期变化时，做时间聚合 + 结构化证据链，再交给 Pro 综合。
"""
import re
import sqlite3
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path

from snippet_extractor import text_layer_label


# MEGA 卷册 → 时间映射 (Abteilung/Band → 年份范围)
VOLUME_TIMELINE = {
    # I — 早期著作
    ("I", "1"):  (1841, 1843, "博士论文、早期政论"),
    ("I", "2"):  (1843, 1844, "黑格尔法哲学批判、论犹太人问题、1844年经济学哲学手稿"),
    ("I", "3"):  (1838, 1844, "恩格斯早期著作、文章与文学作品"),
    ("I", "5"):  (1845, 1847, "德意志意识形态"),
    ("I", "10"): (1852, 1853, "纽约论坛报文章"),
    ("I", "12"): (1853, 1854, "纽约论坛报文章"),
    ("I", "13"): (1854, 1855, "纽约论坛报文章"),
    ("I", "14"): (1855, 1856, "纽约论坛报文章"),
    ("I", "18"): (1857, 1859, "1857-59 经济学手稿时期"),
    ("I", "20"): (1859, 1860, "《政治经济学批判》出版前后"),
    ("I", "22"): (1861, 1862, "南北战争相关文章"),
    ("I", "24"): (1863, 1864, "波兰问题、国际工人协会成立"),
    ("I", "25"): (1864, 1865, "国际工人协会早期活动"),
    ("I", "26"): (1865, 1866, "国际工人协会"),
    ("I", "27"): (1866, 1868, "反杜林论时期"),
    ("I", "29"): (1873, 1883, "自然辩证法、晚年著作"),
    ("I", "31"): (1876, 1878, "反杜林论、社会主义从空想到科学"),
    # II — 资本论
    ("II", "1"):  (1857, 1858, "Grundrisse 大纲"),
    ("II", "3"):  (1861, 1863, "剩余价值理论 / 1861-63手稿"),
    ("II", "4"):  (1863, 1864, "资本论手稿"),
    ("II", "5"):  (1865, 1866, "资本论第一卷手稿"),
    ("II", "6"):  (1867, 1870, "资本论第二卷手稿"),
    ("II", "7"):  (1867, 1870, "资本论第三卷手稿"),
    ("II", "8"):  (1867, 1867, "资本论第一卷付印稿"),
    ("II", "9"):  (1867, 1872, "资本论第一卷再版准备"),
    ("II", "10"): (1863, 1865, "资本论经济学手稿"),
    ("II", "11"): (1868, 1879, "资本论第二卷手稿"),
    ("II", "12"): (1868, 1879, "资本论第三卷手稿"),
    ("II", "13"): (1868, 1880, "资本论第四卷"),
    ("II", "14"): (1870, 1875, "利润率趋向下降/地租手稿"),
    ("II", "15"): (1870, 1878, "资本论手稿"),
    # III — 书信
    ("III", "1"):  (1846, 1847, "书信 — 布鲁塞尔时期"),
    ("III", "2"):  (1848, 1849, "书信 — 革命时期"),
    ("III", "3"):  (1850, 1851, "书信 — 流亡伦敦初期"),
    ("III", "4"):  (1852, 1852, "书信 — 科隆共产党人审判"),
    ("III", "5"):  (1853, 1853, "书信 — 纽约论坛报撰稿"),
    ("III", "6"):  (1854, 1854, "书信 — 克里米亚战争时期"),
    ("III", "7"):  (1855, 1856, "书信 — 个人悲剧/经济困境"),
    ("III", "8"):  (1856, 1857, "书信 — 经济危机前夕"),
    ("III", "9"):  (1858, 1858, "书信 — 危机年"),
    ("III", "10"): (1859, 1860, "书信 — Vogt事件"),
    ("III", "11"): (1861, 1862, "书信 — 南北战争"),
    ("III", "13"): (1863, 1864, "书信 — 拉萨尔/全德工人联合会"),
    # IV — 摘录
    ("IV", "1"):  (1840, 1843, "摘录 — 早期哲学/法学"),
    ("IV", "2"):  (1844, 1845, "摘录 — 政治经济学"),
    ("IV", "3"):  (1844, 1846, "摘录 — 政治经济学"),
    ("IV", "4"):  (1850, 1851, "摘录 — 货币/信用"),
    ("IV", "6"):  (1851, 1853, "摘录 — 技术史/化学"),
    ("IV", "7"):  (1857, 1858, "摘录 — 危机理论"),
    ("IV", "12"): (1863, 1864, "摘录 — 剩余价值理论"),
    ("IV", "31"): (1870, 1880, "摘录 — 自然科学/数学"),
}


_TEMPORAL_PATTERNS = [
    '不同时期', '时期变化', '变化过程', '如何变化', '什么变化',
    '从.*到.*如何', '发展的过程', '演变', '时间线', '时间轴',
    'how did.*change', 'over time', 'timeline', 'évolution',
    'transformiert', 'Wandel', 'Veränderung', 'Entwicklung',
]


def is_temporal_query(question: str) -> bool:
    text = (question or "").lower()
    return any(pattern.lower() in text for pattern in _TEMPORAL_PATTERNS)


_FINANCE_MARKERS = (
    "经济状况", "财务", "债务", "收入", "稿费", "资助", "借钱", "贫困",
    "geld", "schuld", "einkommen", "honorar", "finanz", "kredit", "erbschaft",
)


def is_personal_finance_query(question: str) -> bool:
    text = (question or "").lower()
    return any(marker in text for marker in _FINANCE_MARKERS)


def _metadata_db_path() -> str:
    import yaml
    config_path = Path(__file__).parent / "config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["paths"]["metadata_db"]


def _fts_terms(terms: List[str]) -> List[str]:
    clean = []
    for term in terms:
        term = (term or "").strip()
        if len(term) < 3:
            continue
        if re.fullmatch(r"[A-Za-z\u00c0-\u024f]+", term) and term not in clean:
            clean.append(term)
    return clean


def _layer_select_sql(conn: sqlite3.Connection, alias: str = "c") -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "text_layer" not in columns:
        return "'unclassified', 0, ''"
    return (
        f"COALESCE({alias}.text_layer, 'unclassified'), "
        f"COALESCE({alias}.text_layer_confidence, 0), "
        f"COALESCE({alias}.text_layer_provenance, '')"
    )


def _retrieve_volume_evidence(conn, abteilung: str, band: str, terms: List[str]) -> list[dict]:
    """Use one scoped FTS query per volume; LIKE is only a narrow fallback."""
    rows = []
    layer_columns = _layer_select_sql(conn)
    fts_terms = _fts_terms(terms)
    if fts_terms:
        fts_query = " OR ".join(fts_terms[:12])
        try:
            rows = conn.execute(
                f"""
                SELECT c.page, c.source_type, c.is_main_text, c.chunk_text,
                       COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''),
                       {layer_columns}
                FROM chunks_fts f JOIN chunks c ON f.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                  AND c.mega_abteilung = ? AND c.band = ? AND c.source_type = 'TEXT'
                ORDER BY rank LIMIT 3
                """,
                (fts_query, abteilung, band),
            ).fetchall()
        except sqlite3.Error:
            rows = []

    if not rows and terms:
        clauses = " OR ".join("c.chunk_text LIKE ?" for _ in terms[:8])
        params = [f"%{term}%" for term in terms[:8]]
        rows = conn.execute(
            f"""
            SELECT c.page, c.source_type, c.is_main_text, c.chunk_text,
                   COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''),
                   {layer_columns}
            FROM chunks c
            WHERE c.mega_abteilung = ? AND c.band = ? AND c.source_type = 'TEXT'
              AND ({clauses})
            ORDER BY c.char_count DESC LIMIT 3
            """,
            [abteilung, band, *params],
        ).fetchall()

    return [
        {
            "page": row[0],
            "text_layer": row[6],
            "text_layer_confidence": row[7],
            "text_layer_provenance": row[8],
            "text_type": text_layer_label({"text_layer": row[6]}),
            "text": (row[3] or "")[:320],
            "page_kind": row[4],
            "page_label": row[5],
        }
        for row in rows
    ]


def build_temporal_evidence(results: List[Dict], core_terms: List[str] = None,
                            search_terms: List[str] = None, question: str = "") -> Dict:
    """Build a bounded timeline from scoped letter-volume evidence."""
    import sqlite3

    core_terms = core_terms or []
    search_terms = search_terms or []
    terms = []
    for term in [*core_terms, *search_terms]:
        if isinstance(term, dict):
            terms.extend(term.get("expansions", []))
            terms.append(term.get("term", ""))
        else:
            terms.append(str(term))
    if is_personal_finance_query(question):
        terms.extend([
            "Pfund", "Geld", "Schuld", "Einkommen", "Tribune", "Kredit",
            "Erbschaft", "Honorar", "finanziell", "Armut", "Not", "Vorschuss",
            "Sterling", "Geldnot", "Schulden", "Gläubiger",
        ])
    terms = list(dict.fromkeys(term for term in terms if term))[:20]

    conn = sqlite3.connect(_metadata_db_path())
    per_volume = {}
    try:
        for (abteilung, band), (year_start, year_end, description) in VOLUME_TIMELINE.items():
            if abteilung != "III":
                continue
            items = _retrieve_volume_evidence(conn, abteilung, band, terms)
            if items:
                per_volume[(abteilung, band)] = {
                    "period": f"{year_start}-{year_end}",
                    "years": (year_start, year_end),
                    "description": description,
                    "abt": abteilung,
                    "band": band,
                    "items": items,
                }
    finally:
        conn.close()

    time_groups = dict(per_volume)
    for result in results:
        abteilung = result.get("abteilung", "?")
        band = str(result.get("band", "?"))
        key = (abteilung, band)
        if key in time_groups or key not in VOLUME_TIMELINE:
            continue
        year_start, year_end, description = VOLUME_TIMELINE[key]
        time_groups[key] = {
            "period": f"{year_start}-{year_end}",
            "years": (year_start, year_end),
            "description": description,
            "abt": abteilung,
            "band": band,
            "items": [{
                "page": result.get("page", "?"),
                "text_type": text_layer_label(result),
                "text": (result.get("text") or "")[:320],
                "page_kind": result.get("page_kind", "pdf"),
                "page_label": result.get("page_label", ""),
            }],
        }

    periods = sorted(time_groups.values(), key=lambda item: item["years"])
    lines = ["# 跨卷时间线分析", f"覆盖 {len(periods)} 个时期"]
    for period in periods:
        lines.append(f"\n## {period['period']} - {period['description']} (MEGA {period['abt']}/{period['band']})")
        for item in period["items"][:2]:
            label = f"source p. {item['page']}"
            if item.get("page_label"):
                label += f"; text p. {item['page_label']}"
            lines.append(f"\n[{label}] [{item['text_type']}]\n> {item['text']}")

    return {"periods": periods, "timeline_text": "\n".join(lines), "period_count": len(periods)}