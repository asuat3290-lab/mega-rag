#!/usr/bin/env python3
"""
MEGA² RAG — 元数据 + FTS5 全文索引 + 向量索引 构建脚本
增量模式：只索引尚未入库的页面
"""
import sys, os, re, hashlib, yaml, sqlite3, json
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ============================================================
# 配置
# ============================================================
sys.path.insert(0, str(Path(__file__).parent))

CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

OCR_ROOT = Path(config['paths']['ocr_texts'])
META_DB = Path(config['paths']['metadata_db'])
VECTOR_DB = Path(config['paths']['vector_db'])

from ocr_quality import assess_quality
from index_version import compute_index_version, store_version, get_current_version

# ============================================================
# 元数据解析
# ============================================================
def parse_metadata(txt_path: Path):
    """从 OCR 输出路径解析 MEGA 元数据"""
    rel = txt_path.relative_to(OCR_ROOT)
    parts = rel.parts

    abteilung = "UNKNOWN"
    band = "UNKNOWN"
    doc_type = "UNKNOWN"  # TEXT or APPARAT

    for p in parts:
        pu = p.upper()
        if pu.startswith("ERSTE"):
            abteilung = "I"
        elif pu.startswith("ZWEITE"):
            abteilung = "II"
        elif pu.startswith("DRITTE"):
            abteilung = "III"
        elif pu.startswith("VIERTE"):
            abteilung = "IV"

        # Extract Band number
        m = re.search(r'BAND\s*(\d+)', pu)
        if m:
            band = m.group(1)

        if "TEXT" in pu and "APPARAT" not in pu:
            doc_type = "TEXT"
        elif "APPARAT" in pu or "APPARAT" in pu.replace("APParat", "APPARAT"):
            doc_type = "APPARAT"

    # Extract page number
    page_num = 0
    m = re.search(r'page_(\d+)', txt_path.stem)
    if m:
        page_num = int(m.group(1))

    # Read text
    try:
        text = txt_path.read_text(encoding='utf-8').strip()
    except:
        text = ""

    # Generate unique ID from hash
    uid = hashlib.md5(str(rel).encode()).hexdigest()[:12]
    # OCR quality
    q = assess_quality(text)

    return {
        "id": uid,
        "source_path": str(rel),
        "mega_abteilung": abteilung,
        "band": band,
        "source_type": doc_type,
        "page": page_num,
        "is_main_text": doc_type == "TEXT",
        "is_editorial_comment": doc_type == "APPARAT",
        "language": "de",
        "chunk_text": text,
        "char_count": len(text),
        "source_file": parts[-2] if len(parts) >= 2 else "unknown",
        "ocr_quality": q.get('quality', 'medium'),
        "alpha_ratio": q.get('alpha_ratio', 0),
        "german_word_ratio": q.get('german_word_ratio', 0),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_layer": "apparatus" if doc_type == "APPARAT" else "textband_unclassified",
        "text_layer_confidence": 0.99 if doc_type == "APPARAT" else 0.0,
        "text_layer_provenance": (
            "text-layer-v1:source_type=APPARAT" if doc_type == "APPARAT"
            else "text-layer-v1:new_ocr_row_unclassified"
        ),
        "text_layer_version": "text-layer-v1",
    }

