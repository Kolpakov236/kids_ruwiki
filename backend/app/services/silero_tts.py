from __future__ import annotations

import io
import logging
import os
import pathlib
import re
import threading
import wave

logger = logging.getLogger(__name__)

_MODEL_URL = "https://models.silero.ai/models/tts/ru/v3_1_ru.pt"
_SAMPLE_RATE = 48000
_SPEAKER = "baya"  # БИЯ

# Resolve model path: prefer /tmp in Cloud Functions, ./data locally
_CF_ROOT = pathlib.Path("/function/code")
_MODEL_PATH = pathlib.Path("/tmp/silero_model.pt") if _CF_ROOT.exists() else (
    pathlib.Path(__file__).parent.parent.parent / "data" / "silero_model.pt"
)

_model = None
_model_lock = threading.Lock()


def _num2words_ru(match: re.Match) -> str:
    try:
        from num2words import num2words
        raw = match.group().replace(",", ".").replace("_", "")
        return num2words(float(raw), lang="ru")
    except Exception:
        return match.group()


def _preprocess(text: str) -> str:
    # convert numbers to words
    text = re.sub(r"-?[0-9][0-9,._]*", _num2words_ru, text)
    # strip leftover punctuation clusters that confuse the model
    text = re.sub(r"[^\w\s\.\,\!\?\-–—«»]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def download_model() -> None:
    """Download Silero model weights if not already present."""
    if _MODEL_PATH.exists():
        logger.info("Silero model already at %s", _MODEL_PATH)
        return
    import torch
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading Silero model → %s …", _MODEL_PATH)
    torch.hub.download_url_to_file(_MODEL_URL, str(_MODEL_PATH))
    logger.info("Silero model downloaded (%d MB)", _MODEL_PATH.stat().st_size // 1_000_000)


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        import torch
        download_model()
        torch.set_num_threads(4)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("Loading Silero model on %s …", device)
        m = torch.package.PackageImporter(str(_MODEL_PATH)).load_pickle("tts_models", "model")
        m.to(device)
        _model = m
        logger.info("Silero model ready")
    return _model


def synthesize(text: str, speaker: str = _SPEAKER, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Return WAV bytes for the given Russian text."""
    import numpy as np

    clean = _preprocess(text)
    if not clean:
        raise ValueError("empty_text_after_preprocessing")

    model = _load_model()
    audio = model.apply_tts(text=clean, speaker=speaker, sample_rate=sample_rate)
    arr = audio.numpy()
    peak = float(np.max(np.abs(arr))) or 1.0
    arr = (arr * 32767 / peak).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(arr.tobytes())
    return buf.getvalue()
