#!/usr/bin/env python3
"""
MEGA² RAG — 混合检索 CLI
用法:
  python search_cli.py "Entfremdung"              BM25 关键词检索
  python search_cli.py "剩余价值" --semantic        BM25 + 向量混合检索
  python search_cli.py "Mehrwert" --evidence       输出证据卡片格式
"""
import sys, os, yaml, argparse, sqlite3, json
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

META_DB = config['paths']['metadata_db']
OLLAMA_HOST = config['ollama']['base_url']
EMB_MODEL = config['ollama']['embedding_model']

from cache import make_cache_key, cache_get, cache_put, cache_status
from index_version import get_current_version
from rerank import rerank, MODE_WEIGHTS
from glossary_loader import expand_with_glossary, load_glossary

_GLOSSARY = load_glossary()

def fts5_or(query: str) -> str:
    """多词用 OR 连接，过滤非拉丁字符（中文等）避免 FTS5 报错"""
    import re
    words = [w for w in query.split() if len(w) > 1 and re.match(r'^[a-zA-ZäöüßÄÖÜẞ]+$', w)]
    if not words:
        return query  # 无有效德语词时返回原词（让 FTS5 自己处理）
    return " OR ".join(words) if len(words) > 1 else words[0]

# ============================================================
# BM25 全文检索
# ============================================================
def search_bm25(query, top_k=30):
    conn = sqlite3.connect(str(META_DB))
    fts_q = fts5_or(query)
    results = []
    for row in conn.execute("""
        SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
               c.page, c.is_main_text, c.chunk_text, rank
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.rowid
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (fts_q, top_k)):
        results.append({
            "id": row[0], "source_path": row[1], "abteilung": row[2],
            "band": row[3], "type": row[4], "page": row[5],
            "is_main_text": bool(row[6]), "text": row[7],
        })
    conn.close()
    return results

# ============================================================
# 向量检索
# ============================================================
def search_vector(query, top_k=30):
    from ollama import Client
    import lancedb

    ollama = Client(host=OLLAMA_HOST)
    resp = ollama.embed(model=EMB_MODEL, input=query)
    query_vec = resp['embeddings'][0]

    db = lancedb.connect(str(config['paths']['vector_db']))
    try:
        tbl = db.open_table("chunks")
        results = tbl.search(query_vec).limit(top_k).to_list()

        conn = sqlite3.connect(str(META_DB))
        enriched = []
        ids = [r['id'] for r in results]
        placeholders = ','.join(['?'] * len(ids))
        for row in conn.execute(
            f"SELECT id, source_path, mega_abteilung, band, source_type, page, is_main_text, chunk_text FROM chunks WHERE id IN ({placeholders})",
            ids
        ):
            enriched.append({
                "id": row[0], "source_path": row[1], "abteilung": row[2],
                "band": row[3], "type": row[4], "page": row[5],
                "is_main_text": bool(row[6]), "text": row[7],
            })
        conn.close()

        # 保持 LanceDB 排序
        id_order = {r['id']: i for i, r in enumerate(results)}
        enriched.sort(key=lambda x: id_order.get(x['id'], 999))
        return enriched
    except Exception as e:
        print(f"向量检索失败: {e}")
        return []

# ============================================================
# 混合检索 (RRF 融合)
# ============================================================
def search_hybrid(query, top_k=20, bm25_k=30, vec_k=30, rrf_k=60):
    bm25_results = search_bm25(query, bm25_k)
    vec_results = search_vector(query, vec_k)

    scores = {}
    for rank, r in enumerate(bm25_results):
        scores[r['id']] = scores.get(r['id'], 0) + 1 / (rrf_k + rank + 1)
        scores[f"_{r['id']}_data"] = r

    for rank, r in enumerate(vec_results):
        scores[r['id']] = scores.get(r['id'], 0) + 1 / (rrf_k + rank + 1)
        if f"_{r['id']}_data" not in scores:
            scores[f"_{r['id']}_data"] = r

    merged = []
    for key, score in scores.items():
        if key.startswith("_"):
            continue
        data = scores.get(f"_{key}_data", {})
        data['_score'] = score
        merged.append(data)

    merged.sort(key=lambda x: x['_score'], reverse=True)
    return merged[:top_k]

# ============================================================
# 证据卡片格式化
# ============================================================
def format_evidence(results, query):
    lines = [f"查询: {query}", "=" * 60]
    for i, r in enumerate(results[:10]):
        abt = {"I": "ERSTE", "II": "ZWEITE", "III": "DRITTE", "IV": "VIERTE"}.get(r['abteilung'], r['abteilung'])
        src = f"MEGA {r['abteilung']}/{r['band']}, {r['type']}, S. {r['page']}"
        layer = "正文" if r['is_main_text'] else "编者说明/考证"
        author = "Marx/Engels" if r['is_main_text'] else "MEGA 编者"

        lines.append(f"\n[证据 {i+1}]")
        lines.append(f"来源: {src}")
        lines.append(f"层级: {layer}")
        lines.append(f"作者: {author}")
        lines.append(f"德语原文: {r['text'][:500]}")
        if not r['is_main_text']:
            lines.append(f"可靠性: 编者考证，非马克思/恩格斯原文")
        else:
            lines.append(f"可靠性: {author} 正文")
        lines.append("-" * 40)

    return "\n".join(lines)

# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEGA² RAG 检索")
    parser.add_argument("query", type=str, help="检索词（中文或德语）")
    parser.add_argument("--semantic", action="store_true", help="混合检索（BM25+向量）")
    parser.add_argument("--evidence", action="store_true", help="证据卡片格式输出")
    parser.add_argument("--top", type=int, default=10, help="返回条数")
    parser.add_argument("--mode", type=str, default="balanced",
                        choices=["original_first", "apparat_first", "balanced", "philology"],
                        help="检索模式")
    parser.add_argument("--rerank", type=str, default="rule",
                        choices=["none", "rule", "bge"], help="重排方法")
    parser.add_argument("--no-cache", action="store_true", help="跳过缓存")
    parser.add_argument("--clear-cache", action="store_true", help="清空缓存")
    parser.add_argument("--cache-status", action="store_true", help="缓存状态")
    args = parser.parse_args()

    if args.clear_cache:
        from cache import clear_cache
        clear_cache()
        print("缓存已清空")
        sys.exit(0)

    if args.cache_status:
        print(cache_status())
        sys.exit(0)

    # 查询扩展
    expanded, matched_terms, hints = expand_with_glossary(args.query, _GLOSSARY)
    search_query = expanded if expanded != args.query else args.query

    # 缓存检查
    idx_ver = get_current_version()
    ck = make_cache_key(search_query, f"mode={args.mode}", args.top, args.rerank, idx_ver)
    if not args.no_cache:
        cached = cache_get(ck)
        if cached:
            print(f"💾 缓存命中!\n{cached}")
            sys.exit(0)

    if args.semantic:
        results = search_hybrid(search_query, top_k=min(args.top * 2, 30))
    else:
        results = search_bm25(search_query, top_k=min(args.top * 2, 30))

    # Metadata boost via reranker
    results = rerank(results, query=search_query, mode=args.mode, method=args.rerank,
                     glossary_terms=[t for t in matched_terms])
    results = results[:args.top]

    if args.evidence:
        output = format_evidence(results, args.query)
        print(output)
    else:
        print(f"查询: '{args.query}' | 模式: {args.mode} | 重排: {args.rerank} | 结果: {len(results)} 条\n")
        for i, r in enumerate(results):
            src = f"MEGA {r['abteilung']}/{r['band']} [{r.get('type', r.get('source_type','?'))}] p.{r.get('page', r.get('page_no','?'))}"
            tag = "[正文]" if r.get('is_main_text') else "[编者]"
            scores = f"final={r.get('final_score', '-')} bm25={r.get('bm25_score','-')}"
            print(f"[{i+1}] {tag} {src} | {scores}")
            print(f"    {r['text'][:250]}")
            print()

    # 写缓存
    if not args.no_cache:
        output_for_cache = format_evidence(results, args.query) if args.evidence else json.dumps(
            [{"src": f"MEGA {r['abteilung']}/{r['band']} p.{r.get('page','?')}", "text": r['text'][:200]} for r in results],
            ensure_ascii=False)
        cache_put(ck, "search_results", output_for_cache, idx_ver)