# ============================================================
# SQLite 建表
# ============================================================
def init_metadata_db():
    import sqlite3
    conn = sqlite3.connect(str(META_DB))
    conn.execute("PRAGMA journal_mode=WAL")

    # Page-level chunks
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            mega_abteilung TEXT,
            band TEXT,
            source_type TEXT,
            page INTEGER,
            is_main_text INTEGER DEFAULT 0,
            is_editorial_comment INTEGER DEFAULT 0,
            language TEXT DEFAULT 'de',
            chunk_text TEXT,
            char_count INTEGER DEFAULT 0,
            source_file TEXT,
            ocr_quality TEXT DEFAULT 'medium',
            alpha_ratio REAL DEFAULT 0,
            german_word_ratio REAL DEFAULT 0,
            content_hash TEXT,
            text_layer TEXT DEFAULT 'unclassified',
            text_layer_confidence REAL DEFAULT 0,
            text_layer_provenance TEXT DEFAULT '',
            text_layer_version TEXT DEFAULT '',
            indexed_at TEXT
        )
    """)

    # Passage-level chunks
    conn.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            passage_id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            abteilung TEXT,
            band TEXT,
            text_type TEXT,
            page_no INTEGER,
            passage_no INTEGER,
            text TEXT NOT NULL,
            char_start INTEGER,
            char_end INTEGER,
            ocr_quality REAL DEFAULT 0.5,
            source_pdf TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # FTS5
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_text,
            source_file,
            content='chunks',
            content_rowid='rowid'
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
            text,
            content='passages',
            content_rowid='rowid'
        )
    """)

    # Progress
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_progress (
            source_path TEXT PRIMARY KEY,
            indexed_at TEXT,
            status TEXT DEFAULT 'indexed',
            reason TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            embedding_status TEXT DEFAULT 'unknown'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # 迁移旧表：添加 OCR 质量列
    _migrate_schema(conn)

    return conn

def _migrate_schema(conn):
    """Safely migrate old databases and make skipped pages explicit."""
    migrations = [
        ("chunks", "ocr_quality TEXT DEFAULT 'medium'"),
        ("chunks", "alpha_ratio REAL DEFAULT 0"),
        ("chunks", "german_word_ratio REAL DEFAULT 0"),
        ("chunks", "content_hash TEXT"),
        ("chunks", "text_layer TEXT DEFAULT 'unclassified'"),
        ("chunks", "text_layer_confidence REAL DEFAULT 0"),
        ("chunks", "text_layer_provenance TEXT DEFAULT ''"),
        ("chunks", "text_layer_version TEXT DEFAULT ''"),
        ("index_progress", "status TEXT DEFAULT 'indexed'"),
        ("index_progress", "reason TEXT DEFAULT ''"),
        ("index_progress", "content_hash TEXT DEFAULT ''"),
        ("index_progress", "embedding_status TEXT DEFAULT 'unknown'"),
    ]
    for table, col_def in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    conn.execute("""
        UPDATE index_progress
        SET status='skipped', reason='short_or_empty', embedding_status='not_applicable'
        WHERE source_path NOT IN (SELECT source_path FROM chunks)
          AND COALESCE(status, 'indexed') = 'indexed'
    """)
    conn.execute("""
        UPDATE index_progress
        SET status='indexed'
        WHERE source_path IN (SELECT source_path FROM chunks)
          AND (status IS NULL OR status NOT IN ('indexed', 'skipped'))
    """)
    conn.commit()

# ============================================================
# 增量索引
# ============================================================
def _set_index_state(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO index_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, datetime.now().isoformat()),
    )
    conn.commit()


