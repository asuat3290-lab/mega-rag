#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MEGAdigital (megadigital.bbaw.de) crawler — III 书信 + IV 摘录卷.

两个阶段:
  Phase A: collect  — 从 index 页面收集条目清单 (id, 卷, 标题, 日期, 类型)
  Phase B: fetch    — 下载官方 TEI-XML 并解析为 import_megadigital.py 兼容 JSONL

用法:
  python crawler_megadigital.py collect
  python crawler_megadigital.py fetch            # 断点续爬
  python crawler_megadigital.py fetch --limit 5  # 试跑

输出:
  <out_dir>/manifest_exzerpte.json   IV 部条目清单
  <out_dir>/manifest_briefe.json     III 部书信清单 (含日期)
  <out_dir>/chunks_exzerpte.jsonl    IV 部逐页文本
  <out_dir>/chunks_briefe.jsonl      III 部逐封文本
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import ssl
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = "https://megadigital.bbaw.de"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
DELAY = 0.4          # 请求间隔（秒），尊重服务器
RETRIES = 4

OUT_DIR = Path(r"D:\research-archives\MEGA_V3_论文研究工作台\02_语料库\MEGAdigital网页语料")
PROGRESS = OUT_DIR / ".megadigital_fetch_state.json"

# 书信年份 → MEGA 卷号（官方 mega.bbaw.de 卷次表）
# III/14: 1866-01 .. 1867-12; III/15: 1868-01 .. 1869-02; III/16: 1869-03 .. 1870-05
# III/17: 1870-06 .. 1871-06; III/18: 1871-07 .. 1871-11; III/19: 1871-12 ..
LETTER_VOLUME_BOUNDS = [
    ((1866, 1),  "III/14"),
    ((1868, 1),  "III/15"),
    ((1869, 3),  "III/16"),
    ((1870, 6),  "III/17"),
    ((1871, 7),  "III/18"),
    ((1871, 12), "III/19"),
]

MONTHS_DE = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}

_CTX = ssl.create_default_context()


def http_get(url: str, timeout: int = 90) -> bytes:
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 2 ** attempt
            print(f"  [retry {attempt+1}/{RETRIES}] {url} -> {exc} (wait {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {RETRIES} retries: {url}: {last}")


def html_text(url: str) -> str:
    return http_get(url).decode("utf-8", "replace")


# ---------------------------------------------------------------- Phase A

def collect_exzerpte() -> list[dict]:
    """解析 exzerpte/index.xql：卷 → 条目。"""
    html = html_text(f"{BASE}/exzerpte/index.xql")
    out: list[dict] = []
    # 每个卷 section: <h2 id="IV-19">...</h2> ... 到下一个 <h2 id="IV-..">
    vols = list(re.finditer(r'<h2 class="list_excerpts" id="(IV-\d+)">(.*?)</h2>', html, re.S))
    for idx, m in enumerate(vols):
        vol_key, _title = m.group(1), m.group(2)
        end = vols[idx + 1].start() if idx + 1 < len(vols) else len(html)
        seg = html[m.start():end]
        # 该卷下的条目块: <a href="../exzerpte/detail.xql?id=XXX" class="header"> 区域
        items = list(re.finditer(r'<a href="\.\./exzerpte/detail\.xql\?id=([A-Z0-9]+)"[^>]*class="header"[^>]*>(.*?)</a>\s*<div class="toc_heading"><a[^>]*>\s*<h2>(.*?)</h2>', seg, re.S))
        if not items:
            # 回退：直接找所有 detail 链接
            seen: set[str] = set()
            for link in re.finditer(r'<a href="\.\./exzerpte/detail\.xql\?id=([A-Z0-9]+)"', seg):
                iid = link.group(1)
                if iid not in seen:
                    seen.add(iid)
                    out.append({"id": iid, "volume": vol_key.replace("IV-", "IV/"), "title": "", "type": "excerpt"})
            continue
        for it in items:
            iid = it.group(1)
            title = " ".join(re.sub(r"<[^>]+>", " ", it.group(3)).split())
            out.append({"id": iid, "volume": vol_key.replace("IV-", "IV/"), "title": title, "type": "excerpt"})
    # 去重（某些 id 可能出现在多卷 section 边缘）
    seen: dict[str, dict] = {}
    for item in out:
        seen.setdefault(item["id"], item)
    return list(seen.values())


