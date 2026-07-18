#!/usr/bin/env python3
"""
MEGA² RAG — 可视化查询界面 (Gradio)
启动: python webui.py
"""
import sys, os, yaml, json, sqlite3
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding='utf-8'))
GLOSSARY = yaml.safe_load(open(Path(__file__).parent / "glossary.yaml", encoding='utf-8'))
META_DB = CONFIG['paths']['metadata_db']

from cache import make_cache_key, cache_get, cache_put, cache_status, clear_cache
from index_version import get_current_version
from rerank import rerank, MODE_WEIGHTS
from glossary_loader import expand_with_glossary, load_glossary, needs_model_expansion, parse_model_expansion
from ocr_quality import assess_quality
from query_analyzer import analyze_query, build_priority_terms
from snippet_extractor import extract_best_snippet, build_snippet_for_flash, build_snippet_for_display, format_source_label, text_layer_label
from cross_volume_analysis import is_temporal_query, build_temporal_evidence
from passage_index import search_passages
from concept_retrieval import search_core_variants, merge_core_candidates, annotate_concept_groups, apply_concept_group_coverage

_GLOSSARY = load_glossary()


def resolve_api_key(model_config: dict) -> str:
    """Read the configured API key without placing it in config files or logs."""
    env_name = model_config.get("api_key_env", "")
    key = os.environ.get(env_name, "")
    if key:
        return key
    if sys.platform == "win32" and env_name:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry_key:
                value, _ = winreg.QueryValueEx(registry_key, env_name)
                return str(value or "")
        except (FileNotFoundError, OSError):
            pass
    return ""

def expand_query(question: str) -> str:
    """术语表扩展：兼容旧版 webui callers"""
    expanded, _, _ = expand_with_glossary(question, _GLOSSARY)
    return expanded

# ---- 核心函数（从 query.py 精简） ----

def fts5_query(query: str) -> str:
    """多词 FTS5 用 OR 连接，过滤中文防 FTS5 报错"""
    import re
    operators = {'AND', 'OR', 'NOT', 'NEAR'}
    words = [
        w for w in query.split()
        if len(w) > 1
        and w.upper() not in operators
        and re.match(r'^[a-zA-ZäöüßÄÖÜẞ]+$', w)
    ]
    if not words:
        return query
    return " OR ".join(words) if words else query

def _layer_select_sql(conn: sqlite3.Connection, alias: str = "c") -> str:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "text_layer" not in columns:
        return "'unclassified', 0, ''"
    return (
        f"COALESCE({alias}.text_layer, 'unclassified'), "
        f"COALESCE({alias}.text_layer_confidence, 0), "
        f"COALESCE({alias}.text_layer_provenance, '')"
    )

