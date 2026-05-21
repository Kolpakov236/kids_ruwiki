from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import contextmanager

from app.settings import settings

_OR_REPLACE_RE = re.compile(r"\bOR\s+REPLACE\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Cursor and connection wrappers
# ---------------------------------------------------------------------------

class _CursorWrapper:
    """Normalises sqlite3 / psycopg2 cursor differences."""

    def __init__(self, cur, is_pg: bool, last_id: int | None = None):
        self._cur = cur
        self._is_pg = is_pg
        self._last_id = last_id

    @property
    def lastrowid(self) -> int | None:
        return self._last_id if self._is_pg else self._cur.lastrowid

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def _to_dict(self, row):
        if row is None or not self._is_pg or not self._cur.description:
            return row
        return dict(zip((d[0] for d in self._cur.description), row))

    def fetchone(self):
        return self._to_dict(self._cur.fetchone())

    def fetchall(self):
        rows = self._cur.fetchall()
        if not self._is_pg or not self._cur.description:
            return rows
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, r)) for r in rows]


class _ConnWrapper:
    """Wraps a raw DB connection, smoothing over SQLite vs PostgreSQL differences."""

    def __init__(self, raw, is_pg: bool):
        self._raw = raw
        self._is_pg = is_pg

    def _adapt(self, sql: str) -> tuple[str, bool]:
        """Return (adapted_sql, was_insert_or_replace)."""
        was_or_replace = bool(_OR_REPLACE_RE.search(sql))
        if not self._is_pg:
            return sql, False
        adapted = sql.replace("?", "%s")
        if was_or_replace:
            adapted = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "INSERT", adapted, flags=re.IGNORECASE)
        return adapted, was_or_replace

    def execute(self, sql: str, params=()) -> _CursorWrapper:
        adapted, was_or_replace = self._adapt(sql)
        cur = self._raw.cursor()
        is_insert = adapted.lstrip().upper().startswith("INSERT")

        if self._is_pg and is_insert:
            if was_or_replace:
                adapted = adapted.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
            # RETURNING * lets us capture the inserted id without a second round-trip
            ret_sql = adapted.rstrip().rstrip(";") + " RETURNING *"
            cur.execute(ret_sql, params)
            row = cur.fetchone()
            last_id = None
            if row and cur.description:
                cols = [d[0] for d in cur.description]
                if "id" in cols:
                    last_id = row[cols.index("id")]
            return _CursorWrapper(cur, is_pg=True, last_id=last_id)

        cur.execute(adapted, params)
        return _CursorWrapper(cur, self._is_pg)

    def executescript(self, sql: str) -> None:
        if self._is_pg:
            cur = self._raw.cursor()
            for stmt in sql.split(";"):
                s = stmt.strip()
                if s and not s.upper().startswith("PRAGMA"):
                    cur.execute(s)
        else:
            self._raw.executescript(sql)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def _use_pg() -> bool:
    return bool(getattr(settings, "database_url", None))


def ensure_dirs() -> None:
    os.makedirs(os.path.dirname(settings.sqlite_path), exist_ok=True)
    os.makedirs(settings.chroma_path, exist_ok=True)