def _get_index_state(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM index_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else ""


def _rebuild_fts(conn) -> None:
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    conn.commit()


def build_index(max_files=None, use_embedding=True):
    from ollama import Client as OllamaClient

    conn = init_metadata_db()
    ollama = OllamaClient(host=config['ollama']['base_url']) if use_embedding else None
    emb_model = config['ollama']['embedding_model']

    all_txt = sorted(OCR_ROOT.rglob("page_*.txt"))
    print(f"总页面文件: {len(all_txt)}")
    indexed = {
        row[0] for row in conn.execute(
            "SELECT source_path FROM index_progress WHERE status IN ('indexed', 'skipped')"
        )
    }
    new_files = [path for path in all_txt if str(path.relative_to(OCR_ROOT)) not in indexed]
    if max_files:
        new_files = new_files[:max_files]
    print(f"已索引: {len(indexed)} | 待索引: {len(new_files)}", flush=True)

    if not new_files:
        if _get_index_state(conn, "chunks_fts_dirty") == "1":
            print("FTS5 状态未完成，正在补建...", flush=True)
            _rebuild_fts(conn)
            _set_index_state(conn, "chunks_fts_dirty", "0")
        else:
            print("✅ 没有新文件需要索引", flush=True)
        conn.close()
        version = store_version("page_index_noop_refresh")
        print(f"索引版本已刷新: {version}", flush=True)
        return

    _set_index_state(conn, "chunks_fts_dirty", "1")
    batch_meta = []
    batch_size = 64
    skipped = 0
    embed_errors = 0
    started_at = datetime.now()

    try:
        for index, txt_path in enumerate(new_files, start=1):
            meta = parse_metadata(txt_path)
            if not meta['chunk_text'] or meta['char_count'] < 10:
                skipped += 1
                conn.execute("""
                    INSERT INTO index_progress
                        (source_path, indexed_at, status, reason, content_hash, embedding_status)
                    VALUES (?, ?, 'skipped', 'short_or_empty', ?, 'not_applicable')
                    ON CONFLICT(source_path) DO UPDATE SET
                        indexed_at=excluded.indexed_at,
                        status='skipped', reason='short_or_empty',
                        content_hash=excluded.content_hash,
                        embedding_status='not_applicable'
                """, (meta['source_path'], datetime.now().isoformat(), meta['content_hash']))
                conn.commit()
                continue

            batch_meta.append(meta)
            if len(batch_meta) >= batch_size:
                embed_errors += _flush_batch(conn, batch_meta, ollama, emb_model, use_embedding)
                elapsed = (datetime.now() - started_at).total_seconds()
                rate = index / max(elapsed, 1) * 60
                remaining = len(new_files) - index
                eta = remaining / max(rate, 0.01)
                print(
                    f"  [{index}/{len(new_files)} ({100 * index // len(new_files)}%)] "
                    f"{rate:.0f} pg/min | ETA {eta / 60:.0f}h{eta % 60:.0f}m | "
                    f"跳过 {skipped} | emb错误 {embed_errors}",
                    flush=True,
                )
                batch_meta = []

        if batch_meta:
            embed_errors += _flush_batch(conn, batch_meta, ollama, emb_model, use_embedding)

        _rebuild_fts(conn)
        _set_index_state(conn, "chunks_fts_dirty", "0")
        if use_embedding:
            _bump_vector_revision(conn)
            conn.commit()
    finally:
        conn.close()

    elapsed = (datetime.now() - started_at).total_seconds()
    print(f"\n✅ 索引完成: {len(new_files)} 页入库 ({elapsed:.0f}s)", flush=True)
    version = store_version("page_index_build")
    print(f"📌 索引版本: {version}", flush=True)


def _flush_batch(conn, batch_meta, ollama, emb_model, use_embedding) -> int:
    """Persist page metadata first, then track embedding completion separately."""
    now = datetime.now().isoformat()
    for meta in batch_meta:
        conn.execute(
            """
            INSERT INTO chunks (id, source_path, mega_abteilung, band,
                source_type, page, is_main_text, is_editorial_comment, language,
                chunk_text, char_count, source_file, ocr_quality, alpha_ratio,
                german_word_ratio, content_hash, text_layer, text_layer_confidence,
                text_layer_provenance, text_layer_version, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_path=excluded.source_path,
                mega_abteilung=excluded.mega_abteilung,
                band=excluded.band,
                source_type=excluded.source_type,
                page=excluded.page,
                is_main_text=excluded.is_main_text,
                is_editorial_comment=excluded.is_editorial_comment,
                language=excluded.language,
                chunk_text=excluded.chunk_text,
                char_count=excluded.char_count,
                source_file=excluded.source_file,
                ocr_quality=excluded.ocr_quality,
                alpha_ratio=excluded.alpha_ratio,
                german_word_ratio=excluded.german_word_ratio,
                content_hash=excluded.content_hash,
                text_layer=CASE
                    WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                        THEN chunks.text_layer
                    WHEN chunks.source_type <> excluded.source_type
                        THEN excluded.text_layer
                    ELSE chunks.text_layer
                END,
                text_layer_confidence=CASE
                    WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                        THEN chunks.text_layer_confidence
                    WHEN chunks.source_type <> excluded.source_type
                        THEN excluded.text_layer_confidence
                    ELSE chunks.text_layer_confidence
                END,
                text_layer_provenance=CASE
                    WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                        THEN chunks.text_layer_provenance
                    WHEN chunks.source_type <> excluded.source_type
                        THEN excluded.text_layer_provenance
                    ELSE chunks.text_layer_provenance
                END,
                text_layer_version=CASE
                    WHEN COALESCE(chunks.text_layer_provenance, '') LIKE 'manual:%'
                        THEN chunks.text_layer_version
                    WHEN chunks.source_type <> excluded.source_type
                        THEN excluded.text_layer_version
                    ELSE chunks.text_layer_version
                END,
                indexed_at=excluded.indexed_at
            """,
            (
                meta['id'], meta['source_path'], meta['mega_abteilung'], meta['band'],
                meta['source_type'], meta['page'], meta['is_main_text'], meta['is_editorial_comment'],
                meta['language'], meta['chunk_text'], meta['char_count'], meta['source_file'],
                meta.get('ocr_quality', 'medium'), meta.get('alpha_ratio', 0),
                meta.get('german_word_ratio', 0), meta['content_hash'],
                meta['text_layer'], meta['text_layer_confidence'],
                meta['text_layer_provenance'], meta['text_layer_version'], now,
            ),
        )
        conn.execute("""
            INSERT INTO index_progress
                (source_path, indexed_at, status, reason, content_hash, embedding_status)
            VALUES (?, ?, 'indexed', '', ?, 'pending')
            ON CONFLICT(source_path) DO UPDATE SET
                indexed_at=excluded.indexed_at,
                status='indexed', reason='', content_hash=excluded.content_hash,
                embedding_status='pending'
        """, (meta['source_path'], now, meta['content_hash']))
    conn.commit()
    if batch_meta:
        _set_index_state(conn, "passages_complete", "0")

    if not use_embedding:
        return 0

    try:
        inputs = [meta['chunk_text'][:2000] for meta in batch_meta]
        response = ollama.embed(model=emb_model, input=inputs)
        embeddings = response['embeddings']
        if len(embeddings) != len(batch_meta):
            raise RuntimeError(f"embedding count mismatch: {len(embeddings)} != {len(batch_meta)}")
        vectors = [
            {"id": meta['id'], "vector": vector, "source_path": meta['source_path']}
            for meta, vector in zip(batch_meta, embeddings)
        ]
        _write_vectors(vectors, replace_existing=True)
    except Exception as exc:
        print(f"  ⚠ embedding batch error: {exc}", flush=True)
        return len(batch_meta)

    conn.executemany(
        "UPDATE index_progress SET embedding_status='done' WHERE source_path=?",
        [(meta['source_path'],) for meta in batch_meta],
    )
    conn.commit()
    return 0


def _write_vectors(vectors, replace_existing: bool = False):
    """Write one vector per id. A failed delete aborts instead of creating duplicates."""
    if not vectors:
        return
    import lancedb
    db = lancedb.connect(str(VECTOR_DB))
    try:
        table = db.open_table("chunks")
    except Exception:
        db.create_table("chunks", vectors)
        return

    if replace_existing:
        ids = ",".join("'" + item['id'].replace("'", "''") + "'" for item in vectors)
        table.delete(f"id IN ({ids})")
    table.add(vectors)


def _bump_vector_revision(conn) -> int:
    """Increment a durable vector-state counter used by index versioning."""
    row = conn.execute(
        "SELECT value FROM index_state WHERE key='vector_revision'"
    ).fetchone()
    revision = int(row[0]) + 1 if row and str(row[0]).isdigit() else 1
    conn.execute(
        """
        INSERT OR REPLACE INTO index_state (key, value, updated_at)
        VALUES ('vector_revision', ?, ?)
        """,
        (str(revision), datetime.now().isoformat()),
    )
    return revision

def vector_status() -> dict:
    """Return exact vector uniqueness and coverage against current OCR chunks."""
    import lancedb
    conn = init_metadata_db()
    ocr_ids = {
        row[0] for row in conn.execute(
            "SELECT id FROM chunks WHERE COALESCE(source_collection, 'ocr')='ocr'"
        )
    }
    digital_ids = {
        row[0] for row in conn.execute(
            "SELECT id FROM chunks WHERE source_collection='megadigital'"
        )
    }
    conn.close()

    try:
        table = lancedb.connect(str(VECTOR_DB)).open_table("chunks")
        vector_list = table.to_arrow()['id'].to_pylist()
    except Exception:
        vector_list = []
    vector_ids = set(vector_list)
    return {
        "rows": len(vector_list),
        "unique": len(vector_ids),
        "duplicates": len(vector_list) - len(vector_ids),
        "ocr_chunks": len(ocr_ids),
        "ocr_covered": len(vector_ids & ocr_ids),
        "ocr_missing": len(ocr_ids - vector_ids),
        "digital_covered": len(vector_ids & digital_ids),
        "stale": len(vector_ids - ocr_ids - digital_ids),
    }


def deduplicate_vectors() -> dict:
    """Rewrite the Lance table with one row per id, preserving the latest row."""
    import lancedb
    import pyarrow as pa

    db = lancedb.connect(str(VECTOR_DB))
    table = db.open_table("chunks")
    arrow = table.to_arrow()
    ids = arrow['id'].to_pylist()
    latest = {}
    for index, record_id in enumerate(ids):
        latest[record_id] = index
    keep = sorted(latest.values())
    if len(keep) != len(ids):
        clean = arrow.take(pa.array(keep, type=pa.int64()))
        db.create_table("chunks", clean, mode="overwrite")

    clean_table = db.open_table("chunks").to_arrow()
    conn = init_metadata_db()
    conn.execute(
        "UPDATE index_progress SET embedding_status='pending' WHERE status='indexed'"
    )
    conn.executemany(
        "UPDATE index_progress SET embedding_status='done' WHERE source_path=?",
        [(path,) for path in clean_table['source_path'].to_pylist()],
    )
    _bump_vector_revision(conn)
    conn.commit()
    conn.close()
    result = vector_status()
    store_version("vector_deduplicate")
    return result


def repair_vectors(max_records=None, batch_size: int = 64) -> dict:
    """Embed current OCR chunks missing from LanceDB without redoing covered pages."""
    import lancedb
    from ollama import Client as OllamaClient

    conn = init_metadata_db()
    try:
        table = lancedb.connect(str(VECTOR_DB)).open_table("chunks")
        arrow = table.to_arrow()
        existing = set(arrow['id'].to_pylist())
        existing_paths = (
            set(arrow['source_path'].to_pylist())
            if 'source_path' in arrow.column_names else set()
        )
    except Exception:
        existing = set()
        existing_paths = set()

    # Reconcile metadata after an interrupted run. LanceDB is the source of
    # truth for completed vectors, so a vector written just before Ctrl+C is
    # not needlessly recomputed on the next invocation.
    if existing_paths:
        conn.executemany(
            "UPDATE index_progress SET embedding_status='done' WHERE source_path=?",
            [(path,) for path in existing_paths],
        )
        conn.commit()

    rows = conn.execute("""
        SELECT id, source_path, chunk_text
        FROM chunks
        WHERE COALESCE(source_collection, 'ocr')='ocr'
        ORDER BY id
    """).fetchall()
    missing = [row for row in rows if row[0] not in existing]
    if max_records:
        missing = missing[:max_records]
    conn.executemany(
        "UPDATE index_progress SET embedding_status='pending' WHERE source_path=?",
        [(row[1],) for row in missing],
    )
    conn.commit()

    client = OllamaClient(host=config['ollama']['base_url'])
    model = config['ollama']['embedding_model']
    completed = 0
    errors = 0
    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        try:
            response = client.embed(model=model, input=[row[2][:2000] for row in batch])
            embeddings = response['embeddings']
            if len(embeddings) != len(batch):
                raise RuntimeError("embedding count mismatch")
            vectors = [
                {"id": row[0], "source_path": row[1], "vector": vector}
                for row, vector in zip(batch, embeddings)
            ]
            _write_vectors(vectors, replace_existing=False)
            conn.executemany(
                "UPDATE index_progress SET embedding_status='done' WHERE source_path=?",
                [(row[1],) for row in batch],
            )
            conn.commit()
            completed += len(batch)
            print(f"  vector repair: {completed}/{len(missing)}", flush=True)
        except Exception as exc:
            errors += len(batch)
            print(f"  ⚠ vector repair batch failed: {exc}", flush=True)

    if completed:
        _bump_vector_revision(conn)
        conn.commit()
    conn.close()
    result = vector_status()
    result.update({"requested": len(missing), "completed": completed, "errors": errors})
    store_version("vector_repair")
    return result


def backfill_content_hashes(batch_size: int = 500) -> dict:
    """Backfill stable hashes for legacy chunks and matching progress rows."""
    conn = init_metadata_db()
    total = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE COALESCE(content_hash, '') = ''"
    ).fetchone()[0]
    completed = 0
    while True:
        rows = conn.execute(
            """
            SELECT id, source_path, COALESCE(chunk_text, '')
            FROM chunks
            WHERE COALESCE(content_hash, '') = ''
            ORDER BY id
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break
        chunk_updates = []
        progress_updates = []
        for record_id, source_path, chunk_text in rows:
            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            chunk_updates.append((content_hash, record_id))
            progress_updates.append((content_hash, source_path))
        conn.executemany(
            "UPDATE chunks SET content_hash=? WHERE id=?", chunk_updates
        )
        conn.executemany(
            """
            UPDATE index_progress SET content_hash=?
            WHERE source_path=? AND COALESCE(content_hash, '') = ''
            """,
            progress_updates,
        )
        conn.commit()
        completed += len(rows)
        print(f"  content hashes: {completed}/{total}", flush=True)
    conn.close()
    version = store_version("content_hash_backfill")
    return {"requested": total, "completed": completed, "index_version": version}



def search(query, top_k=10):
    import sqlite3
    conn = sqlite3.connect(str(META_DB))

    # FTS5 BM25
    fts_results = {}
    for row in conn.execute("""
        SELECT c.id, c.source_path, c.mega_abteilung, c.band, c.source_type,
               c.page, c.is_main_text, c.chunk_text, rank
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.rowid
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, top_k)):
        fts_results[row[0]] = {
            "id": row[0], "source_path": row[1], "abteilung": row[2],
            "band": row[3], "type": row[4], "page": row[5],
            "is_main_text": row[6], "text": row[7][:300],
        }

    conn.close()
    return list(fts_results.values())

# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MEGA² RAG 索引工具")
    parser.add_argument("--build", action="store_true", help="构建/增量索引")
    parser.add_argument("--no-embed", action="store_true", help="跳过向量索引（仅 FTS5）")
    parser.add_argument("--search", type=str, help="测试检索 (FTS5)")
    parser.add_argument("--status", action="store_true", help="索引状态")
    parser.add_argument("--max", type=int, help="限制索引文件数（测试用）")
    parser.add_argument("--vector-status", action="store_true", help="检查向量去重与覆盖状态")
    parser.add_argument("--dedupe-vectors", action="store_true", help="清理 LanceDB 重复向量")
    parser.add_argument("--repair-vectors", action="store_true", help="补建缺失的 OCR 向量")
    parser.add_argument("--max-vectors", type=int, help="限制本次补建向量数量（测试用）")
    parser.add_argument("--backfill-hashes", action="store_true", help="Backfill stable content hashes for legacy chunks")
    parser.add_argument("--chunk-pages", action="store_true", help="切分已有页面为 passage")
    parser.add_argument("--rechunk", action="store_true", help="重新切分（清空旧 passage 数据）")
    parser.add_argument("--chunk-size", type=int, default=260, help="Passage 目标大小（近似词元，默认 260）")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="Passage overlap（近似词元，默认 50）")
    args = parser.parse_args()

    if args.vector_status:
        print(vector_status())
        sys.exit(0)

    if args.dedupe_vectors:
        print(deduplicate_vectors())
        sys.exit(0)

    if args.repair_vectors:
        print(repair_vectors(max_records=args.max_vectors))
        sys.exit(0)

    if args.backfill_hashes:
        print(backfill_content_hashes())
        sys.exit(0)

    if args.status:
        import sqlite3
        conn = sqlite3.connect(str(META_DB))
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        text_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE source_type='TEXT'").fetchone()[0]
        apparat_count = conn.execute("SELECT COUNT(*) FROM chunks WHERE source_type='APPARAT'").fetchone()[0]
        chars = conn.execute("SELECT COALESCE(SUM(char_count),0) FROM chunks").fetchone()[0]
        # OCR quality distribution
        qdist = {}
        try:
            for row in conn.execute("SELECT ocr_quality, COUNT(*) FROM chunks GROUP BY ocr_quality"):
                qdist[row[0]] = row[1]
        except:
            qdist = {"unknown": total}  # 旧数据库无此列
        # Source provenance summary: OCR and authoritative MEGAdigital remain distinct.
        source_stats = []
        try:
            source_stats = list(conn.execute("""
                SELECT COALESCE(source_collection, 'ocr') AS source,
                       COUNT(*) AS records,
                       COALESCE(SUM(LENGTH(chunk_text)), 0) AS chars
                FROM chunks
                GROUP BY COALESCE(source_collection, 'ocr')
                ORDER BY records DESC
            """))
        except sqlite3.Error:
            source_stats = []
        # Passage status
        try:
            from passage_index import passage_status
            pass_status = passage_status()
            pass_count = pass_status.get("passages", 0)
        except Exception:
            pass_status = {"passages": 0, "fts": 0, "pages": 0, "dirty": True}
            pass_count = 0
        # Index version
        idx_ver = get_current_version()
        print(f"索引状态: {total} 页 | TEXT: {text_count} | APPARAT: {apparat_count} | {chars:,} 字符 | FTS5: {fts} 条")
        for source, records, source_chars in source_stats:
            print(f"来源 {source}: {records} 条 | {source_chars:,} 字符")
        print(f"OCR 质量: {qdist}")
        print(
            f"Passage: {pass_count} 个 | FTS: {pass_status.get('fts', 0)} | "
            f"覆盖页: {pass_status.get('pages', 0)}/{pass_status.get('eligible_pages', 0)} | "
            f"dirty={pass_status.get('dirty', False)}"
        )
        print(f"Index Version: {idx_ver}")
        conn.close()
        sys.exit(0)

    if args.search:
        results = search(args.search)
        print(f"\n查询: '{args.search}' → {len(results)} 条结果\n")
        for i, r in enumerate(results[:5]):
            src = f"MEGA {r['abteilung']}/{r['band']} [{r['type']}] p.{r['page']}"
            print(f"[{i+1}] {src}")
            print(f"    {r['text'][:200]}...\n")
        sys.exit(0)

    if args.chunk_pages:
        from passage_index import build_passage_index
        result = build_passage_index(
            target_tokens=args.chunk_size,
            overlap_tokens=args.chunk_overlap,
            rebuild=args.rechunk,
        )
        result["index_version"] = (
            get_current_version() if result.get("no_op")
            else store_version("passage_v2_build")
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.build:
        print("MEGA² RAG — 索引构建")
        print(f"OCR 目录: {OCR_ROOT}")
        print(f"向量库: {VECTOR_DB}")
        print(f"Embedding: {config['ollama']['embedding_model']}")
        print()
        build_index(max_files=args.max, use_embedding=not args.no_embed)
