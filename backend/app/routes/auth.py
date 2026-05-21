from __future__ import annotations

import time
from datetime import date
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.auth_service import (
    create_token,
    get_optional_user_id,
    get_or_create_oauth_user,
    get_user_by_id,
    hash_password,
    require_user,
    verify_password,
)
from app.services.db import tx
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _birth_date_to_age(birth_date: Optional[str]) -> int:
    if not birth_date:
        return 10
    try:
        bd = date.fromisoformat(birth_date)
        today = date.today()
        age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        return max(6, min(14, age))
    except Exception:
        return 10


def _user_out(user: dict) -> UserOut:
    return UserOut(
        id=user["id"],
        email=user.get("email"),
        display_name=user.get("display_name") or "",
        birth_date=user.get("birth_date"),
        avatar_url=user.get("avatar_url"),
        age=_birth_date_to_age(user.get("birth_date")),
    )


# ---------------------------------------------------------------------------
# Register / Login
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest) -> TokenResponse:
    with tx() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email=?", (req.email,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
        now = int(time.time() * 1000)
        cur = conn.execute(
            "INSERT INTO users (created_at, email, password_hash, display_name, birth_date) VALUES (?,?,?,?,?)",
            (now, req.email, hash_password(req.password), req.display_name or req.email.split("@")[0], req.birth_date),
        )
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
    token = create_token(user["id"])
    return TokenResponse(access_token=token, user=_user_out(user))


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest) -> TokenResponse:
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (req.email,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    user = dict(row)
    if not user.get("password_hash") or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    token = create_token(user["id"])
    return TokenResponse(access_token=token, user=_user_out(user))


from fastapi import Depends

@router.get("/me", response_model=UserOut)
async def me_endpoint(user_id: int = Depends(require_user)) -> UserOut:
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return _user_out(user)


# ---------------------------------------------------------------------------
# VK OAuth
# ---------------------------------------------------------------------------

@router.get("/vk")
async def vk_redirect() -> RedirectResponse:
    if not settings.vk_enabled:
        raise HTTPException(status_code=400, detail="VK OAuth не настроен")
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/auth/vk/callback"
    params = {
        "client_id": settings.vk_client_id,
        "display": "page",
        "redirect_uri": redirect_uri,
        "scope": "email",
        "response_type": "code",
        "v": "5.131",
    }
    return RedirectResponse(f"https://oauth.vk.com/authorize?{urlencode(params)}")


@router.get("/vk/callback")
async def vk_callback(code: str) -> RedirectResponse:
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/auth/vk/callback"
    async with httpx.AsyncClient(timeout=15) as client:
        # Exchange code for token
        r = await client.get(
            "https://oauth.vk.com/access_token",
            params={
                "client_id": settings.vk_client_id,
                "client_secret": settings.vk_client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        r.raise_for_status()
        data = r.json()
        access_token = data.get("access_token")
        vk_id = str(data.get("user_id", ""))
        email = data.get("email")
        if not access_token or not vk_id:
            return RedirectResponse(f"{settings.frontend_url}#auth_error=vk_token_failed")
        # Get user info with birth date
        r2 = await client.get(
            "https://api.vk.com/method/users.get",
            params={"user_ids": vk_id, "fields": "bdate,photo_200,first_name,last_name", "access_token": access_token, "v": "5.131"},
        )
        r2.raise_for_status()
        users = r2.json().get("response", [{}])
        vk_user = users[0] if users else {}

    display_name = f"{vk_user.get('first_name', '')} {vk_user.get('last_name', '')}".strip()
    bdate_raw = vk_user.get("bdate", "")
    birth_date = _parse_vk_bdate(bdate_raw)
    avatar_url = vk_user.get("photo_200")

    user = get_or_create_oauth_user(
        provider="vk",
        provider_id=vk_id,
        email=email,
        display_name=display_name or f"vk_{vk_id}",
        birth_date=birth_date,
        avatar_url=avatar_url,
    )
    token = create_token(user["id"])
    return RedirectResponse(f"{settings.frontend_url}#token={token}")


def _parse_vk_bdate(bdate: str) -> Optional[str]:
    if not bdate:
        return None
    parts = bdate.split(".")
    if len(parts) == 3:
        d, m, y = parts
        try:
            return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Yandex OAuth
# ---------------------------------------------------------------------------

@router.get("/yandex")
async def yandex_redirect() -> RedirectResponse:
    if not settings.yandex_oauth_enabled:
        raise HTTPException(status_code=400, detail="Yandex OAuth не настроен")
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/auth/yandex/callback"
    params = {
        "response_type": "code",
        "client_id": settings.yandex_client_id,
        "redirect_uri": redirect_uri,
    }
    return RedirectResponse(f"https://oauth.yandex.ru/authorize?{urlencode(params)}")


@router.get("/yandex/callback")
async def yandex_callback(code: str) -> RedirectResponse:
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/auth/yandex/callback"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.yandex_client_id,
                "client_secret": settings.yandex_client_secret,
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        token_data = r.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse(f"{settings.frontend_url}#auth_error=yandex_token_failed")
        r2 = await client.get(
            "https://login.yandex.ru/info",
            params={"format": "json"},
            headers={"Authorization": f"OAuth {access_token}"},
        )
        r2.raise_for_status()
        info = r2.json()

    yandex_id = str(info.get("id", ""))
    email = info.get("default_email") or info.get("emails", [None])[0]
    display_name = info.get("display_name") or info.get("real_name") or f"yandex_{yandex_id}"
    birth_date = info.get("birthday")  # format: YYYY-MM-DD or None
    avatar_id = info.get("default_avatar_id")
    avatar_url = f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200" if avatar_id else None

    user = get_or_create_oauth_user(
        provider="yandex",
        provider_id=yandex_id,
        email=email,
        display_name=display_name,
        birth_date=birth_date,
        avatar_url=avatar_url,
    )
    token = create_token(user["id"])
    return RedirectResponse(f"{settings.frontend_url}#token={token}")
