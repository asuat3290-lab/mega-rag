#!/usr/bin/env python3
"""
MEGA² RAG — 分层模型查询流水线
用法:
  python query.py "现实的人 德意志意识形态"
  python query.py "Entfremdung in den Frühschriften" --steps  # 逐步查看
"""
import sys, os, yaml, argparse, json
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CONFIG = yaml.safe_load(open(Path(__file__).parent / "config.yaml", encoding='utf-8'))
META_DB = CONFIG['paths']['metadata_db']

# ============================================================
# Step 0: 查询扩展（术语表/词法优先，零费用）
# ============================================================
from glossary_loader import expand_with_glossary, load_glossary
from query_analyzer import analyze_query
from snippet_extractor import text_layer_label

_GLOSSARY = load_glossary()


def rewrite_query(query: str, flash_expand: bool = False) -> dict:
    """Expand known terms locally; optionally use Flash for unknown Chinese terms."""
    expanded, matched_terms, _ = expand_with_glossary(query, _GLOSSARY)
    profile = analyze_query(query, expanded, matched_terms, _GLOSSARY)
    route = "apparat" if profile.get("intent") == "apparat_question" else "all"
    reason = "术语表/词法扩展（零费用）"

    has_latin = any("a" <= ch.lower() <= "z" or "\u00c0" <= ch <= "\u024f" for ch in query)
    if not matched_terms and flash_expand and not has_latin:
        try:
            from openai import OpenAI
            from webui import resolve_api_key
            cfg = CONFIG["models"]["flash"]
            api_key = resolve_api_key(cfg)
            if not api_key:
                raise RuntimeError("API key is not configured")
            client = OpenAI(api_key=api_key, base_url=cfg["base_url"])
            response = client.chat.completions.create(
                model=cfg["model"],
                messages=[{"role": "user", "content": (
                    "Generate German MEGA retrieval terms only, separated by spaces. "
                    "Include variants and historical spellings.\nQuestion: " + query
                )}],
                max_tokens=50,
                temperature=0.2,
            )
            generated = (response.choices[0].message.content or "").strip()
            if generated:
                expanded = f"{query} {generated}"
                reason = "Flash 德语关键词扩展（显式启用）"
        except Exception as exc:
            reason = f"Flash 扩展不可用，已使用原词（{str(exc)[:60]}）"

    return {
        "german_terms": expanded,
        "route": route,
        "reason": reason,
        "profile": profile,
        "matched_terms": matched_terms,
    }
# ============================================================
# Step 1: 混合检索（本地，0 费用）
# ============================================================
def retrieve(german_terms: str, route: str, top_k=15):
    """Use the Web UI's maintained local hybrid retrieval implementation."""
    from webui import do_search
    return do_search(german_terms, route, top_k)
# ============================================================
# Step 2: 证据卡片生成（Flash 模型，便宜）
# ============================================================
def generate_evidence(question: str, passages: list) -> str:
    """用 flash 模型格式化证据卡片 + 德语直译"""
    cfg = CONFIG['models']['flash']

    # 拼装待处理文本
    items = []
    for i, r in enumerate(passages[:8]):
        layer = text_layer_label(r)
        src = f"MEGA {r.get('abteilung','?')}/{r.get('band','?')}, {r.get('type','?')}, S. {r.get('page','?')}"
        items.append(f"[{i+1}] {src} [文献层级: {layer}]\n{r.get('text','')[:600]}")
    raw = "\n\n---\n".join(items)

    prompt = f"""你是马克思主义文献研究助手。用户问题：

"{question}"

以下是从 MEGA² 中检索到的德语文段。请为每条生成证据卡片。

{raw}

请为每条输出：
1. 来源（卷/页）
2. 文献层级（严格沿用输入标签；只有“作者原文（结构化文本）”可自动认定为马克思/恩格斯原文）
3. 德语原文关键句（1-2句）
4. 中文直译
5. 与问题的相关性（一句话）

最后，如果所有段落都不相关，诚实说明"当前索引中未找到直接相关段落"。
用中文回答。"""

    # 调用 Flash 模型
    if cfg['provider'] == 'anthropic':
        return _call_anthropic(cfg, prompt)
    elif cfg['provider'] == 'openai':
        return _call_openai(cfg, prompt)
    else:
        # Fallback: 本地 Ollama
        return _call_ollama(cfg, prompt)

