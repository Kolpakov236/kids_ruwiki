from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.schemas import ChatMessageOut, ChatOut, CreateChatResponse
from app.services.auth_service import require_user
from app.services.chat_service import create_chat, delete_chat, get_chat_messages, list_chats

router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("", response_model=list[ChatOut])
async def get_chats(user_id: int = Depends(require_user)) -> list[ChatOut]:
    return [ChatOut(**c) for c in list_chats(user_id)]


@router.post("", response_model=CreateChatResponse)
async def new_chat(user_id: int = Depends(require_user)) -> CreateChatResponse:
    chat_id = create_chat(user_id)
    return CreateChatResponse(chat_id=chat_id)


@router.delete("/{chat_id}")
async def remove_chat(chat_id: int, user_id: int = Depends(require_user)) -> dict:
    if not delete_chat(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Чат не найден")
    return {"ok": True}


@router.get("/{chat_id}", response_model=list[ChatMessageOut])
async def get_chat(chat_id: int, user_id: int = Depends(require_user)) -> list[ChatMessageOut]:
    messages = get_chat_messages(chat_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return [ChatMessageOut(**m) for m in messages]