def parse_list_date(s: str) -> tuple[int, int] | None:
    """'15.01.1866' / '01.01.1865–' / '09.01.1866– 15.01.1866' → (年,月)。取区间起始。"""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m:
        return int(m.group(3)), int(m.group(2))
    return None


def collect_briefe() -> list[dict]:
    """解析 briefe/index.xql 主页（全部 2927 封，含 1865 年底）→ id + 日期 + 标题。"""
    out: list[dict] = []
    seen: set[str] = set()
    offset = 1
    while True:
        url = f"{BASE}/briefe/index.xql?offset={offset}"
        html = html_text(url)
        rows = re.findall(
            r'<tr class="clickable-row[^"]*" data-href="[^"]*detail\.xql\?id=([A-Z0-9]+)"[^>]*>\s*'
            r'<td class="date">([^<]*)</td>\s*<td><a href="[^"]*">([^<]{1,300})</a></td>',
            html,
        )
        for iid, date_txt, title in rows:
            if iid in seen:
                continue
            seen.add(iid)
            d = parse_list_date(date_txt)
            year = d[0] if d else 0
            out.append({
                "id": iid,
                "year": year,
                "date": f"{d[0]}-{d[1]:02d}" if d else "",
                "date_range": date_txt,
                "title": " ".join(title.split()),
                "type": "letter",
            })
        # 翻页（主页链接形如 ?&amp;offset=41）
        offsets = [int(x) for x in re.findall(r"offset=(\d+)", html)]
        bigger = [o for o in offsets if o > offset]
        if not bigger:
            break
        offset = min(bigger)
        time.sleep(DELAY)
    out.sort(key=lambda x: (x["year"], x["id"]))
    return out


def fetch_list_dates(items: list[dict]) -> None:
    """从列表页把每封信的日期区间补进 items（标题解析失败的兜底）。"""
    for year in range(1866, 1872):
        offset = 1
        while True:
            url = f"{BASE}/briefe/index.xql?jahr={year}&offset={offset}"
            html = html_text(url)
            # 行结构: <tr><td>日期</td><td>通信人</td><td><a href=detail>...</a></td>
            blocks = re.split(r"<tr>", html)
            for blk in blocks:
                m = re.search(r'detail\.xql\?id=([A-Z0-9]+)', blk)
                if not m:
                    continue
                iid = m.group(1)
                tds = re.findall(r"<td[^>]*>(.*?)</td>", blk, re.S)
                date_txt = " ".join(re.sub(r"<[^>]+>", " ", tds[0]).split()) if tds else ""
                for item in items:
                    if item["id"] == iid:
                        item["date_range"] = date_txt
                        d = parse_list_date(date_txt)
                        if d:
                            item["date"] = f"{d[0]}-{d[1]:02d}"
                        break
            m_next = re.search(r'\?jahr=%d&amp;offset=(\d+)' % year, html)
            if not m_next:
                break
            nxt = int(m_next.group(1))
            if nxt <= offset:
                break
            offset = nxt
            time.sleep(DELAY)


def volume_for_date(year: int, month: int) -> str:
    best = "III/14"  # 早于 1866-01（如 1865-12）按 III/14 处理
    for (y, m), vol in LETTER_VOLUME_BOUNDS:
        if (year, month) >= (y, m):
            best = vol
        else:
            break
    return best


# ---------------------------------------------------------------- Phase B

TEI = "{http://www.tei-c.org/ns/1.0}"


def parse_date_from_title(title: str) -> tuple[int, int] | None:
    """'…London, Freitag, 20. Januar 1871…' / '…Januar 1871…' → (年, 月)"""
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s*(\d{4})", title)
    if m:
        mon = MONTHS_DE.get(m.group(2).lower())
        if mon:
            return int(m.group(3)), mon
    m = re.search(r"([A-Za-zäöüÄÖÜ]+)\.\s*(\d{4})", title)
    if m:
        mon = MONTHS_DE.get(m.group(1).lower())
        if mon:
            return int(m.group(2)), mon
    # 数字日期 "20.01.1871" / "01.01.1871–" 兜底
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", title)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(3)), int(m.group(2))
    return None


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_text(el: ET.Element, exclude: set[str] | None = None) -> str:
    """递归提取纯文本。

    - exclude 中的标签整棵子树跳过（note 等编辑注）
    - <lb/> 转为 '\x00' 占位（之后还原为换行）
    - 普通文本中的排版空白原样保留，由 clean_text 统一折叠
    """
    exclude = exclude or set()
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        tag = strip_ns(node.tag) if isinstance(node.tag, str) else ""
        if tag in exclude:
            return
        if tag == "lb":
            parts.append("\x00")
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return "".join(parts)