def _call_anthropic(cfg, prompt):
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=cfg['model'],
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text

def _call_openai(cfg, prompt):
    from openai import OpenAI
    from webui import resolve_api_key
    api_key = resolve_api_key(cfg)
    client = OpenAI(api_key=api_key, base_url=cfg['base_url'])
    resp = client.chat.completions.create(
        model=cfg['model'],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )
    return resp.choices[0].message.content

def _call_ollama(cfg, prompt):
    from ollama import Client
    client = Client(host=cfg.get('base_url', 'http://localhost:11434'))
    resp = client.generate(model=cfg['model'], prompt=prompt, stream=False)
    return resp['response']

# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MEGA² RAG 分层查询")
    parser.add_argument("query", type=str, help="查询问题（中文或德语）")
    parser.add_argument("--steps", action="store_true", help="逐步展示每个阶段的结果")
    parser.add_argument("--top", type=int, default=10, help="检索结果数")
    parser.add_argument("--skip-rewrite", action="store_true", help="跳过查询改写（直接用德语查询时）")
    parser.add_argument("--flash-expand", action="store_true", help="未知中文术语时允许 Flash 生成德语检索词")
    parser.add_argument("--no-flash", action="store_true", help="跳过 flash 模型，直接返回原始检索结果")
    args = parser.parse_args()

    print("=" * 70)
    print(f"查询: {args.query}")
    print("=" * 70)

    # Step 0: 查询改写
    t_start = datetime.now()
    if args.skip_rewrite:
        rewrite = {"german_terms": args.query, "route": "all", "reason": "跳过改写"}
        print("\n[Step 0] 查询改写 【跳过】")
    else:
        print("\n[Step 0] 查询扩展 【术语表/词法 — 0 费用】")
        rewrite = rewrite_query(args.query, flash_expand=args.flash_expand)
        print(f"  德语检索词: {rewrite['german_terms']}")
        print(f"  路由: {rewrite['route']}")
        print(f"  理由: {rewrite['reason']}")
    t_rewrite = datetime.now()

    # Step 1: 检索
    print(f"\n[Step 1] 混合检索 【本地 — 0 费用】")
    results = retrieve(rewrite['german_terms'], rewrite['route'], top_k=args.top)
    print(f"  找到 {len(results)} 条结果")
    for i, r in enumerate(results[:5]):
        is_main = f"[{text_layer_label(r)}]"
        src = f"MEGA {r.get('abteilung','?')}/{r.get('band','?')} [{r.get('type','?')}] p.{r.get('page','?')}"
        print(f"  [{i+1}] {is_main} {src}")
        print(f"      {r.get('text','')[:150]}...")
    t_search = datetime.now()

    # Step 2: 证据卡片
    if args.no_flash:
        print(f"\n[Step 2] 证据卡片 【跳过】")
    elif not results:
        print(f"\n[Step 2] 证据卡片 【检索无结果，跳过】")
    else:
        print(f"\n[Step 2] 证据卡片 【Flash 模型 — 少量费用】")
        print(f"  模型: {CONFIG['models']['flash']['model']}")
        try:
            evidence = generate_evidence(args.query, results)
            t_flash = datetime.now()
            print(f"\n{evidence}")
        except Exception as e:
            print(f"  ❌ Flash 调用失败: {e}")
            print(f"  💡 你可能需要设置环境变量 {CONFIG['models']['flash'].get('api_key_env','API_KEY')}")
            t_flash = datetime.now()

    t_flash = locals().get("t_flash", t_search)

    # 耗时统计
    print(f"\n{'─'*60}")
    print(f"⏱ 耗时: 改写 {(t_rewrite-t_start).total_seconds():.1f}s | "
          f"检索 {(t_search-t_rewrite).total_seconds():.1f}s | "
          f"证据卡片 {(t_flash-t_search).total_seconds():.1f}s")
    print(f"💰 费用: 改写=0 | 检索=0 | 证据卡片=flash模型少量API费用")
    print(f"💡 将证据卡片 + 你的学术问题发给 Pro 模型即可获得最终分析")
