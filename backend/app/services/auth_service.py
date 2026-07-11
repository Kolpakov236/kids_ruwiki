from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.db import tx
from app.settings import settings

_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"{salt}:{h.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split(":", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
        return candidate.hex() == h
    except Exception:
        return False


def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.jwt_expire_days * 86400,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return int(payload["sub"])
    except Exception:
        return None


def get_optional_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(_security),
) -> Optional[int]:
    if not creds:
        return None
    return decode_token(creds.credentials)


def require_user(user_id: Optional[int] = Depends(get_optional_user_id)) -> int:
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return user_id


def get_user_by_id(user_id: int) -> Optional[dict]:
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_or_create_oauth_user(
    *,
    provider: str,
    provider_id: str,
    email: Optional[str],
    display_name: str,
    birth_date: Optional[str],
    avatar_url: Optional[str],
) -> dict:
    col = f"{provider}_id"
    with tx() as conn:
        # Try by provider ID first
        row = conn.execute(f"SELECT * FROM users WHERE {col} = ?", (provider_id,)).fetchone()
        if row:
            conn.execute(
                f"UPDATE users SET display_name=?, avatar_url=? WHERE id=?",
                (display_name or dict(row)["display_name"], avatar_url, dict(row)["id"]),
            )
            return dict(row)
        # Try by email
        if email:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                conn.execute(
                    f"UPDATE users SET {col}=?, display_name=?, avatar_url=? WHERE id=?",
                    (provider_id, display_name or dict(row)["display_name"], avatar_url, dict(row)["id"]),
                )
                return dict(row)
        # Create new user
        now = int(time.time() * 1000)
        cur = conn.execute(
            f"INSERT INTO users (created_at, email, display_name, birth_date, {col}, avatar_url) VALUES (?,?,?,?,?,?)",
            (now, email, display_name, birth_date, provider_id, avatar_url),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    