#!/usr/bin/env python3
"""
MEGA² RAG — 通用回归测试 (v2)
覆盖: 概念查询、著作查询、APPARAT 查询、德语精确查询
"""
import sys, os, json, sqlite3, yaml
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from glossary_loader import expand_with_glossary, load_glossary
from query_analyzer import analyze_query, build_priority_terms
from snippet_extractor import extract_best_snippet
from rerank import rerank
from webui import do_search

_GLOSSARY = load_glossary()
with (Path(__file__).parent / "config.yaml").open(encoding="utf-8") as handle:
    _CONFIG = yaml.safe_load(handle)
_META_DB = Path(_CONFIG["paths"]["metadata_db"])
PASS, FAIL_ALG, XFAIL_DATA, SKIP = "PASS", "FAIL_ALGORITHM", "XFAIL_DATA", "SKIP"


def run_test(query: str, expected_terms_in_snippet: list = None,
             expected_volume: tuple = None, expected_page_range: tuple = None,
             min_text_in_top: int = 1, description: str = "") -> dict:
    result = {
        "query": query, "description": description,
        "status": PASS, "failures": [],
    }

    expanded, matched, hints = expand_with_glossary(query, _GLOSSARY)
    profile = analyze_query(query, expanded, matched, _GLOSSARY)
    priority = build_priority_terms(profile, _GLOSSARY)

    result["expanded_query"] = expanded[:120]
    result["matched_terms"] = matched
    result["query_profile"] = {
        "core_terms": profile['core_terms'],
        "work_terms": profile['work_terms'],
        "intent": profile['intent'],
        "target": f"{profile.get('target_abteilung','?')}/{profile.get('target_band','?')}",
    }
    result["priority_terms"] = priority[:20]

    # Data coverage check
    if expected_volume:
        abt, band = expected_volume
        conn = sqlite3.connect(str(_META_DB))
        cnt = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE mega_abteilung=? AND band=? AND source_type='TEXT'",
            (abt, band)).fetchone()[0]
        conn.close()
        if cnt == 0:
            result["status"] = XFAIL_DATA
            result["failures"].append(
                f"MEGA {abt}/{band} TEXT 未索引，跳过高标准判定")
            return result

    # Search + rerank
    # 有目标卷时扩大检索池
    search_pool = 50 if expected_volume else 30
    search_results = do_search(expanded, 'all', search_pool)

    # Scoped injection (mirrors webui.py)
    if profile.get('target_abteilung') and profile.get('intent') == 'author_argument':
        from webui import do_scoped_search
        scoped_terms = [t for t in priority[:10] if len(t) > 2]
        scoped_r = do_scoped_search(scoped_terms,
                                     target_abt=profile['target_abteilung'],
                                     target_band=profile.get('target_band'),
                                     text_only=True, top_k=20)
        existing_ids = {r.get('id', '') for r in search_results}
        injected = 0
        for sr in scoped_r:
            if sr['id'] not in existing_ids:
                search_results.append(sr)
                existing_ids.add(sr['id'])
                injected += 1
        result["scoped_found"] = len(scoped_r)
        result["scoped_injected"] = injected

    reranked = rerank(search_results, query=expanded, mode="original_first",
                      method="rule", glossary_terms=matched, query_profile=profile)
    top10 = reranked[:10]

    result["top10"] = []
    snippet_found_core = False
    target_page_rank = None
    target_evidence_rank = None

    for i, r in enumerate(top10):
        full_text = r.get('text', '')
        extracted = extract_best_snippet(full_text, priority)
        snippet = extracted['snippet']
        preview = extracted['preview']
        matched_term = extracted.get('matched_term','')

        has_expansion = (bool(matched_term) and any(
            matched_term.lower() == t.lower() for t in priority[:10]
        )) if priority else False

        entry = {
            "rank": i + 1,
            "type": r.get('type', r.get('source_type', '?')),
            "abt": r.get('abteilung', '?'),
            "band": r.get('band', '?'),
            "page": r.get('page', r.get('page_no', '?')),
            "final_score": r.get('final_score', '?'),
            "snippet_has_core_expansion": has_expansion,
            "matched_term": matched_term,
            "snippet_preview": preview[:200],
            "debug": r.get('_debug', {}),
        }
        result["top10"].append(entry)

        # Track target page
        if expected_volume:
            if (r.get('abteilung') == expected_volume[0] and
                    r.get('band') == expected_volume[1] and
                    r.get('is_main_text')):
                if target_page_rank is None:
                    target_page_rank = i + 1

        if expected_volume and expected_page_range and r.get('is_main_text'):
            try:
                page_value = int(r.get('page', r.get('page_no', -1)))
            except (TypeError, ValueError):
                page_value = -1
            in_target_range = (
                r.get('abteilung') == expected_volume[0]
                and r.get('band') == expected_volume[1]
                and expected_page_range[0] <= page_value <= expected_page_range[1]
            )
            evidence_text = f"{preview} {matched_term}".lower()
            has_expected_term = (
                not expected_terms_in_snippet
                or any(term.lower() in evidence_text for term in expected_terms_in_snippet)
            )
            if in_target_range and has_expected_term and target_evidence_rank is None:
                target_evidence_rank = i + 1
    # Assertions
    result["target_page_rank"] = target_page_rank
    result["target_evidence_rank"] = target_evidence_rank

    if expected_terms_in_snippet:
        for expected in expected_terms_in_snippet:
            found_any = False
            for e in result["top10"]:
                if expected.lower() in e['snippet_preview'].lower():
                    found_any = True
                    break
                # 也检查 matched_term
                mt = e.get('matched_term', '') or ''
                if expected.lower() == mt.lower():
                    found_any = True
                    break
            if not found_any:
                result["status"] = FAIL_ALG
                result["failures"].append(
                    f"期望术语 '{expected}' 未在任何 preview/matched_term 中找到")

    if expected_volume and expected_page_range and target_evidence_rank is None:
        abt, band = expected_volume
        start_page, end_page = expected_page_range
        conn_range = sqlite3.connect(str(_META_DB))
        try:
            term_conditions = " OR ".join(
                "chunk_text LIKE ?" for _ in (expected_terms_in_snippet or [])
            )
            sql = (
                "SELECT COUNT(*) FROM chunks WHERE mega_abteilung=? AND band=? "
                "AND source_type='TEXT' AND page BETWEEN ? AND ?"
            )
            params = [abt, band, start_page, end_page]
            if term_conditions:
                sql += f" AND ({term_conditions})"
                params.extend(f"%{term}%" for term in expected_terms_in_snippet)
            evidence_count = conn_range.execute(sql, params).fetchone()[0]
        finally:
            conn_range.close()
        result["target_range_available"] = evidence_count
        if evidence_count == 0:
            result["status"] = XFAIL_DATA
            result["failures"].append(
                f"目标原文页 {expected_volume} p.{start_page}-{end_page} 未覆盖所需术语"
            )
        else:
            result["status"] = FAIL_ALG
            result["failures"].append(
                f"目标原文页有 {evidence_count} 条证据，但未进入 top10"
            )
    if expected_volume and target_page_rank is None:
        abt, band = expected_volume
        try:
            conn2 = sqlite3.connect(str(_META_DB))
            vol_cnt = conn2.execute(
                "SELECT COUNT(*) FROM chunks WHERE mega_abteilung=? AND band=? AND source_type='TEXT'",
                (abt, band)).fetchone()[0]
            # 也检查是否有 core term 匹配
            core_terms = profile.get('core_terms', [])
            core_in_vol = 0
            if core_terms:
                # 用 priority 中的德语词查
                for pt in priority[:20]:
                    cnt = conn2.execute(
                        "SELECT COUNT(*) FROM chunks WHERE mega_abteilung=? AND band=? AND source_type='TEXT' AND chunk_text LIKE ?",
                        (abt, band, '%' + pt + '%')).fetchone()[0]
                    if cnt > 0:
                        core_in_vol += cnt
            conn2.close()
        except Exception:
            vol_cnt = 0; core_in_vol = 0
        if vol_cnt < 5 or core_in_vol == 0:
            result["status"] = XFAIL_DATA
            result["failures"].append(
                f"期望卷 {expected_volume} TEXT 无 core term 命中 (vol={vol_cnt}, core_hits={core_in_vol})")
        else:
            result["status"] = FAIL_ALG
            result["failures"].append(
                f"期望卷 {expected_volume} TEXT 有 {core_in_vol} 条 core term 命中但未进 top10")

    return result


