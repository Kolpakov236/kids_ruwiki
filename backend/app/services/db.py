from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

from app.settings import settings


def ensure_dirs() -> None:
    os.makedirs(os.path.dirname(settings.sqlite_path), exist_ok=True)
    os.makedirs(settings.chroma_path, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(settings.sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with tx() as conn:
        conn.executescript(
            """
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
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    for table in ("simplification_history", "semantic_cache"):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
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
