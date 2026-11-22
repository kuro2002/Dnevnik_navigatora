import json

from fastapi.testclient import TestClient

from app import jobs
from app.main import create_app
from tests.conftest import FakeBackend

SESSION_ANSWER = {
    "synopsis": "Клиентка рассказала о неделе.",
    "fields": {"focus": "Привязанность", "agreements": "Вести дневник"},
    "observations": [{"kind": "difficulty", "observation": "Психолог быстро предложила решение",
                      "evidence": "«Давайте попробуем...»", "possible_meaning": "спешка",
                      "alternative": "клиентка сама просила совет", "question": "Что было бы, если подождать?"}],
    "clarifying_questions": ["Какая шкала была названа в начале?"],
}


def make_client(c, code="К-014"):
    r = c.post("/clients", data={"code": code, "client_type": "adult", "status": "active",
                                 "goal_words": "Перестать думать о бывшем",
                                 "goal_process": "Ослабить эмоциональную зависимость"},
               follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def make_session(c, cid, **extra):
    data = {"session_date": "2026-09-21", "number": "1", "methods": ["act", "hypnosis"],
            "reflection": "торопилась", "transcript": "Психолог: Здравствуйте.\nКлиент: Привет."}
    data.update(extra)
    r = c.post(f"/clients/{cid}/sessions", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_requires_login():
    app = create_app(start_background=False)
    with TestClient(app) as anon:
        r = anon.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert anon.post("/login", data={"password": "wrong"}).status_code == 401


def test_client_card_flow(client):
    cid = make_client(client)
    page = client.get(f"/clients/{cid}").text
    assert "К-014" in page and "Перестать думать о бывшем" in page
    # дубликат шифра
    r = client.post("/clients", data={"code": "К-014"})
    assert "уже занят" in r.text
    # шифр с пробелом не принимается
    assert "без пробелов" in client.post("/clients", data={"code": "Иван Петров"}).text
    # гипотеза и смена фазы попадают в карточку и хронологию
    client.post(f"/clients/{cid}/hypotheses", data={"text": "Страх одиночества", "grounds": "слова о пустоте"})
    client.post(f"/clients/{cid}/edit", data={"phase": "resource", "status": "active", "client_type": "adult"})
    assert "Страх одиночества" in client.get(f"/clients/{cid}").text
    assert "Фаза 2" in client.get(f"/clients/{cid}?tab=timeline").text


def test_markers_history(client):
    cid = make_client(client)
    client.post(f"/clients/{cid}/markers", data={"name": "Эмоциональная зависимость", "how": "шкала 0–10",
                                                 "start_value": "8", "start_date": "2026-09-01"})
    page = client.get(f"/clients/{cid}?tab=markers").text
    assert "Эмоциональная зависимость" in page
    from app.db import SessionLocal
    from app.models import Marker
    with SessionLocal() as db:
        mid = db.query(Marker).one().id
    client.post(f"/markers/{mid}/measure", data={"measured_on": "2026-09-21", "value": "5,5", "comment": "пример"})
    page = client.get(f"/clients/{cid}?tab=markers").text
    assert "8.0" in page and "5.5" in page and "<polyline" in page


def test_session_requires_explicit_date(client):
    cid = make_client(client)
    r = client.post(f"/clients/{cid}/sessions", data={"transcript": "текст"})
    assert "Укажите дату встречи" in r.text


def test_session_analysis_and_apply(client, monkeypatch):
    cid = make_client(client)
    sid = make_session(client, cid)
    fake = FakeBackend([json.dumps(SESSION_ANSWER, ensure_ascii=False)])
    monkeypatch.setattr(jobs, "get_backend", lambda name: fake)
    r = client.post(f"/sessions/{sid}/analyze/ollama", follow_redirects=False)
    assert r.status_code == 303
    page = client.get(f"/sessions/{sid}").text
    assert "Психолог быстро предложила решение" in page
    assert "Какая шкала была названа" in page

    from app.db import SessionLocal
    from app.models import Analysis, Session
    with SessionLocal() as db:
        aid = db.query(Analysis).one().id
    client.post(f"/analyses/{aid}/apply", data={"take": ["synopsis", "focus"]})
    with SessionLocal() as db:
        s = db.get(Session, sid)
        assert s.focus == "Привязанность"
        assert s.synopsis.startswith("Клиентка")
        assert s.agreements == ""  # не выбрано — не перенесено
        assert s.review_status == "review"
        assert s.reflection == "торопилась"  # поле психолога не тронуто


def test_claude_manual_roundtrip(client):
    cid = make_client(client)
    sid = make_session(client, cid)
    r = client.post(f"/sessions/{sid}/analyze/claude", follow_redirects=False)
    url = r.headers["location"]
    page = client.get(url).text
    assert "=== ИНСТРУКЦИЯ ===" in page and "К-014" in page and "Здравствуйте" in page
    bad = client.post(url + "/import", data={"answer": "извините, не могу"})
    assert "Не удалось прочитать ответ" in bad.text
    answer = "Вот разбор:\n```json\n" + json.dumps(SESSION_ANSWER, ensure_ascii=False) + "\n```"
    client.post(url + "/import", data={"answer": answer})
    page = client.get(url).text
    assert "Перенести в запись сессии" in page and "Клиентка рассказала" in page


def test_analyzer_unavailable_shows_error(client, monkeypatch):
    from app.analysis.backends import OllamaBackend
    import httpx

    def handler(req):
        raise httpx.ConnectError("off")
    monkeypatch.setattr(jobs, "get_backend",
                        lambda name: OllamaBackend(url="http://pc:1", client=httpx.Client(transport=httpx.MockTransport(handler))))
    cid = make_client(client)
    sid = make_session(client, cid)
    client.post(f"/sessions/{sid}/analyze/ollama")
    assert "Включён ли компьютер" in client.get(f"/sessions/{sid}").text


def test_dynamics(client, monkeypatch):
    cid = make_client(client)
    sid = make_session(client, cid)
    client.post(f"/sessions/{sid}", data={"session_date": "2026-09-21", "focus": "Привязанность",
                                          "review_status": "checked"})
    fake = FakeBackend([json.dumps({"summary": "Есть движение", "by_goal": [
        {"goal": "Перестать думать о бывшем", "direction": "улучшение", "changes": "реже думает",
         "evidence": "сессия 1"}]}, ensure_ascii=False)])
    monkeypatch.setattr(jobs, "get_backend", lambda name: fake)
    client.post(f"/clients/{cid}/dynamics/yandex")
    page = client.get(f"/clients/{cid}?tab=dynamics").text
    assert "Есть движение" in page and "реже думает" in page
    assert "Привязанность" in fake.calls[0][1]


def test_audio_upload_and_transcription(client, monkeypatch):
    cid = make_client(client)
    calls = []

    def fake_transcriber(path):
        calls.append(path)
        return "Клиент: расшифрованный текст"
    orig = jobs.run_transcription
    monkeypatch.setattr(jobs, "run_transcription", lambda aid: orig(aid, transcriber=fake_transcriber))
    r = client.post(f"/clients/{cid}/sessions", data={"session_date": "2026-09-21"},
                    files={"audio": ("voice.ogg", b"OggS....", "audio/ogg")}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert calls and calls[0].suffix == ".ogg"
    page = client.get(f"/sessions/{sid}").text
    assert "расшифрованный текст" in page and "Готово" in page


def test_audio_waits_when_pc_offline(client, monkeypatch):
    from app.transcription import TranscriberUnavailable
    cid = make_client(client)

    def offline(path):
        raise TranscriberUnavailable("Компьютер с транскрибатором недоступен, попробую позже.")
    orig = jobs.run_transcription
    monkeypatch.setattr(jobs, "run_transcription", lambda aid: orig(aid, transcriber=offline))
    r = client.post(f"/clients/{cid}/sessions", data={"session_date": "2026-09-21"},
                    files={"audio": ("voice.mp3", b"ID3", "audio/mpeg")}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert "Ждёт компьютер" in client.get(f"/sessions/{sid}").text


def test_print_and_dashboard(client):
    cid = make_client(client)
    make_session(client, cid)
    assert "сессий всего" in client.get("/").text
    page = client.get(f"/clients/{cid}/print?transcripts=1").text
    assert "Здравствуйте" in page and "Перестать думать о бывшем" in page


def test_delete_client_needs_code(client):
    cid = make_client(client)
    assert client.post(f"/clients/{cid}/delete", data={"confirm": "нет"}).status_code == 400
    client.post(f"/clients/{cid}/delete", data={"confirm": "К-014"})
    assert client.get(f"/clients/{cid}").status_code == 404


def test_upload_page_single_form(client, monkeypatch):
    cid = make_client(client)
    page = client.get("/upload").text
    assert "Загрузить запись сессии" in page and "К-014" in page
    assert 'href="/upload"' in client.get("/").text
    assert f'/upload?client_id={cid}' in client.get(f"/clients/{cid}").text
    # без клиента — понятная ошибка
    assert "Выберите клиента" in client.post("/upload", data={"session_date": "2026-09-21"}).text
    calls = []
    orig = jobs.run_transcription
    monkeypatch.setattr(jobs, "run_transcription",
                        lambda aid: orig(aid, transcriber=lambda p: calls.append(p) or "Клиент: текст речи"))
    r = client.post("/upload", data={"client_id": str(cid), "session_date": "2026-09-21"},
                    files={"recording": ("Запись.m4a", b"....", "audio/mp4")}, follow_redirects=False)
    assert r.status_code == 303
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert calls and "текст речи" in client.get(f"/sessions/{sid}").text


def test_upload_txt_goes_to_transcript_and_bad_ext_rejected(client):
    cid = make_client(client)
    r = client.post("/upload", data={"client_id": str(cid), "session_date": "2026-09-21"},
                    files={"recording": ("t.txt", "Психолог: привет".encode(), "text/plain")},
                    follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert "Психолог: привет" in client.get(f"/sessions/{sid}").text
    bad = client.post("/upload", data={"client_id": str(cid), "session_date": "2026-09-22"},
                      files={"recording": ("x.docx", b"PK", "application/octet-stream")})
    assert "не похож на аудио" in bad.text


def test_old_new_session_link_redirects(client):
    cid = make_client(client)
    r = client.get(f"/clients/{cid}/sessions/new", follow_redirects=False)
    assert r.headers["location"] == f"/upload?client_id={cid}"


def _orig_from_page(html):
    import html as h
    import re
    return h.unescape(re.search(r'name="_orig" value="([^"]*)"', html).group(1))


def test_stale_page_does_not_erase_new_transcript(client):
    """Повтор бага: страница открыта до окончания расшифровки, потом «Сохранить»."""
    cid = make_client(client)
    sid = make_session(client, cid, transcript="")
    orig = _orig_from_page(client.get(f"/sessions/{sid}").text)
    from app.db import SessionLocal
    from app.models import Session
    with SessionLocal() as db:  # фоновая расшифровка дописала текст
        db.get(Session, sid).transcript = "Клиент: длинная расшифровка"
        db.commit()
    client.post(f"/sessions/{sid}", data={"_orig": orig, "session_date": "2026-09-21", "number": "1",
                                          "transcript": "", "focus": "Новый фокус\r\n"})
    with SessionLocal() as db:
        s = db.get(Session, sid)
        assert s.transcript == "Клиент: длинная расшифровка"
        assert s.focus == "Новый фокус"
    assert "Пока страница была открыта" not in client.get(f"/sessions/{sid}").text


def test_both_changed_keeps_both_and_warns(client):
    cid = make_client(client)
    sid = make_session(client, cid, transcript="старый")
    orig = _orig_from_page(client.get(f"/sessions/{sid}").text)
    from app.db import SessionLocal
    from app.models import Session
    with SessionLocal() as db:
        db.get(Session, sid).transcript = "старый\n\nновая расшифровка"
        db.commit()
    page = client.post(f"/sessions/{sid}", data={"_orig": orig, "session_date": "2026-09-21",
                                                 "transcript": "старый с моей правкой"}).text
    with SessionLocal() as db:
        t = db.get(Session, sid).transcript
    assert "новая расшифровка" in t and "старый с моей правкой" in t and "Ваша правка" in t
    assert "Пока страница была открыта" in page and "Транскрипт" in page
    # предупреждение показывается один раз
    assert "Пока страница была открыта" not in client.get(f"/sessions/{sid}").text


def test_normal_edit_still_saves(client):
    cid = make_client(client)
    sid = make_session(client, cid)
    orig = _orig_from_page(client.get(f"/sessions/{sid}").text)
    client.post(f"/sessions/{sid}", data={"_orig": orig, "session_date": "2026-09-21",
                                          "transcript": "исправленный текст", "reflection": "заметка"})
    from app.db import SessionLocal
    from app.models import Session
    with SessionLocal() as db:
        s = db.get(Session, sid)
        assert s.transcript == "исправленный текст" and s.reflection == "заметка"


def test_transcript_locked_and_status_while_audio_pending(client, monkeypatch):
    from app.transcription import TranscriberUnavailable
    cid = make_client(client)

    def offline(path):
        raise TranscriberUnavailable("недоступен")
    orig = jobs.run_transcription
    monkeypatch.setattr(jobs, "run_transcription", lambda aid: orig(aid, transcriber=offline))
    r = client.post("/upload", data={"client_id": str(cid), "session_date": "2026-09-21"},
                    files={"recording": ("a.m4a", b"..", "audio/mp4")}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    page = client.get(f"/sessions/{sid}").text
    assert 'name="transcript" class="tall" readonly' in page
    assert f"/sessions/{sid}/status" in page
    st = client.get(f"/sessions/{sid}/status").json()
    assert st["pending_audio"] == 1 and st["version"]
