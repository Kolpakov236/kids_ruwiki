from __future__ import annotations

import hashlib
import json

import chromadb
from chromadb.utils import embedding_functions

from app.services.db import init_db, tx, now_ms
from app.settings import settings


def cache_key(
    query: str,
    age_group: str,
    mode: str,
    source_title: str,
    model_variant: str,
    key_facts: dict | None = None,
) -> str:
    h = hashlib.sha256()
    h.update(query.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(age_group.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(mode.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(source_title.strip().lower().encode("utf-8"))
    h.update(b"\0")
    h.update(model_variant.strip().lower().encode("utf-8"))
    facts = key_facts or {}
    required_terms = facts.get("required_terms") or []
    formulas = facts.get("formulas") or []
    fact_items = sorted(
        {
            str(x).strip().lower()
            for x in [*required_terms[:48], *formulas[:16]]
            if str(x).strip()
        }
    )
    h.update(b"\0")
    h.update("|".join(fact_items).encode("utf-8"))
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
    keys = row.keys()

    def _json(col: str, default):
        if col in keys:
            raw = row[col]
            try:
                return json.loads(raw) if raw else default
            except Exception:
                return default
        return default

    extras = _json("extras_json", {})
    return {
        "query": row["query"],
        "age": row["age"],
        "mode": row["mode"],
        "source_title": row["source_title"],
        "source_url": row["source_url"],
        "original_text": row["original_text"],
        "main_idea": row["main_idea"],
        "simplified_text": row["simplified_text"],
        "glossary": _json("glossary_json", []),
        "analogies": _json("analogies_json", []),
        "quiz": _json("quiz_json", []),
        "quality": _json("quality_json", {}),
        "model": _json("model_json", {}),
        "verifier": _json("verifier_json", {}),
        "reasoning_steps": extras.get("reasoning_steps") or [],
        "learning_steps": extras.get("learning_steps") or [],
        "accuracy": extras.get("accuracy") or {},
        "evaluation": extras.get("evaluation") or {},
        "age_group": extras.get("age_group") or "",
        "cache_similarity": extras.get("cache_similarity"),
        "summarization": _json("summarization_json", {}),
        "metrics_enabled": bool(row["metrics_enabled"]) if "metrics_enabled" in keys else True,
        "history_key": key,
    }


def _extras_payload(payload: dict) -> dict:
    return {
        "reasoning_steps": payload.get("reasoning_steps") or [],
        "learning_steps": payload.get("learning_steps") or [],
        "accuracy": payload.get("accuracy") or {},
        "evaluation": payload.get("evaluation") or {},
        "key_facts": payload.get("key_facts") or {},
        "age_group": payload.get("age_group") or "",
        "cache_similarity": payload.get("cache_similarity"),
    }


def put_sqlite_cached(key: str, payload: dict) -> None:
    init_db()
    with tx() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO semantic_cache
              (key, created_at, query, age, mode, source_title, source_url, original_text,
               main_idea, simplified_text, glossary_json, analogies_json, quiz_json,
               quality_json, model_json, verifier_json, extras_json,
               summarization_json, timings_json, metrics_enabled)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(_extras_payload(payload), ensure_ascii=False),
                json.dumps(payload.get("summarization", {}), ensure_ascii=False),
                json.dumps(payload.get("timings", {}), ensure_ascii=False),
                1 if payload.get("metrics_enabled", True) else 0,
            ),
        )


def log_history(
    payload: dict,
    cached: bool,
    latency_ms: int,
    timings: dict | None = None,
) -> str:
    """Insert a history row and return its rowid (used as history_key for ratings)."""
    init_db()
    with tx() as conn:
        cur = conn.execute(
            """
            INSERT INTO simplification_history
              (created_at, query, age, mode, source_title, source_url, original_text,
               main_idea, simplified_text, glossary_json, analogies_json, quiz_json,
               quality_json, model_json, verifier_json, extras_json,
               summarization_json, timings_json, metrics_enabled,
               cached, latency_ms)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(_extras_payload(payload), ensure_ascii=False),
                json.dumps(payload.get("summarization", {}), ensure_ascii=False),
                json.dumps(timings or {}, ensure_ascii=False),
                1 if payload.get("metrics_enabled", True) else 0,
                1 if cached else 0,
                latency_ms,
            ),
        )
        return str(cur.lastrowid)


def save_rating(history_key: str, stars: int, comment: str = "") -> None:
    init_db()
    with tx() as conn:
        conn.execute(
            "INSERT INTO ratings (created_at, history_key, stars, comment) VALUES (?, ?, ?, ?)",
            (now_ms(), history_key, stars, comment[:500]),
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


def get_similar_answer_key(query: str, age_group: str, top_k: int = 1, threshold: float = 0.92):
    client = _chroma_client()
    col = client.get_or_create_collection(
        name="ruwiki_answer_cache",
        embedding_function=_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )
    res = col.query(
        query_texts=[f"{age_group}\n{query.strip().lower()}"],
        n_results=top_k,
        include=["metadatas", "distances"],
        where={"age_group": age_group},
    )
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    if not metas or not dists:
        return None
    distance = float(dists[0])
    similarity = 1.0 - distance
    if similarity < threshold:
        return None
    key = (metas[0] or {}).get("key")
    if not key:
        return None
    return {"key": key, "similarity": round(similarity, 4)}


def upsert_answer_query(query: str, age_group: str, key: str, meta: dict | None = None) -> None:
    client = _chroma_client()
    col = client.get_or_create_collection(
        name="ruwiki_answer_cache",
        embedding_function=_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )
    doc = f"{age_group}\n{query.strip().lower()}"
    doc_id = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    metadata = {"key": key, "age_group": age_group, **(meta or {})}
    col.upsert(ids=[doc_id], documents=[doc], metadatas=[metadata])