def do_scoped_search(query_terms: list, target_abt: str, target_band: str = None,
                     text_only: bool = True, top_k: int = 20,
                     source_collection: str = None) -> list:
    """Recall candidates within an explicit MEGA scope, optionally by source."""
    conn = sqlite3.connect(str(META_DB))
    layer_columns = _layer_select_sql(conn)

    conditions = ["c.mega_abteilung = ?"]
    params = [target_abt]
    if target_band:
        conditions.append("c.band = ?")
        params.append(target_band)
    if text_only:
        conditions.append("c.is_main_text = 1")
    if source_collection:
        conditions.append("COALESCE(c.source_collection, 'ocr') = ?")
        params.append(source_collection)
    where_base = " AND ".join(conditions)

    # Collect a small bucket for every priority term, then merge round-robin.
    # This prevents a broad first term (for example Kapital) from consuming the
    # whole scoped pool before a narrower concept term is queried.
    usable_terms = []
    seen_terms = set()
    for term in query_terms[:15]:
        normalized = str(term or "").strip().casefold()
        if len(normalized) < 2 or normalized in seen_terms:
            continue
        seen_terms.add(normalized)
        usable_terms.append(str(term).strip())
    term_count = max(len(usable_terms), 1)
    per_term_limit = max(
        2,
        min(6, (top_k + term_count - 1) // term_count + 1),
    )
    term_buckets = []
    source_tag = (
        "scoped_authoritative" if source_collection else "scoped_target_volume"
    )

    for term in usable_terms:
        bucket = []
        try:
            for row in conn.execute(f"""
                SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
                       c.page, c.is_main_text, c.chunk_text, c.char_count,
                       COALESCE(c.source_collection, 'ocr'), COALESCE(c.source_quality, 'ocr'),
                       COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''), COALESCE(c.source_url, ''),
                       {layer_columns}
                FROM chunks c
                WHERE {where_base} AND c.chunk_text LIKE ?
                ORDER BY c.char_count DESC LIMIT ?
            """, tuple(params + ['%' + term + '%', max(6, per_term_limit * 3)])):
                record_id = row[0]
                bucket.append({
                    "id": record_id, "source_path": row[1], "abteilung": row[2],
                    "band": row[3], "type": row[4], "page": row[5],
                    "is_main_text": bool(row[6]), "text": row[7],
                    "source_collection": row[9], "source_quality": row[10],
                    "page_kind": row[11], "page_label": row[12], "source_url": row[13],
                    "text_layer": row[14], "text_layer_confidence": row[15],
                    "text_layer_provenance": row[16],
                    "rank": 99, "_bm25_rank": 99,
                    "_retrieval_source": source_tag, "_retrieval_sources": [source_tag],
                    "_scoped_term": term,
                })
                if len(bucket) >= per_term_limit:
                    break
        except sqlite3.Error:
            continue
        if bucket:
            term_buckets.append(bucket)

    results = []
    seen = set()
    positions = [0] * len(term_buckets)
    while len(results) < top_k:
        made_progress = False
        for index, bucket in enumerate(term_buckets):
            while positions[index] < len(bucket):
                candidate = bucket[positions[index]]
                positions[index] += 1
                record_id = candidate.get("id")
                if record_id in seen:
                    continue
                seen.add(record_id)
                results.append(candidate)
                made_progress = True
                break
            if len(results) >= top_k:
                break
        if not made_progress:
            break

    conn.close()
    return results

def do_search(query: str, route: str = "all", top_k: int = 15, use_passage: bool = True):
    """混合检索，优先 passage 回退 page"""
    import sqlite3, lancedb
    from ollama import Client

    conn = sqlite3.connect(str(META_DB))
    layer_columns = _layer_select_sql(conn)
    fts_q = fts5_query(query)

    # search_passages() independently verifies FTS parity and the dirty flag.
    has_passage = bool(use_passage)

    # Page-level retrieval remains the safe fallback.
    route_filter = ""
    if route == "main_text":
        route_filter = "AND c.is_main_text = 1"
    elif route == "apparat":
        route_filter = "AND c.is_editorial_comment = 1"

    bm25 = {}
    if has_passage:
        try:
            passage_rows = search_passages(
                conn, fts_q, route=route, limit=max(40, top_k * 2)
            )
            bm25 = {row["id"]: row for row in passage_rows}
        except sqlite3.Error:
            bm25 = {}

    if not bm25:
        try:
            for row in conn.execute(f"""
                SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
                       c.page, c.is_main_text, c.chunk_text, rank,
                       COALESCE(c.source_collection, 'ocr'), COALESCE(c.source_quality, 'ocr'),
                       COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''), COALESCE(c.source_url, ''),
                       {layer_columns}
                FROM chunks_fts f JOIN chunks c ON f.rowid = c.rowid
                WHERE chunks_fts MATCH ? {route_filter}
                ORDER BY rank LIMIT 30
            """, (fts_q,)):
                bm25[row[0]] = {
                    "id": row[0], "record_type": "page",
                    "source_path": row[1], "abteilung": row[2],
                    "band": row[3], "type": row[4], "page": row[5],
                    "is_main_text": bool(row[6]), "text": row[7],
                    "rank": row[8], "_bm25_rank": row[8],
                    "source_collection": row[9], "source_quality": row[10],
                    "page_kind": row[11], "page_label": row[12], "source_url": row[13],
                    "text_layer": row[14], "text_layer_confidence": row[15],
                    "text_layer_provenance": row[16],
                }
        except sqlite3.Error:
            bm25 = {}

    vec = {}
    try:
        ollama = Client(host=CONFIG['ollama']['base_url'])
        emb = ollama.embed(model=CONFIG['ollama']['embedding_model'], input=query)
        db = lancedb.connect(str(CONFIG['paths']['vector_db']))
        # 尝试 passage 表，不存在则回退 chunks
        try:
            tbl = db.open_table("chunks")
            for r in tbl.search(emb['embeddings'][0]).limit(30).to_list():
                vec[r['id']] = r
        except:
            pass
    except:
        pass

    # LanceDB stores vector metadata only. Hydrate candidate ids from SQLite so
    # semantic-only results participate in RRF instead of being discarded.
    if vec:
        ids = list(vec)
        placeholders = ",".join("?" for _ in ids)
        hydrated = {}
        try:
            for row in conn.execute(f"""
                SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
                       c.page, c.is_main_text, c.chunk_text,
                       COALESCE(c.source_collection, 'ocr'), COALESCE(c.source_quality, 'ocr'),
                       COALESCE(c.page_kind, 'pdf'), COALESCE(c.page_label, ''), COALESCE(c.source_url, ''),
                       {layer_columns}
                FROM chunks c
                WHERE c.id IN ({placeholders}) {route_filter}
            """, ids):
                hydrated[row[0]] = {
                    "id": row[0], "source_path": row[1], "abteilung": row[2],
                    "band": row[3], "type": row[4], "page": row[5],
                    "is_main_text": bool(row[6]), "text": row[7],
                    "source_collection": row[8], "source_quality": row[9],
                    "page_kind": row[10], "page_label": row[11], "source_url": row[12],
                    "text_layer": row[13], "text_layer_confidence": row[14],
                    "text_layer_provenance": row[15],
                }
            vec = hydrated
        except Exception:
            vec = {}

    # RRF. A page vector reinforces the best lexical passage on the same page.
    scores = {}
    passage_by_page = {}
    for rid, item in bm25.items():
        if item.get("record_type") == "passage":
            passage_by_page.setdefault(item.get("page_id"), rid)
    for rank, rid in enumerate(bm25):
        scores[rid] = scores.get(rid, 0) + 1/(60+rank+1)
    for rank, rid in enumerate(vec):
        target_id = passage_by_page.get(rid, rid)
        scores[target_id] = scores.get(target_id, 0) + 1/(60+rank+1)
        if target_id != rid:
            sources = bm25[target_id].setdefault("_retrieval_sources", ["passage_fts"])
            if "vector_page" not in sources:
                sources.append("vector_page")

    merged = []
    for rid, score in sorted(scores.items(), key=lambda x:x[1], reverse=True)[:top_k]:
        r = bm25.get(rid) or vec.get(rid)
        if isinstance(r, dict) and 'text' in r:
            r['_score'] = round(score, 4)
            # OCR quality
            r['ocr_quality'] = assess_quality(r.get('text', '')).get('quality', 'medium')
            merged.append(r)

    conn.close()
    return merged


def merge_scoped_candidates(results: list, candidates: list) -> int:
    """Merge page-level scoped recall into passage candidates without page duplicates."""
    by_record = {item.get("id"): item for item in results if item.get("id")}
    by_page = {}
    for item in results:
        page_key = item.get("page_id") or item.get("id")
        if page_key:
            by_page.setdefault(page_key, item)

    injected = 0
    for candidate in candidates:
        candidate_id = candidate.get("id")
        existing = by_page.get(candidate_id) or by_record.get(candidate_id)
        if existing is not None:
            sources = existing.setdefault("_retrieval_sources", [])
            source = candidate.get("_retrieval_source", "scoped_target_volume")
            if source not in sources:
                sources.append(source)
            continue
        results.append(candidate)
        if candidate_id:
            by_record[candidate_id] = candidate
            by_page[candidate_id] = candidate
        injected += 1
    return injected


def query_pipeline(question: str, route: str, top_k: int, use_flash: bool, use_pro: bool,
                   retrieval_mode: str = "balanced", rerank_method: str = "rule",
                   use_cache: bool = True, use_timeline: bool = False, progress=print):
    """完整流水线：检索 → flash 证据卡片 → pro 回答"""
    progress("🔍 检索中...")
    idx_ver = get_current_version()
    cache_key = make_cache_key(
        question,
        f"ui-v4|route={route}|mode={retrieval_mode}|rerank={rerank_method}|"
        f"flash={int(use_flash)}|pro={int(use_pro)}|timeline={int(use_timeline)}",
        top_k,
        rerank_method,
        idx_ver,
        prompt_version="ui-v4",
        model=f"{CONFIG['models']['flash']['model']}|{CONFIG['models']['pro']['model']}",
    )
    if use_cache:
        cached = cache_get(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                if isinstance(payload, dict) and {"raw", "flash", "pro"} <= set(payload):
                    progress("  💾 缓存命中")
                    return payload["raw"], payload["flash"], payload["pro"]
            except (TypeError, ValueError):
                pass

    # Step 0: local concept clusters first; Flash only fills uncovered concepts.
    glossary_expanded, matched_terms, hints = expand_with_glossary(question, _GLOSSARY)
    glossary_hit = glossary_expanded != question
    expanded = glossary_expanded if glossary_hit else question
    import re
    incomplete_expansion, uncovered = needs_model_expansion(
        question, matched_terms, _GLOSSARY
    )
    use_flash_expansion = (
        not re.search(r"[A-Za-z\u00C0-\u024F]", question)
        and (not glossary_hit or incomplete_expansion)
    )
    if use_flash_expansion:
        try:
            from openai import OpenAI
            api_key = resolve_api_key(CONFIG['models']['flash'])
            if not api_key:
                raise RuntimeError("API key is not configured")
            client = OpenAI(api_key=api_key, base_url=CONFIG['models']['flash']['base_url'])
            resp = client.chat.completions.create(
                model=CONFIG['models']['flash']['model'],
                messages=[{"role": "user", "content": (
                    "Return JSON only: {\"phrases\": [...], \"terms\": [...], "
                    "\"senses\": [{\"label\": \"...\", \"terms\": [...]}]}. "
                    "Generate concise German MEGA retrieval expressions, inflections, "
                    "historical spellings, and distinct senses. Question: " + question
                )}],
                max_tokens=160,
                temperature=0.2,
            )
            flash_items = parse_model_expansion(resp.choices[0].message.content or "")
            if flash_items:
                expanded = f"{expanded} {' '.join(flash_items)}"
                progress(f"  Flash query expansion used ({len(flash_items)} terms).")
            else:
                progress("  Flash expansion returned no usable German terms; using local terms.")
        except Exception as exc:
            progress(f"  Flash expansion unavailable; using local terms ({str(exc)[:60]}).")
    else:
        progress("  Using glossary or lexical terms; skipped paid query expansion.")

    query_profile = analyze_query(question, expanded, matched_terms, _GLOSSARY)
    priority_terms = build_priority_terms(query_profile, _GLOSSARY)
    progress(f"  🎯 Intent: {query_profile['intent']} | Core: {query_profile['core_terms']} | Target: {query_profile.get('target_abteilung','')}/{query_profile.get('target_band','')}")

    # Step 1: 检索（有明确卷册目标时扩大检索池 + 目标卷内单独召回）
    search_top_k = top_k * 3 if query_profile.get('target_abteilung') else top_k
    results = do_search(expanded, route, max(search_top_k, 30))

    # Exact per-variant recall prevents broad OR terms from consuming the pool.
    variant_route = route
    if route == "all" and query_profile.get("intent") == "author_argument":
        variant_route = "main_text"
    variant_results = search_core_variants(
        META_DB, query_profile, priority_terms, route=variant_route,
        top_k=max(16, min(30, top_k * 2)),
    )
    variant_injected = merge_core_candidates(results, variant_results)
    annotate_concept_groups(results, query_profile)
    if variant_injected:
        progress(
            f"  🧭 核心词形召回注入: {variant_injected} 条 "
            f"({len(variant_results)} exact candidates)"
        )

    # 目标卷定向召回注入
    scoped_injected = 0
    scoped_text_count = 0
    authoritative_injected = 0
    authoritative_text_count = 0
    if query_profile.get('target_abteilung') and query_profile.get('intent') == 'author_argument':
        scoped_terms = [t for t in priority_terms[:10] if len(t) > 2]
        scoped_results = do_scoped_search(
            scoped_terms,
            target_abt=query_profile['target_abteilung'],
            target_band=query_profile.get('target_band'),
            text_only=True,
            top_k=20)
        scoped_text_count = len(scoped_results)
        # Merge by parent page so a passage and its full page are not duplicated.
        scoped_injected = merge_scoped_candidates(results, scoped_results)

        # If the target has authoritative digital text, inject a small parallel
        # candidate set. This prevents OCR-only global retrieval from hiding it.
        authoritative_results = do_scoped_search(
            scoped_terms,
            target_abt=query_profile['target_abteilung'],
            target_band=query_profile.get('target_band'),
            text_only=True,
            top_k=12,
            source_collection='megadigital')
        authoritative_text_count = len(authoritative_results)
        authoritative_injected = merge_scoped_candidates(results, authoritative_results)
        if scoped_injected > 0:
            progress(f"  🎯 目标卷召回注入: {scoped_injected} 条 ({scoped_text_count} scoped)")
        if authoritative_injected > 0:
            progress(f"  📜 权威数字文本注入: {authoritative_injected} 条 ({authoritative_text_count} available)")

    if not results:
        return "❌ 未找到相关段落", "", ""
    progress(f"  📚 检索到 {len(results)} 条结果 (含注入)")

    # Rerank (传入 query_profile 用于 intent/volume 加权)
    results = rerank(results, query=expanded, mode=retrieval_mode, method=rerank_method,
                     glossary_terms=matched_terms, query_profile=query_profile)
    results = apply_concept_group_coverage(results, query_profile)
    results = results[:top_k]
    progress(f"  📊 重排完成 (mode={retrieval_mode}, intent={query_profile['intent']})")

    # 生成展示片段（围绕核心概念）
    build_snippet_for_display(results, priority_terms)

    # 格式化原始结果（含 debug 信号）
    raw_output = ""
    for i, r in enumerate(results):
        tag = "TEXT卷" if r.get('is_main_text') else "APPARAT卷"
        layer = text_layer_label(r)
        src = format_source_label(r)
        ocr_q = r.get('ocr_quality', '?')
        ocr_warn = " ⚠️ OCR质量低" if ocr_q in ('low', 'failed') else ""
        group_labels = [
            group.get("label", "")
            for group in r.get("_matched_concept_groups", [])
            if group.get("label")
        ]
        group_note = f"\n词义组: {'；'.join(group_labels)}" if group_labels else ""
        dbg = r.get('_debug', {})
        scores = (f"final={r.get('final_score','-')} | "
                  f"rrf={dbg.get('rrf_base','-')} | "
                  f"intent={dbg.get('intent_boost','-')} | "
                  f"volume={dbg.get('volume_boost','-')} | "
                  f"type={dbg.get('type_boost','-')} | "
                  f"layer={dbg.get('layer_adjustment','-')}")
        # 优先使用 display_snippet，回退到原始 text 截断
        display_text = r.get('display_snippet', r.get('text', '')[:500])
        raw_output += f"""
### [{i+1}] [{tag}] [文献层级: {layer}] {src}{ocr_warn}{group_note}
{scores}
{display_text}
---
"""

    # Step 2: Flash — 证据卡片
    flash_output = ""
    if use_flash:
        progress("  ⚡ Flash 模型生成证据卡片...")
        flash_output = _call_flash(question, results, priority_terms)
        progress("  ✅ 证据卡片完成")

    # Step 3: 跨卷分析（检测到跨时期问题时）
    temporal_context = ""
    if use_timeline and is_temporal_query(question):
        progress("  📅 检测到跨时期问题，构建时间线...")
        # 扩大检索范围做跨卷聚合
        all_results = do_search(expanded, route, max(top_k * 4, 60))
        timeline = build_temporal_evidence(
            all_results,
            query_profile.get('core_terms', []),
            priority_terms,
            question=question,
        )
        temporal_context = timeline['timeline_text']
        progress(f"  📅 时间线覆盖 {timeline['period_count']} 个时期")

    # Step 4: Pro — 学术分析
    pro_output = ""
    if use_pro:
        progress("  🧠 Pro 模型学术分析...")
        base_context = flash_output if flash_output else raw_output
        if temporal_context:
            base_context = temporal_context + "\n\n---\n以下为详细检索证据:\n" + base_context
        pro_output = _call_pro(question, base_context)
        progress("  ✅ 分析完成")

    payload = {"raw": raw_output, "flash": flash_output, "pro": pro_output}
    if use_cache:
        cache_put(cache_key, "ui_query", payload, idx_ver, "ui-v4")
    return raw_output, flash_output, pro_output


def _call_flash(question, results, priority_terms=None):
    """DeepSeek Chat 生成证据卡片"""
    cfg = CONFIG['models']['flash']
    api_key = resolve_api_key(cfg)
    if not api_key:
        return "⚠ 未设置 DEEPSEEK_API_KEY 环境变量"

    if priority_terms is None:
        priority_terms = []
    items = build_snippet_for_flash(results, priority_terms)

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=cfg['base_url'])
    resp = client.chat.completions.create(
        model=cfg['model'],
        messages=[{
            "role": "user",
            "content": f"""用户问题: {question}

从 MEGA² 检索到的德语文段。请为每条生成证据卡片，格式如下：

[证据 N] 来源: ... 层级: ... 德语原文关键句: ... 中文直译: ... 相关性: ...

层级字段由本地分类器给定，不得自行改写：只有“作者原文（结构化文本）”可自动认定为马克思/恩格斯原文；“Textband 未分类”必须说明尚未自动判定，不能仅因位于 TEXT 卷就称为作者原文。
如材料标有“检索词义组”，必须分别说明各组含义，不得把 Pöbel、Paria、Lumpenproletariat 等不同范畴直接视为同义词。
诚实判断：如不相关，说明"当前索引中未找到直接相关段落"

{chr(10).join(items)}"""
        }],
        max_tokens=2000,
        temperature=0.3
    )
    return resp.choices[0].message.content


def _call_pro(question, evidence):
    """DeepSeek Reasoner 最终学术分析"""
    cfg = CONFIG['models']['pro']
    api_key = resolve_api_key(cfg)

    has_timeline = "跨卷时间线分析" in str(evidence)

    instruction = f"""你是马克思主义文献研究专家。基于以下 MEGA² 证据回答用户问题。

用户问题: {question}

{"如果证据中包含时间线，请综合各时期的变化，构建历时性分析。" if has_timeline else ""}

证据:
{evidence}

要求:
1. 基于证据给出严谨的学术回答
2. 严格沿用证据卡片的文献层级；只有标为“作者原文（结构化文本）”的证据可自动称为马克思/恩格斯原文，Textband 未分类材料必须保留不确定性
3. 引证时注明 MEGA 卷册页码
4. 如果证据不足，诚实说明
5. 用中文回答"""

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=cfg['base_url'])
    resp = client.chat.completions.create(
        model=cfg['model'],
        messages=[{"role": "user", "content": instruction}],
        max_tokens=3000,
        temperature=0.7
    )
    return resp.choices[0].message.content


