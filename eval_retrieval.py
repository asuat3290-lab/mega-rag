#!/usr/bin/env python3
"""
MEGA² RAG — 检索质量自动评估
用法: python eval_retrieval.py --queries eval_queries.yaml --top-k 10
"""
import sys, os, yaml, json, argparse
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from glossary_loader import expand_with_glossary, load_glossary
from ocr_quality import assess_quality


def compute_recall(results: list, expected_terms: list, k: int) -> float:
    """Recall@k: 前k个结果中包含至少一个期望术语的比例"""
    if not expected_terms:
        return 0.0
    found = 0
    for term in expected_terms:
        for r in results[:k]:
            if term.lower() in r.get('text', '').lower():
                found += 1
                break
    return found / len(expected_terms)


def compute_mrr(results: list, expected_terms: list) -> float:
    """MRR: 第一个命中期望术语的结果的倒数排名"""
    if not expected_terms:
        return 0.0
    for i, r in enumerate(results):
        for term in expected_terms:
            if term.lower() in r.get('text', '').lower():
                return 1.0 / (i + 1)
    return 0.0


def text_hit_rate(results: list) -> float:
    """TEXT 命中率"""
    if not results:
        return 0.0
    return sum(1 for r in results if r.get('is_main_text') or r.get('type') == 'TEXT') / len(results)


def apparat_rate(results: list) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if not (r.get('is_main_text') or r.get('type') == 'TEXT')) / len(results)


def avg_ocr_quality(results: list) -> float:
    """平均 OCR 质量分数 (high=3, medium=2, low=1, failed=0)"""
    if not results:
        return 0.0
    val = {"high": 3, "medium": 2, "low": 1, "failed": 0}
    scores = [val.get(r.get('ocr_quality', 'medium'), 2) for r in results if r.get('text')]
    return sum(scores) / len(scores) if scores else 0.0


def run_eval(queries_yaml: str, top_k: int = 10, output_dir: str = None):
    """运行评估"""
    from search_cli import search_bm25  # 复用现有检索

    queries = yaml.safe_load(open(queries_yaml, encoding='utf-8')).get('queries', [])
    glossary = load_glossary()

    results = []
    for q in queries:
        zh = q['zh']
        expected_de = q.get('de_expected', [])
        pref_abt = q.get('preferred_abteilung', '')
        pref_band = str(q.get('preferred_band', ''))
        pref_type = q.get('preferred_type', '')

        # 术语表扩展
        expanded, matched, hints = expand_with_glossary(zh, glossary)

        # 检索
        search_results = search_bm25(expanded, top_k=top_k)

        # OCR 质量评估
        for r in search_results:
            r['ocr_quality'] = assess_quality(r.get('text', '')).get('quality', 'medium')

        # 计算指标
        r5 = compute_recall(search_results, expected_de, 5)
        r10 = compute_recall(search_results, expected_de, 10)
        mrr = compute_mrr(search_results, expected_de)
        thr = text_hit_rate(search_results)
        apr = apparat_rate(search_results)
        oq = avg_ocr_quality(search_results)

        # 检查是否命中期望的卷
        abt_hits = sum(1 for r in search_results if r.get('abteilung') == pref_abt) if pref_abt else 0
        band_hits = sum(1 for r in search_results if r.get('band') == pref_band) if pref_band else 0
        type_hits = sum(1 for r in search_results if pref_type in (r.get('type') or '')) if pref_type else 0

        result = {
            "query": zh,
            "expanded_query": expanded[:100],
            "matched_glossary": matched,
            "results_count": len(search_results),
            "recall_at_5": round(r5, 3),
            "recall_at_10": round(r10, 3),
            "mrr": round(mrr, 3),
            "text_hit_rate": round(thr, 3),
            "apparat_rate": round(apr, 3),
            "avg_ocr_quality": round(oq, 2),
            "abteilung_hits": abt_hits,
            "band_hits": band_hits,
            "text_type_hits": type_hits,
        }
        results.append(result)
        print(f"{zh:10s} | R@5={r5:.2f} R@10={r10:.2f} MRR={mrr:.2f} | TEXT={thr:.0%} | OCR={oq:.1f}")

    # 总体指标
    avg_r5 = sum(r['recall_at_5'] for r in results) / len(results)
    avg_r10 = sum(r['recall_at_10'] for r in results) / len(results)
    avg_mrr = sum(r['mrr'] for r in results) / len(results)
    avg_thr = sum(r['text_hit_rate'] for r in results) / len(results)

    print(f"\n{'='*60}")
    print(f"总体: R@5={avg_r5:.2f} R@10={avg_r10:.2f} MRR={avg_mrr:.2f} TEXT={avg_thr:.0%}")

    # 保存报告
    if output_dir is None:
        output_dir = SCRIPT_DIR / "eval_reports"
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": timestamp,
        "top_k": top_k,
        "num_queries": len(queries),
        "summary": {
            "avg_recall_at_5": round(avg_r5, 3),
            "avg_recall_at_10": round(avg_r10, 3),
            "avg_mrr": round(avg_mrr, 3),
            "avg_text_hit_rate": round(avg_thr, 3),
        },
        "per_query": results,
    }

    md_path = os.path.join(output_dir, f"eval_{timestamp}.md")
    json_path = os.path.join(output_dir, f"eval_{timestamp}.json")

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Markdown 报告
    md = [f"# MEGA² RAG 检索评估报告",
          f"\n时间: {timestamp}  |  Top-K: {top_k}  |  查询数: {len(queries)}",
          f"\n## 总体指标",
          f"| 指标 | 值 |",
          f"|------|-----|",
          f"| Recall@5 | {avg_r5:.3f} |",
          f"| Recall@10 | {avg_r10:.3f} |",
          f"| MRR | {avg_mrr:.3f} |",
          f"| TEXT Hit Rate | {avg_thr:.1%} |",
          f"\n## 逐查询结果",
          f"| 查询 | R@5 | R@10 | MRR | TEXT% | OCR质量 |",
          f"|------|-----|------|-----|------|---------|"]
    for r in results:
        md.append(f"| {r['query']} | {r['recall_at_5']:.2f} | {r['recall_at_10']:.2f} | {r['mrr']:.2f} | {r['text_hit_rate']:.0%} | {r['avg_ocr_quality']:.1f} |")

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))

    print(f"\n报告已保存: {md_path}")
    print(f"JSON: {json_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEGA² RAG 检索评估")
    parser.add_argument("--queries", type=str, default=str(SCRIPT_DIR / "eval_queries.yaml"),
                        help="评估查询 YAML")
    parser.add_argument("--top-k", type=int, default=10, help="Top-K")
    parser.add_argument("--output-dir", type=str, default=None, help="报告输出目录")
    args = parser.parse_args()

    run_eval(args.queries, args.top_k, args.output_dir)
