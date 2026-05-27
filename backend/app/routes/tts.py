from __future__ import annotations

import hashlib
import io
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

_VOICE_FEMALE = "ru-RU-SvetlanaNeural"
_VOICE_MALE = "ru-RU-DmitryNeural"
_DEFAULT_VOICE = _VOICE_FEMALE

# Simple in-process LRU-style cache (text_hash → mp3 bytes)
_cache: dict[str, bytes] = {}
_CACHE_MAX = 64


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    voice: str = Field(default=_DEFAULT_VOICE)
    rate: str = Field(default="-5%")   # slightly slower than default


@router.post("/tts", response_class=Response)
async def tts(req: TtsRequest) -> Response:
    try:
        import edge_tts
    except ImportError:
        raise HTTPException(status_code=503, detail="edge_tts_not_installed")

    voice = req.voice if req.voice in (_VOICE_FEMALE, _VOICE_MALE) else _DEFAULT_VOICE
    cache_key = hashlib.md5(f"{voice}:{req.rate}:{req.text}".encode()).hexdigest()

    if cache_key in _cache:
        return Response(content=_cache[cache_key], media_type="audio/mpeg")

    try:
        communicate = edge_tts.Communicate(req.text, voice, rate=req.rate)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        audio = b"".join(chunks)
    except Exception as e:
        logger.warning("edge-tts failed: %s", e)
        raise HTTPException(status_code=502, detail=f"tts_generation_failed:{e}")

    if not audio:
        raise HTTPException(status_code=502, detail="tts_empty_audio")

    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[cache_key] = audio

    return Response(content=audio, media_type="audio/mpeg")