# ---- Gradio UI ----

def gradio_query(question, route, top_k, use_flash, use_pro, retrieval_mode, rerank_method, use_cache, use_timeline):
    """Gradio 回调"""
    if not question.strip():
        return "", "", "", "请输入问题"

    logs = []
    def log(msg):
        logs.append(msg)

    try:
        raw, flash, pro = query_pipeline(
            question, route, int(top_k), use_flash, use_pro,
            retrieval_mode=retrieval_mode, rerank_method=rerank_method,
            use_cache=use_cache, use_timeline=use_timeline, progress=log)
        status = "\n".join(logs)
        return status, raw, flash or "（未启用 Flash）", pro or "（未启用 Pro）"
    except Exception as e:
        import traceback
        return f"❌ 错误: {e}\n{traceback.format_exc()}", "", "", ""


def gradio_export(question, route, top_k, retrieval_mode, rerank_method):
    """Create an API-free Markdown/JSON evidence package for local research."""
    if not str(question or "").strip():
        return "请输入研究问题", None, None
    try:
        from research_export import export_research_package

        exported = export_research_package(
            question,
            route=route,
            top_k=int(top_k),
            retrieval_mode=retrieval_mode,
            rerank_method=rerank_method,
        )
        package = exported["package"]
        paths = exported["paths"]
        status = (
            f"已生成 {package['summary']['evidence_count']} 条证据，"
            f"约 {package['summary']['rough_total_tokens']} tokens；未调用 DeepSeek API"
        )
        return status, paths.get("markdown"), paths.get("json")
    except Exception as exc:
        return f"导出失败: {type(exc).__name__}: {exc}", None, None


