"""Разбор сессии и динамики: подготовка запроса, нарезка длинных транскриптов, разбор ответа."""
from __future__ import annotations

import json
import re

from ..models import SESSION_TEXT_FIELDS
from . import prompts
from .backends import LLMBackend


# --- Разбор ответа модели ---------------------------------------------------

def extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект из ответа (модели любят обрамлять его ```json ...```)."""
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("В ответе модели не найден JSON")
    return json.loads(text[start:end + 1])


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "\n".join(_as_str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_as_str(x)}" for k, x in v.items())
    return str(v).strip()


def normalize_session_result(data: dict) -> dict:
    """Приводит ответ к единому формату, чтобы разборы разных моделей были сравнимы."""
    fields_in = data.get("fields") or {}
    fields = {k: _as_str(fields_in.get(k, "")) for k in SESSION_TEXT_FIELDS}
    obs = []
    for o in _as_list(data.get("observations")):
        if isinstance(o, dict):
            obs.append({k: _as_str(o.get(k, "")) for k in
                        ("kind", "observation", "evidence", "possible_meaning", "alternative", "question")})
        else:
            obs.append({"kind": "neutral", "observation": _as_str(o), "evidence": "",
                        "possible_meaning": "", "alternative": "", "question": ""})
    return {
        "synopsis": _as_str(data.get("synopsis")),
        "fields": fields,
        "methods_observed": [
            {"method": _as_str(m.get("method")), "evidence": _as_str(m.get("evidence"))}
            if isinstance(m, dict) else {"method": _as_str(m), "evidence": ""}
            for m in _as_list(data.get("methods_observed"))],
        "observations": obs,
        "marker_evidence": [
            {"marker": _as_str(m.get("marker")), "evidence": _as_str(m.get("evidence")),
             "value": _as_str(m.get("value"))}
            for m in _as_list(data.get("marker_evidence")) if isinstance(m, dict)],
        "gaps": [_as_str(x) for x in _as_list(data.get("gaps"))],
        "contradictions": [_as_str(x) for x in _as_list(data.get("contradictions"))],
        "clarifying_questions": [_as_str(x) for x in _as_list(data.get("clarifying_questions"))],
        "asr_issues": [_as_str(x) for x in _as_list(data.get("asr_issues"))],
    }


def _norm_obs_list(v, with_kind=False):
    keys = ("observation", "evidence", "possible_meaning", "alternative", "question")
    if with_kind:
        keys = ("kind",) + keys
    out = []
    for o in _as_list(v):
        if isinstance(o, dict):
            out.append({k: _as_str(o.get(k, "")) for k in keys})
        else:
            out.append({k: (_as_str(o) if k == "observation" else "") for k in keys})
    return out


def normalize_dynamics_result(data: dict) -> dict:
    return {
        "summary": _as_str(data.get("summary")),
        "by_goal": [
            {k: _as_str(g.get(k, "")) for k in ("goal", "direction", "changes", "evidence", "contrary")}
            for g in _as_list(data.get("by_goal")) if isinstance(g, dict)],
        "interaction": _norm_obs_list(data.get("interaction")),
        "psychologist_skills": _norm_obs_list(data.get("psychologist_skills"), with_kind=True),
        "regress_or_risks": [_as_str(x) for x in _as_list(data.get("regress_or_risks"))],
        "data_gaps": [_as_str(x) for x in _as_list(data.get("data_gaps"))],
        "supervision_questions": [_as_str(x) for x in _as_list(data.get("supervision_questions"))],
    }


# --- Нарезка длинных транскриптов ------------------------------------------

def split_text(text: str, max_chars: int) -> list[str]:
    """Режет по абзацам/строкам так, чтобы куски не превышали max_chars."""
    if len(text) <= max_chars:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:  # очень длинная строка без переносов
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if len(cur) + len(line) > max_chars and cur:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks


# --- Запуск ---------------------------------------------------------------

def previous_sessions(session, limit: int = 2):
    prev = [s for s in session.client.sessions
            if s.id != session.id and s.session_date <= session.session_date]
    return prev[-limit:]


def build_session_prompt(session, transcript: str | None = None, notes: str | None = None):
    """Возвращает (system, user) для разбора сессии."""
    body = notes if notes is not None else (transcript if transcript is not None else session.transcript)
    return (prompts.session_system_prompt(),
            prompts.session_user_prompt(session, body, is_notes=notes is not None,
                                        previous_sessions=previous_sessions(session)))


def analyze_session(session, backend: LLMBackend, chunk_chars: int) -> tuple[dict, str]:
    """Разбор сессии. Длинный транскрипт сначала конспектируется по частям.
    Возвращает (нормализованный результат, сырой текст ответа)."""
    transcript = session.transcript or ""
    if not transcript.strip():
        raise ValueError("У сессии нет транскрипта")
    chunks = split_text(transcript, chunk_chars)
    notes = None
    if len(chunks) > 1:
        parts = []
        for i, ch in enumerate(chunks, 1):
            part = backend.complete(prompts.CHUNK_SYSTEM,
                                    prompts.chunk_user_prompt(i, len(chunks), ch), want_json=False)
            parts.append(f"--- Часть {i} из {len(chunks)} ---\n{part.strip()}")
        notes = "\n\n".join(parts)
    system, user = build_session_prompt(session, notes=notes) if notes else build_session_prompt(session)
    raw = backend.complete(system, user, want_json=True)
    return normalize_session_result(extract_json(raw)), raw


def build_dynamics_prompt(client, sessions):
    return prompts.dynamics_system_prompt(), prompts.dynamics_user_prompt(client, sessions)


def analyze_dynamics(client, sessions, backend: LLMBackend) -> tuple[dict, str]:
    if not sessions:
        raise ValueError("Нет сессий для анализа динамики")
    system, user = build_dynamics_prompt(client, sessions)
    raw = backend.complete(system, user, want_json=True)
    return normalize_dynamics_result(extract_json(raw)), raw


# --- Ручной обмен с Claude ------------------------------------------------

CLAUDE_HEADER = (
    "Ниже инструкция и материалы для разбора. Выполни инструкцию и ответь одним JSON-объектом "
    "в блоке ```json```. Ответ будет вставлен обратно в систему автоматически, поэтому не "
    "меняй названия ключей.\n\n"
)


def claude_export_text(system: str, user: str) -> str:
    return CLAUDE_HEADER + "=== ИНСТРУКЦИЯ ===\n" + system + "\n\n=== МАТЕРИАЛЫ ===\n" + user
