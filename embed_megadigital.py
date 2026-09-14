#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 megadigital chunks 批量生成 bge-m3 向量并写入 vectors.lancedb。

用法（使用本项目安装环境）:
  .venv/Scripts/python.exe embed_megadigital.py
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import lancedb
from ollama import Client

from portable_paths import configured_path

META_DB = configured_path("metadata_db", "data/metadata.db")
VEC_DB = configured_path("vector_db", "data/vectors.lancedb")
STATE_FILE = configured_path("embedding_state", "data/.embed_megadigital_state.json")
BATCH = 32  # 长文本大 batch 会卡死 Ollama runner；32 稳妥
MAX_CHARS = 6000  # bge-m3 8192 token 安全上限内

OLLAMA = Client(host="http://127.0.0.1:11434", timeout=600)
MODEL = "bge-m3"


def load_existing_ids() -> set[str]:
    db = lancedb.connect(str(VEC_DB))
    tbl = db.open_table("chunks")
    df = tbl.to_pandas()
    return set(df["id"].tolist())


def load_rows(existing: set[str]):
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT id, chunk_text, source_path, content_hash FROM chunks "
        "WHERE source_collection='megadigital' ORDER BY rowid"
    ).fetchall()
    conn.close()
    out = []
    for rid, text, spath, chash in rows:
        if rid in existing:
            continue
        text = (text or "")[:MAX_CHARS]
        if not text.strip():
            continue
        out.append({"id": rid, "text": text, "source_path": spath, "content_hash": chash or ""})
    return out


def main() -> int:
    existing = load_existing_ids()
    print(f"lancedb 已有 {len(existing)} 条")
    rows = load_rows(existing)
    print(f"待嵌入 {len(rows)} 条")
    if not rows:
        print("无待嵌入数据")
        return 0

    db = lancedb.connect(str(VEC_DB))
    tbl = db.open_table("chunks")

    state: dict = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    done = set(state.get("done", []))
    pending = [r for r in rows if r["id"] not in done]
    print(f"续跑跳过 {len(rows) - len(pending)} 条")

    lock: object = None  # 单线程，无需锁

    stats = {"ok": 0, "err": 0, "vec": 0}
    t0 = time.time()

    def save_state() -> None:
        json.dump({"done": sorted(done)}, STATE_FILE.open("w", encoding="utf-8"))

    # 按长度排序分组：同批内长度相近，避免最长文本拖慢整批
    pending.sort(key=lambda r: len(r["text"]))
    batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]

    for bi, batch in enumerate(batches):
        texts = [r["text"] for r in batch]
        ids = [r["id"] for r in batch]
        paths = [r["source_path"] for r in batch]
        try:
            resp = OLLAMA.embed(model=MODEL, input=texts)
            embs = resp["embeddings"]
            if len(embs) != len(batch):
                raise RuntimeError(f"embed 数量不符: {len(embs)} != {len(batch)}")
            tbl.add([{"id": ids[i], "vector": embs[i], "source_path": paths[i]} for i in range(len(batch))])
            done.update(ids)
            stats["ok"] += len(batch)
            stats["vec"] += len(batch)
        except Exception as exc:  # noqa: BLE001
            stats["err"] += 1
            print(f"  BATCH {bi} ERR: {exc}", flush=True)
            # 错误批逐条重试（避免整批丢弃）
            for r in batch:
                try:
                    emb = OLLAMA.embed(model=MODEL, input=[r["text"]])["embeddings"][0]
                    tbl.add([{"id": r["id"], "vector": emb, "source_path": r["source_path"]}])
                    done.add(r["id"])
                    stats["vec"] += 1
                except Exception as exc2:  # noqa: BLE001
                    print(f"  FAIL {r['id']}: {exc2}", flush=True)
        if (bi + 1) % 5 == 0:
            save_state()
            el = time.time() - t0
            rate = stats["ok"] / el
            eta = (len(pending) - stats["ok"]) / rate / 60
            print(f"  {stats['ok']}/{len(pending)} {rate:.2f}条/s ETA {eta:.0f}min", flush=True)
    save_state()
    el = time.time() - t0
    print(f"完成: ok={stats['ok']} err={stats['err']} 耗时 {el/60:.1f}min")

    # 更新 index_progress，标记 megadigital 向量完成
    conn = sqlite3.connect(str(META_DB))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for r in rows:
        if r["id"] in done:
            conn.execute(
                "INSERT OR REPLACE INTO index_progress"
                "(source_path, indexed_at, status, reason, content_hash, embedding_status)"
                " VALUES (?, ?, 'indexed', '', ?, 'done')",
                (r["source_path"], now, r["content_hash"]),
            )
            n += 1
    conn.commit()
    conn.close()
    print(f"index_progress 更新 {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
