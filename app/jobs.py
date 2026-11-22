"""Фоновые задачи: разборы моделями и транскрибация."""
from __future__ import annotations

import json
import logging
import threading
import time

from .analysis import pipeline
from .analysis.backends import AnalyzerUnavailable, get_backend
from .analysis.prompts import DYNAMICS_PROMPT_VERSION, PROMPT_VERSION
from .config import settings
from .db import SessionLocal
from .models import Analysis, AudioFile, Client, Session, now
from .transcription import TranscriberUnavailable, transcribe_file

log = logging.getLogger("navigator.jobs")
_lock = threading.Lock()


def dynamics_sessions(client: Client) -> list[Session]:
    """Для динамики берём проверенные сессии, а если их нет — все с заполненными полями."""
    checked = [s for s in client.sessions if s.review_status == "checked"]
    return checked or [s for s in client.sessions if s.synopsis or s.focus or s.conclusions]


def run_analysis(analysis_id: int, backend=None) -> None:
    db = SessionLocal()
    try:
        a = db.get(Analysis, analysis_id)
        if a is None or a.status not in ("queued", "error"):
            return
        a.status, a.error = "running", ""
        db.commit()
        backend = backend or get_backend(a.analyzer)
        a.model = getattr(backend, "model", "")
        try:
            if a.kind == "session":
                result, raw = pipeline.analyze_session(a.session, backend, settings.chunk_chars)
                a.prompt_version = PROMPT_VERSION
            else:
                result, raw = pipeline.analyze_dynamics(a.client, dynamics_sessions(a.client), backend)
                a.prompt_version = DYNAMICS_PROMPT_VERSION
            a.result_json = json.dumps(result, ensure_ascii=False)
            a.raw_text = raw
            a.status = "done"
        except AnalyzerUnavailable as e:
            a.status, a.error = "error", str(e)
        except Exception as e:  # noqa: BLE001 — показываем ошибку психологу на странице
            log.exception("analysis %s failed", analysis_id)
            a.status, a.error = "error", f"{type(e).__name__}: {e}"
        a.finished_at = now()
        db.commit()
    finally:
        db.close()


def import_claude_answer(db, analysis: Analysis, text: str) -> None:
    data = pipeline.extract_json(text)
    if analysis.kind == "session":
        result = pipeline.normalize_session_result(data)
        analysis.prompt_version = PROMPT_VERSION
    else:
        result = pipeline.normalize_dynamics_result(data)
        analysis.prompt_version = DYNAMICS_PROMPT_VERSION
    analysis.result_json = json.dumps(result, ensure_ascii=False)
    analysis.raw_text = text
    analysis.model = "claude (вручную)"
    analysis.status = "done"
    analysis.error = ""
    analysis.finished_at = now()
    db.commit()


def run_transcription(audio_id: int, transcriber=transcribe_file) -> None:
    with _lock:
        db = SessionLocal()
        try:
            af = db.get(AudioFile, audio_id)
            if af is None or af.status == "done":
                return
            af.status, af.error = "running", ""
            db.commit()
            try:
                text = transcriber(settings.audio_dir / af.stored_name)
                s = af.session
                sep = "\n\n" if s.transcript.strip() else ""
                s.transcript = s.transcript + sep + text
                af.status = "done"
                af.finished_at = now()
            except TranscriberUnavailable as e:
                af.status, af.error = "queued", str(e)
            except Exception as e:  # noqa: BLE001
                log.exception("transcription %s failed", audio_id)
                af.status, af.error = "error", f"{type(e).__name__}: {e}"
            db.commit()
        finally:
            db.close()


def retry_queued_transcriptions() -> None:
    db = SessionLocal()
    try:
        ids = [a.id for a in db.query(AudioFile).filter(AudioFile.status == "queued").all()]
    finally:
        db.close()
    for i in ids:
        run_transcription(i)


def start_retry_loop() -> threading.Thread:
    def loop():
        while True:
            time.sleep(max(1, settings.asr_retry_minutes) * 60)
            try:
                retry_queued_transcriptions()
            except Exception:  # noqa: BLE001
                log.exception("retry loop")
    t = threading.Thread(target=loop, daemon=True, name="asr-retry")
    t.start()
    return t
