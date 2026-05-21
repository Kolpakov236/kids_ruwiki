from __future__ import annotations

import json
import time
from typing import Optional

from app.services.db import tx


def log_usage(
    operation: str,
    *,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    query: Optional[str] = None,
    age: Optional[int] = None,
    mode: Optional[str] = None,
    model: Optional[str] = None,
    latency_ms: Optional[int] = None,
    cached: bool = False,
    success: bool = True,
    error_text: Optional[str] = None,
    extras: Optional[dict] = None,
) -> None:
    try:
        now = int(time.time() * 1000)
        with tx() as conn:
            conn.execute(
                """INSERT INTO usage_logs
                   (created_at, user_id, chat_id, operation, query, age, mode, model,
                    latency_ms, cached, success, error_text, extras_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now, user_id, chat_id, operation, query, age, mode, model,
                    latency_ms, 1 if cached else 0, 1 if success else 0,
                    error_text, json.dumps(extras or {}, ensure_ascii=False),
                ),
            )
    except Exception:
        pass  # Logs must never break the main flow
