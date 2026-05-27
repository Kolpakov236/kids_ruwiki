from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.silero_tts import synthesize, _SPEAKER, _SAMPLE_RATE

logger = logging.getLogger(__name__)

router = APIRouter()

_cache: dict[str, bytes] = {}
_CACHE_MAX = 64


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    speaker: str = Field(default=_SPEAKER)
    sample_rate: int = Field(default=_SAMPLE_RATE)


@router.post("/tts", response_class=Response)
async def tts(req: TtsRequest) -> Response:
    speaker = req.speaker if req.speaker in ("aidar", "baya", "kseniya", "xenia", "eugene") else _SPEAKER
    cache_key = hashlib.md5(f"{speaker}:{req.sample_rate}:{req.text}".encode()).hexdigest()

    if cache_key in _cache:
        return Response(content=_cache[cache_key], media_type="audio/wav")

    try:
        audio = synthesize(req.text, speaker=speaker, sample_rate=req.sample_rate)
    except Exception as e:
        logger.warning("silero_tts failed: %s", e)
        raise HTTPException(status_code=502, detail=f"tts_failed:{e}")

    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[cache_key] = audio

    return Response(content=audio, media_type="audio/wav")