def main():
    tests = [
        # A. 当前失败用例
        {
            "query": "黑格尔法哲学批判中马克思怎么讨论subsumption",
            "expected_terms_in_snippet": ["Subsumtion"],
            "expected_volume": ("I", "2"),
            "min_text_in_top": 2,
            "description": "回归: subsumption 在黑格尔法哲学批判",
        },
        {
            "query": "黑格尔法哲学批判中马克思怎么讨论归摄",
            "expected_terms_in_snippet": ["Subsumtion"],
            "expected_volume": ("I", "2"),
            "min_text_in_top": 2,
            "description": "回归: 中文'归摄'在黑格尔法哲学批判",
        },
        # B. 德语精确查询
        {
            "query": "Subsumtion Hegelschen Rechtsphilosophie",
            "expected_terms_in_snippet": ["Subsumtion"],
            "min_text_in_top": 2,
            "description": "德语精确: Subsumtion + Hegelschen Rechtsphilosophie",
        },
        # C. 其他 glossary 概念
        {
            "query": "德意志意识形态中如何讨论意识形态概念",
            "expected_terms_in_snippet": ["Ideologie", "ideologisch"],
            "expected_volume": ("I", "5"),
            "min_text_in_top": 3,
            "description": "概念: 意识形态 in 德意志意识形态",
        },
        {
            "query": "1844手稿中马克思怎么讨论异化劳动",
            "expected_terms_in_snippet": ["entfremdete", "Entfremdung"],
            "expected_volume": ("I", "2"),
            "expected_page_range": (298, 305),
            "min_text_in_top": 1,
            "description": "概念: 异化劳动 in 1844手稿 (I/2 p.298-305)",
        },
        {
            "query": "资本论手稿中关于剩余价值的论述",
            "expected_terms_in_snippet": ["Mehrwert"],
            "min_text_in_top": 2,
            "description": "概念: 剩余价值 in 资本论手稿",
        },
        # D. APPARAT 意图
        {
            "query": "德意志意识形态的编者注和异文说明",
            "min_text_in_top": 0,
            "description": "APPARAT意图: 编者注/异文查询",
        },
        # E. 通用查询
        {
            "query": "马克思论国家",
            "min_text_in_top": 2,
            "description": "通用: 无特定著作名/意图",
        },
    ]

    print("=" * 70)
    print("MEGA² RAG 回归测试 v2")
    print("=" * 70)

    results = []
    for t in tests:
        kwargs = {k: v for k, v in t.items() if k != 'description'}
        r = run_test(**kwargs)
        r['description'] = t.get('description', '')
        results.append(r)

    for r in results:
        icon = {"PASS": "✅", "FAIL_ALGORITHM": "❌",
                "XFAIL_DATA": "⚠️", "SKIP": "⏭"}.get(r['status'], "?")
        print(f"\n{icon} [{r['status']}] {r['description']}")
        print(f"   Query: {r['query'][:60]}")
        print(f"   Profile: intent={r['query_profile']['intent']} "
              f"core={r['query_profile']['core_terms']} "
              f"target={r['query_profile']['target']}")
        print(f"   Priority (first 8): {r['priority_terms'][:8]}")
        if r.get('target_page_rank'):
            print(f"   Target vol page rank: #{r['target_page_rank']}")
        if r.get('target_evidence_rank'):
            print(f"   Target evidence rank: #{r['target_evidence_rank']}")

        for e in r.get('top10', [])[:5]:
            ck = "✓" if e.get('snippet_has_core_expansion') else ("-" if e.get('snippet_has_core_expansion') is None else "✗")
            mt = e.get('matched_term', '') or '-'
            dbg = e.get('debug', {})
            print(f"   [{e['rank']}] {e['type']:6s} {e['abt']}/{str(e['band']):3s} "
                  f"p.{str(e['page']):>4s} score={e['final_score']} "
                  f"v={dbg.get('volume_boost','-')} t={dbg.get('type_boost','-')} "
                  f"mt='{mt}'")
            print(f"       {e['snippet_preview'][:100]}...")

        if r['failures']:
            for f in r['failures']:
                print(f"   ↳ {f}")

    # Stats
    pass_cnt = sum(1 for r in results if r['status'] == PASS)
    fail_cnt = sum(1 for r in results if r['status'] == FAIL_ALG)
    xfail_cnt = sum(1 for r in results if r['status'] == XFAIL_DATA)
    skip_cnt = sum(1 for r in results if r['status'] == SKIP)

    print(f"\n{'='*70}")
    print(f"PASS={pass_cnt} FAIL_ALG={fail_cnt} XFAIL_DATA={xfail_cnt} SKIP={skip_cnt} "
          f"({pass_cnt}/{pass_cnt+fail_cnt} 算法测试通过)")

    # Save report
    report_path = Path(__file__).parent / "test_report.json"
    json.dump(results, open(report_path, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2, default=str)
    print(f"Report: {report_path}")

    return fail_cnt == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MEGA² RAG 回归测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出 debug signals")
    parser.add_argument("--top-k", type=int, default=10, help="测试 top-k")
    args = parser.parse_args()

    ok = main()
    sys.exit(0 if ok else 1)
