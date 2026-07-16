"""SQLite cache for repeatable MEGA RAG retrieval and generation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml


_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict:
    with _CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _cache_db_path() -> str:
    return _load_config()["paths"]["cache_db"]


def _cache_ttl() -> timedelta:
    try:
        return timedelta(hours=max(0, int(_load_config().get("cache", {}).get("ttl_hours", 48))))
    except Exception:
        return timedelta(hours=48)


def _cache_max_entries() -> int:
    try:
        return max(0, int(_load_config().get("cache", {}).get("max_entries", 10000)))
    except Exception:
        return 10000


def _prune_cache(conn: sqlite3.Connection) -> None:
    """Delete expired and least-recently-used entries inside the caller transaction."""
    cutoff = (datetime.now() - _cache_ttl()).isoformat()
    conn.execute("DELETE FROM query_cache WHERE created_at < ?", (cutoff,))
    maximum = _cache_max_entries()
    if maximum == 0:
        conn.execute("DELETE FROM query_cache")
        return
    total = conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
    overflow = total - maximum
    if overflow > 0:
        conn.execute(
            """
            DELETE FROM query_cache WHERE cache_key IN (
                SELECT cache_key FROM query_cache
                ORDER BY COALESCE(last_accessed_at, created_at) ASC, created_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )

def _now() -> str:
    return datetime.now().isoformat()


def _safe_connect() -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(_cache_db_path())
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except Exception:
        return None


def init_cache() -> None:
    conn = _safe_connect()
    if not conn:
        return
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_cache (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT NOT NULL,
                index_version TEXT,
                prompt_version TEXT,
                model TEXT,
                input_hash TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT,
                hit_count INTEGER DEFAULT 0
            )
            """
        )
        _prune_cache(conn)
        conn.commit()
    finally:
        conn.close()


def make_cache_key(query: str, filters: str, top_k: int, mode: str,
                   index_version: str = "", prompt_version: str = "",
                   model: str = "") -> str:
    raw = "|".join((
        " ".join(query.strip().lower().split()), filters, str(top_k), mode,
        index_version, prompt_version, model,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def cache_get(cache_key: str) -> Optional[str]:
    conn = _safe_connect()
    if not conn:
        return None
    try:
        row = conn.execute(
            "SELECT output_json, created_at FROM query_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        try:
            created_at = datetime.fromisoformat(row[1])
        except (TypeError, ValueError):
            created_at = None
        if created_at is None or datetime.now() - created_at > _cache_ttl():
            conn.execute("DELETE FROM query_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        conn.execute(
            "UPDATE query_cache SET last_accessed_at = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
            (_now(), cache_key),
        )
        conn.commit()
        return row[0]
    except Exception:
        return None
    finally:
        conn.close()


def cache_put(cache_key: str, cache_type: str, output: Any,
              index_version: str = "", prompt_version: str = "",
              model: str = "", input_hash: str = "") -> None:
    conn = _safe_connect()
    if not conn:
        return
    try:
        output_json = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        conn.execute(
            """
            INSERT OR REPLACE INTO query_cache
            (cache_key, cache_type, index_version, prompt_version, model, input_hash, output_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cache_key, cache_type, index_version, prompt_version, model,
             input_hash or cache_key, output_json, _now()),
        )
        _prune_cache(conn)
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def clear_cache() -> bool:
    conn = _safe_connect()
    if not conn:
        return False
    try:
        conn.execute("DELETE FROM query_cache")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def cache_status() -> dict:
    conn = _safe_connect()
    if not conn:
        return {"error": "cache database unavailable"}
    try:
        total = conn.execute("SELECT COUNT(*) FROM query_cache").fetchone()[0]
        hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM query_cache").fetchone()[0]
        by_type = dict(conn.execute("SELECT cache_type, COUNT(*) FROM query_cache GROUP BY cache_type"))
        return {"total_entries": total, "total_hits": hits, "by_type": by_type}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


init_cache()