def connect() -> _ConnWrapper:
    if _use_pg():
        import psycopg2  # type: ignore
        raw = psycopg2.connect(settings.database_url)
        return _ConnWrapper(raw, is_pg=True)
    ensure_dirs()
    raw = sqlite3.connect(settings.sqlite_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL;")
    raw.execute("PRAGMA foreign_keys=ON;")
    return _ConnWrapper(raw, is_pg=False)


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS simplification_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at INTEGER NOT NULL,
      query TEXT NOT NULL,
      age INTEGER NOT NULL,
      mode TEXT NOT NULL DEFAULT 'balanced',
      source_title TEXT NOT NULL,
      source_url TEXT NOT NULL,
      original_text TEXT NOT NULL,
      main_idea TEXT NOT NULL DEFAULT '',
      simplified_text TEXT NOT NULL,
      glossary_json TEXT NOT NULL,
      analogies_json TEXT NOT NULL,
      quiz_json TEXT NOT NULL DEFAULT '[]',
      quality_json TEXT NOT NULL DEFAULT '{}',
      model_json TEXT NOT NULL DEFAULT '{}',
      verifier_json TEXT NOT NULL DEFAULT '{}',
      cached INTEGER NOT NULL DEFAULT 0,
      latency_ms INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_history_query_age ON simplification_history(query, age);

    CREATE TABLE IF NOT EXISTS semantic_cache (
      key TEXT PRIMARY KEY,
      created_at INTEGER NOT NULL,
      query TEXT NOT NULL,
      age INTEGER NOT NULL,
      mode TEXT NOT NULL DEFAULT 'balanced',
      source_title TEXT NOT NULL,
      source_url TEXT NOT NULL,
      original_text TEXT NOT NULL,
      main_idea TEXT NOT NULL DEFAULT '',
      simplified_text TEXT NOT NULL,
      glossary_json TEXT NOT NULL,
      analogies_json TEXT NOT NULL,
      quiz_json TEXT NOT NULL DEFAULT '[]',
      quality_json TEXT NOT NULL DEFAULT '{}',
      model_json TEXT NOT NULL DEFAULT '{}',
      verifier_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS ratings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at INTEGER NOT NULL,
      history_key TEXT NOT NULL,
      stars INTEGER NOT NULL,
      comment TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_ratings_key ON ratings(history_key);

    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at INTEGER NOT NULL,
      email TEXT UNIQUE,
      password_hash TEXT,
      display_name TEXT NOT NULL DEFAULT '',
      birth_date TEXT,
      vk_id TEXT UNIQUE,
      yandex_id TEXT UNIQUE,
      avatar_url TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_users_vk ON users(vk_id);
    CREATE INDEX IF NOT EXISTS idx_users_yandex ON users(yandex_id);

    CREATE TABLE IF NOT EXISTS chats (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at INTEGER NOT NULL,
      title TEXT NOT NULL DEFAULT 'Новый чат',
      last_message_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, last_message_at);

    CREATE TABLE IF NOT EXISTS chat_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
      created_at INTEGER NOT NULL,
      role TEXT NOT NULL,
      query TEXT NOT NULL DEFAULT '',
      response_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, created_at);

    CREATE TABLE IF NOT EXISTS usage_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at INTEGER NOT NULL,
      user_id INTEGER,
      chat_id INTEGER,
      operation TEXT NOT NULL,
      query TEXT,
      age INTEGER,
      mode TEXT,
      model TEXT,
      latency_ms INTEGER,
      cached INTEGER DEFAULT 0,
      success INTEGER DEFAULT 1,
      error_text TEXT,
      extras_json TEXT DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_usage_logs_created ON usage_logs(created_at);
    CREATE INDEX IF NOT EXISTS idx_usage_logs_user ON usage_logs(user_id);
"""


def _schema_sql() -> str:
    if _use_pg():
        return _BASE_SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return _BASE_SCHEMA


def _get_columns(conn: _ConnWrapper, table: str) -> set[str]:
    if _use_pg():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchall()
        return {r["column_name"] for r in rows}
    rows = conn._raw.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def init_db() -> None:
    with tx() as conn:
        conn.executescript(_schema_sql())
        _migrate(conn)


def _migrate(conn: _ConnWrapper) -> None:
    for table in ("simplification_history", "semantic_cache"):
        cols = _get_columns(conn, table)
        for col, ddl in [
            ("mode", "TEXT NOT NULL DEFAULT 'balanced'"),
            ("main_idea", "TEXT NOT NULL DEFAULT ''"),
            ("quiz_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("quality_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("model_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("verifier_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("extras_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("summarization_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("timings_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("metrics_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def now_ms() -> int:
    return int(time.time() * 1000)
