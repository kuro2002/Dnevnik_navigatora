import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.analysis import pipeline
from tests.conftest import FakeBackend


def test_extract_json_handles_fences_and_think():
    text = "<think>hmm</think>Вот ответ:\n```json\n{\"synopsis\": \"ok\"}\n```\nГотово"
    assert pipeline.extract_json(text) == {"synopsis": "ok"}


def test_extract_json_raises_without_object():
    with pytest.raises(ValueError):
        pipeline.extract_json("нет json")


def test_normalize_session_fills_missing_keys_and_coerces():
    r = pipeline.normalize_session_result({
        "fields": {"focus": ["a", "b"], "unknown": "x"},
        "observations": ["просто строка", {"kind": "difficulty", "observation": "o", "evidence": "e"}],
        "gaps": "одна строка",
    })
    assert r["fields"]["focus"] == "a\nb"
    assert "unknown" not in r["fields"]
    assert r["fields"]["agreements"] == ""
    assert r["observations"][0]["observation"] == "просто строка"
    assert r["observations"][1]["kind"] == "difficulty"
    assert r["gaps"] == ["одна строка"]
    assert r["clarifying_questions"] == []


def test_split_text_respects_limit():
    text = "\n".join(f"строка {i} " + "x" * 50 for i in range(100))
    chunks = pipeline.split_text(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks) == text


def _fake_session(transcript):
    client = SimpleNamespace(code="К-001", goals=[], hypotheses=[], markers=[], sessions=[])
    ses = SimpleNamespace(id=1, number=1, session_date=date(2026, 9, 21), method_codes=["act"],
                          reflection="торопилась", transcript=transcript, client=client)
    client.sessions = [ses]
    return ses


def test_analyze_session_single_call():
    be = FakeBackend([json.dumps({"synopsis": "s", "fields": {"focus": "f"}})])
    result, raw = pipeline.analyze_session(_fake_session("Клиент: привет"), be, 10_000)
    assert result["synopsis"] == "s"
    assert len(be.calls) == 1
    system, user, want_json = be.calls[0]
    assert "К-001" in user and "21.09.2026" in user and "ACT" in user and "торопилась" in user
    assert want_json


def test_analyze_session_chunks_long_transcript():
    transcript = "\n".join("реплика " + "x" * 90 for _ in range(30))
    n_chunks = len(pipeline.split_text(transcript, 1000))
    assert n_chunks > 1
    be = FakeBackend([f"конспект {i}" for i in range(n_chunks)] + [json.dumps({"synopsis": "итог"})])
    result, _ = pipeline.analyze_session(_fake_session(transcript), be, 1000)
    assert len(be.calls) == n_chunks + 1
    assert all(not c[2] for c in be.calls[:-1])
    assert "конспект" in be.calls[-1][1]
    assert result["synopsis"] == "итог"


def test_analyze_session_without_transcript():
    with pytest.raises(ValueError):
        pipeline.analyze_session(_fake_session("  "), FakeBackend([]), 1000)


def test_normalize_dynamics():
    r = pipeline.normalize_dynamics_result({"by_goal": [{"goal": "g", "direction": "улучшение"}],
                                            "psychologist_skills": [{"kind": "growth", "observation": "o"}]})
    assert r["by_goal"][0]["changes"] == ""
    assert r["psychologist_skills"][0]["kind"] == "growth"
    assert r["supervision_questions"] == []