def build_ui():
    import gradio as gr

    with gr.Blocks(title="MEGA² RAG") as app:
        gr.Markdown("""
        # 📚 MEGA² 文献考证型 RAG
        马克思恩格斯全集（MEGA²）本地检索系统 | BM25 + bge-m3 混合检索 | DeepSeek 分层回答
        """)

        with gr.Row():
            with gr.Column(scale=4):
                question = gr.Textbox(
                    label="研究问题",
                    placeholder="例如: 早期马克思如何讨论现实的人？Entfremdung 在1844手稿中的含义？",
                    lines=2)
            with gr.Column(scale=2):
                route = gr.Dropdown(
                    label="文献过滤",
                    choices=[("全部", "all"), ("TEXT 卷（含未分类材料）", "main_text"), ("APPARAT 校勘卷", "apparat")],
                    value="all")
                retrieval_mode = gr.Dropdown(
                    label="检索模式",
                    choices=[("原文优先", "original_first"), ("校勘优先", "apparat_first"),
                             ("平衡", "balanced"), ("版本考证", "philology")],
                    value="balanced")
                rerank_method = gr.Dropdown(
                    label="重排方法",
                    choices=[("规则 (rule)", "rule"), ("无 (none)", "none"), ("BGE (需安装)", "bge")],
                    value="rule")
                top_k = gr.Slider(5, 30, value=15, step=5, label="检索数量")

        with gr.Row():
            use_cache = gr.Checkbox(label="💾 启用缓存", value=True)
            use_flash = gr.Checkbox(label="⚡ Flash (证据卡片)", value=True)
            use_pro = gr.Checkbox(label="🧠 Pro (学术分析)", value=True)
            use_timeline = gr.Checkbox(label="跨卷时间线（较慢）", value=False)
            submit_btn = gr.Button("检索", variant="primary", size="lg")
            export_btn = gr.Button("导出研究包", variant="secondary", size="lg")

        status = gr.Textbox(label="执行状态", lines=3)

        with gr.Row():
            export_status = gr.Textbox(label="研究包状态", interactive=False)
            export_markdown = gr.File(label="Markdown 研究包", interactive=False)
            export_json = gr.File(label="JSON 研究包", interactive=False)

        with gr.Tabs():
            with gr.Tab("📋 证据卡片"):
                evidence_out = gr.Markdown("等待查询...")
            with gr.Tab("📄 原始检索结果"):
                raw_out = gr.Markdown("等待查询...")
            with gr.Tab("🧠 学术分析"):
                pro_out = gr.Markdown("等待查询...")

        submit_btn.click(
            fn=gradio_query,
            inputs=[question, route, top_k, use_flash, use_pro, retrieval_mode, rerank_method, use_cache, use_timeline],
            outputs=[status, raw_out, evidence_out, pro_out]
        )

        export_btn.click(
            fn=gradio_export,
            inputs=[question, route, top_k, retrieval_mode, rerank_method],
            outputs=[export_status, export_markdown, export_json],
        )

    return app


if __name__ == "__main__":
    import gradio as gr
    # 检查 API key
    key = resolve_api_key(CONFIG['models']['flash'])
    if not key:
        print("⚠ DEEPSEEK_API_KEY 未设置！Flash/Pro 模型不可用。")
        print("  设置方法: set DEEPSEEK_API_KEY=你的key")
        print("  检索功能不受影响。")
        print()

    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
