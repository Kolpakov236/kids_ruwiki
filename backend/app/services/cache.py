from __future__ import annotations

import hashlib
import json

import chromadb
from chromadb.utils import embedding_functions

from app.services.db import init_db, tx, now_ms
from app.settings import settings


def cache_key(query: str, age: int, mode: str, source_title: str, model_variant: str) -> str:
    h = hashlib.sha256()
    h.update(query.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(str(age).encode("utf-8"))
    h.update(b"\0")
    h.update(mode.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(source_title.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(model_variant.strip().lower().encode("utf-8"))
    return h.hexdigest()


def clear_sqlite_cache() -> dict[str, int]:
    init_db()
    with tx() as conn:
        semantic = conn.execute("DELETE FROM semantic_cache").rowcount
        history = conn.execute("DELETE FROM simplification_history").rowcount
    return {"semantic_cache": semantic, "simplification_history": history}


def get_sqlite_cached(key: str):
    init_db()
    with tx() as conn:
        row = conn.execute("SELECT * FROM semantic_cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return {
        "query": row["query"],
        "age": row["age"],
        "mode": row["mode"],
        "source_title": row["source_title"],
        "source_url": row["source_url"],
        "original_text": row["original_text"],
        "main_idea": row["main_idea"],
        "simplified_text": row["simplified_text"],
        "glossary": json.loads(row["glossary_json"]),
        "analogies": json.loads(row["analogies_json"]),
        "quiz": json.loads(row["quiz_json"]),
        "quality": json.loads(row["quality_json"]),
        "model": json.loads(row["model_json"]),
        "verifier": json.loads(row["verifier_json"]),
    }


def put_sqlite_cached(key: str, payload: dict) -> None:
    init_db()
    with tx() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO semantic_cache
              (key, created_at, query, age, mode, source_title, source_url, original_text, main_idea, simplified_text, glossary_json, analogies_json, quiz_json, quality_json, model_json, verifier_json)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                now_ms(),
                payload["query"],
                payload["age"],
                payload.get("mode", "balanced"),
                payload["source_title"],
                payload["source_url"],
                payload["original_text"],
                payload.get("main_idea", ""),
                payload["simplified_text"],
                json.dumps(payload["glossary"], ensure_ascii=False),
                json.dumps(payload["analogies"], ensure_ascii=False),
                json.dumps(payload.get("quiz", []), ensure_ascii=False),
                json.dumps(payload.get("quality", {}), ensure_ascii=False),
                json.dumps(payload.get("model", {}), ensure_ascii=False),
                json.dumps(payload.get("verifier", {}), ensure_ascii=False),
            ),
        )


def log_history(payload: dict, cached: bool, latency_ms: int) -> None:
    init_db()
    with tx() as conn:
        conn.execute(
            """
            INSERT INTO simplification_history
              (created_at, query, age, mode, source_title, source_url, original_text, main_idea, simplified_text, glossary_json, analogies_json, quiz_json, quality_json, model_json, verifier_json, cached, latency_ms)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_ms(),
                payload["query"],
                payload["age"],
                payload.get("mode", "balanced"),
                payload["source_title"],
                payload["source_url"],
                payload["original_text"],
                payload.get("main_idea", ""),
                payload["simplified_text"],
                json.dumps(payload["glossary"], ensure_ascii=False),
                json.dumps(payload["analogies"], ensure_ascii=False),
                json.dumps(payload.get("quiz", []), ensure_ascii=False),
                json.dumps(payload.get("quality", {}), ensure_ascii=False),
                json.dumps(payload.get("model", {}), ensure_ascii=False),
                json.dumps(payload.get("verifier", {}), ensure_ascii=False),
                1 if cached else 0,
                latency_ms,
            ),
        )


def _chroma_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=settings.chroma_path)


def _embed_fn():
    return embedding_functions.SentenceTransformerEmbeddingFunction(model_name=settings.embedding_model)


def get_similar_originals(text: str, top_k: int = 3):
    client = _chroma_client()
    col = client.get_or_create_collection(name="ruwiki_originals", embedding_function=_embed_fn())
    res = col.query(query_texts=[text], n_results=top_k, include=["documents", "metadatas", "distances"])
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    out = []
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"document": doc, "meta": meta, "distance": dist})
    return out


def upsert_original(text: str, meta: dict) -> None:
    client = _chroma_client()
    col = client.get_or_create_collection(name="ruwiki_originals", embedding_function=_embed_fn())
    doc_id = meta.get("key") or hashlib.sha256(text.encode("utf-8")).hexdigest()
    col.upsert(ids=[doc_id], documents=[text], metadatas=[meta])