def clean_text(s: str) -> str:
    """折叠排版空白：\x00 → 换行；其余连续空白 → 单空格；去空行。"""
    s = s.replace("\r", "").replace("\x00", "\n")
    lines = [" ".join(line.split()) for line in s.split("\n")]
    return "\n".join(line for line in lines if line)


def page_num(pb_n: str | None) -> int:
    """'[0]' → 0; '12' → 12; 其他 → 0"""
    n = (pb_n or "").strip().strip("[]")
    try:
        return int(n)
    except ValueError:
        return 0


def parse_excerpt_xml(xml_text: str, meta: dict) -> list[dict]:
    """IV 部摘录：按 <pb> 分页 → 每页一个 chunk。"""
    root = ET.fromstring(xml_text)
    body = root.find(f".//{TEI}body")
    if body is None:
        return []
    chunks: list[dict] = []
    cur_page_label = "[0]"
    cur_parts: list[str] = []
    for el in body.iter():
        tag = strip_ns(el.tag)
        if tag == "pb":
            if cur_parts:
                text = "\n\n".join(cur_parts)
                if text.strip():
                    chunks.append({**meta, "page": page_num(cur_page_label),
                                   "page_label": cur_page_label,
                                   "text": f"|{cur_page_label}|\n{text}"})
            cur_parts = []
            cur_page_label = el.get("n") or "[0]"
            continue
        if tag == "div":
            # 标题 head（结构标题 + 小节标题）
            head = el.find(f"{TEI}head")
            if head is not None:
                t = clean_text(node_text(head))
                if t:
                    cur_parts.append(t)
            continue
        if tag == "p":
            t = clean_text(node_text(el, exclude={"note"}))
            if t:
                cur_parts.append(t)
        elif tag in ("opener", "closer"):
            # opener/closer 内部（dateline/salute/signed）由 node_text 递归包含
            t = clean_text(node_text(el, exclude={"note"}))
            if t:
                cur_parts.append(t)
    if cur_parts:
        text = "\n\n".join(cur_parts)
        if text.strip():
            chunks.append({**meta, "page": page_num(cur_page_label),
                           "page_label": cur_page_label,
                           "text": f"|{cur_page_label}|\n{text}"})
    return chunks


def parse_letter_xml(xml_text: str, meta: dict) -> list[dict]:
    """III 部书信：整封一个 chunk。"""
    root = ET.fromstring(xml_text)
    body = root.find(f".//{TEI}body")
    if body is None:
        return []
    parts: list[str] = []
    for el in body.iter():
        tag = strip_ns(el.tag)
        if tag in ("p", "opener", "closer"):
            # opener/closer 内部（dateline/salute/signed）由 node_text 递归包含
            t = clean_text(node_text(el, exclude={"note"}))
            if t:
                parts.append(t)
    text = "\n\n".join(parts)
    return [{**meta, "page": 0, "page_label": "1",
             "text": f"|1|\n{text}"}] if text else []


def fetch_and_parse(item: dict, xml_cache_dir: Path, kind: str) -> list[dict] | None:
    """下载 TEI-XML 并解析；返回 chunk 列表，失败返回 None。"""
    iid = item["id"]
    xml_path = xml_cache_dir / f"{iid}.xml"
    if not xml_path.exists():
        url = f"{BASE}/{iid}.xml"
        try:
            data = http_get(url)
            xml_path.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {iid}: {exc}", flush=True)
            return None
        time.sleep(DELAY)
    try:
        xml_text = xml_path.read_text(encoding="utf-8", errors="replace")
        meta = {
            "id": iid,
            "volume_id": item["volume_id"],
            "doc": iid,
            "title": item.get("title") or "",
            "frontend_url": f"{BASE}/{iid}",
            "language": "de",
            "record_type": kind,
            "date": item.get("date", ""),
        }
        # 书信无列表日期时：从 XML 标题解析日期补卷号
        if kind == "letter" and not meta["volume_id"]:
            root = ET.fromstring(xml_text)
            title_el = root.find(f".//{TEI}title")
            if title_el is not None:
                d = parse_date_from_title(clean_text(node_text(title_el)))
                if d:
                    meta["date"] = f"{d[0]}-{d[1]:02d}"
                    meta["volume_id"] = "MEGA_" + volume_for_date(*d).replace("/", "_")
        chunks = parse_excerpt_xml(xml_text, meta) if kind == "excerpt" else parse_letter_xml(xml_text, meta)
        seen: dict[str, int] = {}
        for c in chunks:
            # chunk id 必须唯一（import 按 id 哈希生成主键）：条目 id + 原始页标；
            # 同一页标出现多次（XML 分页重复）时加出现序号
            base = f"{iid}_p{c.get('page_label') or c['page']}"
            n = seen.get(base, 0)
            seen[base] = n + 1
            c["id"] = base if n == 0 else f"{base}#{n + 1}"
        return chunks
    except ET.ParseError as exc:
        print(f"  XML PARSE FAIL {iid}: {exc}", flush=True)
        return None


