"""Транскрибация аудио через audiocpp_server на компьютере психолога.

Сервер (VPS) приводит аудио к WAV 16 кГц моно через ffmpeg (Telegram присылает OGG/Opus,
а audio.cpp принимает WAV) и отправляет файл на POST /v1/audio/transcriptions.
Если компьютер выключен, файл остаётся в очереди и отправляется повторно позже.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import httpx

from .config import settings


class TranscriberUnavailable(RuntimeError):
    pass


def to_wav(src: Path, dst: Path) -> Path:
    if src.suffix.lower() == ".wav":
        return src
    cmd = [settings.ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg не смог преобразовать файл: {res.stderr[-500:]}")
    return dst


def parse_transcript_response(resp: httpx.Response) -> str:
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        data = resp.json()
        if isinstance(data, dict):
            if isinstance(data.get("text"), str):
                return data["text"]
            segs = data.get("segments") or data.get("chunks")
            if isinstance(segs, list):
                return "\n".join(_segment_line(s) for s in segs if isinstance(s, dict))
        raise RuntimeError(f"Непонятный ответ транскрибатора: {str(data)[:300]}")
    return resp.text


def _segment_line(seg: dict) -> str:
    text = str(seg.get("text", "")).strip()
    start = seg.get("start")
    speaker = seg.get("speaker")
    prefix = ""
    if isinstance(start, (int, float)):
        m, s = divmod(int(start), 60)
        h, m = divmod(m, 60)
        prefix = f"[{h:02d}:{m:02d}:{s:02d}] "
    if speaker not in (None, ""):
        prefix += f"{speaker}: "
    return prefix + text


def transcribe_file(path: Path, client: httpx.Client | None = None) -> str:
    if not settings.asr_url:
        raise TranscriberUnavailable("Транскрибатор не настроен: укажите NAV_ASR_URL.")
    client = client or httpx.Client(timeout=httpx.Timeout(3600.0, connect=10.0))
    with tempfile.TemporaryDirectory() as tmp:
        wav = to_wav(path, Path(tmp) / "audio.wav")
        data = {}
        if settings.asr_model:
            data["model"] = settings.asr_model
        if settings.asr_language:
            data["language"] = settings.asr_language
        with open(wav, "rb") as f:
            try:
                resp = client.post(
                    settings.asr_url.rstrip("/") + "/v1/audio/transcriptions",
                    data=data, files={settings.asr_file_field: ("audio.wav", f, "audio/wav")})
            except httpx.ConnectError as e:
                raise TranscriberUnavailable(
                    "Компьютер с транскрибатором недоступен, попробую позже.") from e
    if resp.status_code >= 400:
        raise RuntimeError(f"Транскрибатор ответил {resp.status_code}: {resp.text[:500]}")
    return parse_transcript_response(resp).strip()
