from __future__ import annotations

import json
import time

from app.services.db import tx
from app.settings import settings


def create_chat(user_id: int, title: str = "Новый чат") -> int:
    now = int(time.time() * 1000)
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO chats (user_id, created_at, title, last_message_at) VALUES (?,?,?,?)",
            (user_id, now, title, now),
        )
        chat_id = cur.lastrowid
        # Keep only max_chats_per_user per user (delete oldest)
        conn.execute(
            """DELETE FROM chats WHERE user_id = ? AND id NOT IN (
               SELECT id FROM chats WHERE user_id = ? ORDER BY last_message_at DESC LIMIT ?)""",
            (user_id, user_id, settings.max_chats_per_user),
        )
        return chat_id


def save_chat_message(chat_id: int, role: str, query: str, response: dict) -> None:
    now = int(time.time() * 1000)
    response_json = json.dumps(response, ensure_ascii=False)
    with tx() as conn:
        conn.execute(
            "INSERT INTO chat_messages (chat_id, created_at, role, query, response_json) VALUES (?,?,?,?,?)",
            (chat_id, now, role, query, response_json),
        )
        conn.execute(
            "UPDATE chats SET last_message_at=?, title=CASE WHEN title='Новый чат' THEN ? ELSE title END WHERE id=?",
            (now, query[:60], chat_id),
        )


def list_chats(user_id: int) -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT id, created_at, title, last_message_at FROM chats WHERE user_id=? ORDER BY last_message_at DESC LIMIT ?",
            (user_id, settings.max_chats_per_user),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_chat(chat_id: int, user_id: int) -> bool:
    with tx() as conn:
        row = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, user_id)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        return True


def get_chat_messages(chat_id: int, user_id: int) -> list[dict] | None:
    with tx() as conn:
        chat = conn.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, user_id)).fetchone()
        if not chat:
            return None
        rows = conn.execute(
            "SELECT id, created_at, role, query, response_json FROM chat_messages WHERE chat_id=? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
        result = []
        for r in rows:
            row = dict(r)
            row["response"] = json.loads(row.pop("response_json", "{}"))
            result.append(row)
        return result