def phase_b(manifest: list[dict], kind: str, limit: int | None = None, workers: int = 8) -> None:
    xml_dir = OUT_DIR / "xml_cache"
    xml_dir.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / (f"chunks_{'exzerpte' if kind == 'excerpt' else 'briefe'}.jsonl")
    state: dict = {}
    if PROGRESS.exists():
        state = json.loads(PROGRESS.read_text(encoding="utf-8"))
    done = set(state.get("done", []))
    failed: dict = state.get("failed", {})

    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    lock = Lock()
    total = len(manifest)
    pending = [it for it in manifest if it["id"] not in done and it["id"] not in failed]
    stats = {"ok": 0, "chunks": 0}
    if limit:
        pending = pending[:limit]

    def save_state() -> None:
        json.dump({"done": sorted(done), "failed": failed},
                  open(PROGRESS, "w", encoding="utf-8"))

    def work(item: dict) -> None:
        nonlocal stats
        iid = item["id"]
        chunks = fetch_and_parse(item, xml_dir, kind)
        with lock:
            if chunks is None:
                failed[iid] = "parse_or_download"
            else:
                done.add(iid)
                stats["ok"] += 1
                stats["chunks"] += len(chunks)
                # 加锁追加写 JSONL（单行 write 原子）
                with out_file.open("a", encoding="utf-8") as fh:
                    for c in chunks:
                        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
            if (stats["ok"] + len(failed)) % 50 == 0:
                save_state()
                print(f"  progress {stats['ok'] + len(failed)}/{total} ok={stats['ok']} "
                      f"failed={len(failed)} chunks={stats['chunks']}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(work, pending):
            pass
    save_state()
    print(f"  DONE {kind}: ok={stats['ok']} failed={len(failed)} chunks={stats['chunks']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["collect", "fetch"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.stage == "collect":
        print("收集 IV 部摘录条目…", flush=True)
        ex = collect_exzerpte()
        for it in ex:
            it["volume_id"] = "MEGA_" + it["volume"].replace("/", "_")
        (OUT_DIR / "manifest_exzerpte.json").write_text(
            json.dumps(ex, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  exzerpte: {len(ex)} 条目")
        for v in sorted({it['volume'] for it in ex}):
            print(f"    {v}: {sum(1 for it in ex if it['volume'] == v)} 条")

        print("收集 III 部书信条目…", flush=True)
        be = collect_briefe()
        # 卷号映射：按列表日期 → MEGA III/x
        for it in be:
            if it.get("date"):
                y, m = (int(x) for x in it["date"].split("-"))
                it["volume_id"] = "MEGA_" + volume_for_date(y, m).replace("/", "_")
            else:
                it["volume_id"] = ""  # fetch 阶段按 XML 标题日期补齐
        (OUT_DIR / "manifest_briefe.json").write_text(
            json.dumps(be, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  briefe: {len(be)} 封")
        print("完成。下一步: python crawler_megadigital.py fetch")
        return 0

    # fetch
    for kind, mfile in [("excerpt", "manifest_exzerpte.json"), ("letter", "manifest_briefe.json")]:
        path = OUT_DIR / mfile
        if not path.exists():
            print(f"缺少 {mfile}，先运行 collect")
            return 1
        manifest = json.loads(path.read_text(encoding="utf-8"))
        # 书信卷号修正（XML 标题日期）
        if kind == "letter":
            fixed = 0
            for it in manifest:
                if not it.get("volume_id") or it["volume_id"].endswith("_14") and it.get("date", "").startswith(("1868", "1869", "1870", "1871")):
                    pass
            # 保持列表日期映射（已足够精确）
        print(f"fetch {kind}: {len(manifest)} 条目", flush=True)
        phase_b(manifest, kind, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
