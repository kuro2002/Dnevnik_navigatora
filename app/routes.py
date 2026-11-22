"""Страницы сайта: клиенты, цели, гипотезы, маркеры, сессии, разборы."""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from . import jobs
from .analysis import pipeline
from .config import settings
from .db import get_db
from .models import (
    ANALYZERS, METHODS, SESSION_SELF_FIELDS, SESSION_TEXT_FIELDS, Analysis, AudioFile, Client,
    ClientEvent, Goal, Hypothesis, Marker, Measurement, Session, PHASES, CLIENT_STATUSES,
)

router = APIRouter()

AUDIO_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".aac", ".webm", ".mp4"}


def render(request: Request, name: str, **ctx):
    from .main import templates
    return templates.TemplateResponse(request, name, ctx)


def back(url: str):
    return RedirectResponse(url, status_code=303)


def parse_date(v, required=False) -> date | None:
    v = (v or "").strip()
    if not v:
        if required:
            raise HTTPException(400, "Дата обязательна")
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    raise HTTPException(400, f"Не понимаю дату «{v}»")


def parse_float(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        raise HTTPException(400, f"Значение «{v}» не число")


def s(form, key) -> str:
    return str(form.get(key, "") or "").strip()


def get_or_404(db: DB, model, id_: int):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, "Не найдено")
    return obj


def log_event(db: DB, client: Client, kind: str, text: str, when: date | None = None):
    db.add(ClientEvent(client_id=client.id, happened_on=when or date.today(), kind=kind, text=text))


# --- Главная ---------------------------------------------------------------

@router.get("/")
def dashboard(request: Request, db: DB = Depends(get_db)):
    clients = db.scalars(select(Client).order_by(Client.code)).all()
    month_start = date.today().replace(day=1)
    stats = {
        "clients": len(clients),
        "active": sum(1 for c in clients if c.status == "active"),
        "sessions": db.scalar(select(func.count(Session.id))) or 0,
        "sessions_month": db.scalar(select(func.count(Session.id)).where(Session.session_date >= month_start)) or 0,
        "to_review": db.scalar(select(func.count(Session.id)).where(Session.review_status != "checked")) or 0,
        "analyzed": db.scalar(select(func.count(func.distinct(Analysis.session_id))).where(
            Analysis.kind == "session", Analysis.status == "done")) or 0,
    }
    return render(request, "dashboard.html", clients=clients, stats=stats)


# --- Клиенты ---------------------------------------------------------------

CODE_RE = re.compile(r"^[\wА-Яа-яЁё\-]{1,32}$")


@router.get("/clients/new")
def client_new(request: Request):
    return render(request, "client_form.html", client=None, error=None)


@router.post("/clients")
async def client_create(request: Request, db: DB = Depends(get_db)):
    form = await request.form()
    code = s(form, "code")
    if not CODE_RE.match(code):
        return render(request, "client_form.html", client=None,
                      error="Шифр: буквы, цифры и дефис, без пробелов (например, К-014)")
    c = Client(code=code, client_type=s(form, "client_type") or "adult",
               status=s(form, "status") or "active", phase=s(form, "phase"),
               started_on=parse_date(form.get("started_on")), context=s(form, "context"),
               contract=s(form, "contract"), intake=s(form, "intake"), notes=s(form, "notes"))
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return render(request, "client_form.html", client=None, error=f"Шифр {code} уже занят")
    log_event(db, c, "status", f"Карточка создана, статус: {CLIENT_STATUSES.get(c.status, c.status)}",
              c.started_on)
    if s(form, "goal_words"):
        db.add(Goal(client_id=c.id, who="client", words=s(form, "goal_words"),
                    process=s(form, "goal_process"), created_on=c.started_on or date.today()))
    db.commit()
    return back(f"/clients/{c.id}")


@router.get("/clients/{cid}")
def client_view(cid: int, request: Request, tab: str = "overview", db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    dynamics = [a for a in db.scalars(select(Analysis).where(
        Analysis.client_id == cid, Analysis.kind == "dynamics").order_by(Analysis.id.desc())).all()]
    return render(request, "client.html", client=c, tab=tab, dynamics=dynamics,
                  timeline=build_timeline(c), today=date.today())


@router.post("/clients/{cid}/edit")
async def client_edit(cid: int, request: Request, db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    form = await request.form()
    new_phase, new_status = s(form, "phase"), s(form, "status") or c.status
    if new_phase != c.phase:
        log_event(db, c, "phase", f"Фаза: {PHASES.get(new_phase, new_phase)}")
    if new_status != c.status:
        log_event(db, c, "status", f"Статус: {CLIENT_STATUSES.get(new_status, new_status)}")
    c.phase, c.status = new_phase, new_status
    c.client_type = s(form, "client_type") or c.client_type
    c.started_on = parse_date(form.get("started_on"))
    for key in ("context", "contract", "intake", "status_reason", "next_contact", "notes"):
        setattr(c, key, s(form, key))
    db.commit()
    return back(f"/clients/{cid}")


@router.post("/clients/{cid}/delete")
async def client_delete(cid: int, request: Request, db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    form = await request.form()
    if s(form, "confirm") != c.code:
        raise HTTPException(400, "Для удаления введите шифр клиента")
    for s_ in c.sessions:
        for af in s_.audio_files:
            (settings.audio_dir / af.stored_name).unlink(missing_ok=True)
    db.delete(c)
    db.commit()
    return back("/")


# --- Цели, гипотезы, маркеры ------------------------------------------------

@router.post("/clients/{cid}/goals")
async def goal_add(cid: int, request: Request, db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    form = await request.form()
    if not s(form, "words"):
        raise HTTPException(400, "Нужна формулировка запроса")
    g = Goal(client_id=cid, who=s(form, "who") or "client", words=s(form, "words"),
             process=s(form, "process"), review_on=parse_date(form.get("review_on")))
    db.add(g)
    log_event(db, c, "goal", f"Добавлена цель: {g.words}")
    db.commit()
    return back(f"/clients/{cid}")


@router.post("/goals/{gid}/edit")
async def goal_edit(gid: int, request: Request, db: DB = Depends(get_db)):
    g = get_or_404(db, Goal, gid)
    form = await request.form()
    old_status = g.status
    g.words, g.process, g.note = s(form, "words") or g.words, s(form, "process"), s(form, "note")
    g.status = s(form, "status") or g.status
    g.review_on = parse_date(form.get("review_on"))
    if g.status != old_status:
        log_event(db, g.client, "goal", f"Цель «{g.words}»: {old_status} → {g.status}"
                  + (f" ({g.note})" if g.note else ""))
    db.commit()
    return back(f"/clients/{g.client_id}")


@router.post("/clients/{cid}/hypotheses")
async def hypothesis_add(cid: int, request: Request, db: DB = Depends(get_db)):
    get_or_404(db, Client, cid)
    form = await request.form()
    if not s(form, "text"):
        raise HTTPException(400, "Нужен текст гипотезы")
    db.add(Hypothesis(client_id=cid, text=s(form, "text"), grounds=s(form, "grounds")))
    db.commit()
    return back(f"/clients/{cid}")


@router.post("/hypotheses/{hid}/edit")
async def hypothesis_edit(hid: int, request: Request, db: DB = Depends(get_db)):
    h = get_or_404(db, Hypothesis, hid)
    form = await request.form()
    h.text = s(form, "text") or h.text
    h.grounds, h.contrary = s(form, "grounds"), s(form, "contrary")
    h.status = s(form, "status") or h.status
    db.commit()
    return back(f"/clients/{h.client_id}")


@router.post("/clients/{cid}/markers")
async def marker_add(cid: int, request: Request, db: DB = Depends(get_db)):
    get_or_404(db, Client, cid)
    form = await request.form()
    if not s(form, "name"):
        raise HTTPException(400, "Нужно название маркера")
    goal_id = int(form["goal_id"]) if s(form, "goal_id") else None
    m = Marker(client_id=cid, name=s(form, "name"), how=s(form, "how") or "шкала 0–10",
               anchors=s(form, "anchors"), lower_is_better=bool(form.get("lower_is_better")),
               goal_id=goal_id)
    db.add(m)
    db.flush()
    if s(form, "start_value"):
        db.add(Measurement(marker_id=m.id, measured_on=parse_date(form.get("start_date")) or date.today(),
                           value=parse_float(form["start_value"]), source=s(form, "source") or "client",
                           comment="Старт"))
    db.commit()
    return back(f"/clients/{cid}?tab=markers")


@router.post("/markers/{mid}/measure")
async def marker_measure(mid: int, request: Request, db: DB = Depends(get_db)):
    m = get_or_404(db, Marker, mid)
    form = await request.form()
    session_id = int(form["session_id"]) if s(form, "session_id") else None
    db.add(Measurement(marker_id=mid, measured_on=parse_date(form.get("measured_on"), required=True),
                       value=parse_float(form.get("value", "")), source=s(form, "source") or "client",
                       comment=s(form, "comment"), session_id=session_id))
    db.commit()
    nxt = s(form, "next")
    return back(nxt if nxt.startswith("/") else f"/clients/{m.client_id}?tab=markers")


@router.post("/measurements/{msid}/delete")
def measurement_delete(msid: int, db: DB = Depends(get_db)):
    ms = get_or_404(db, Measurement, msid)
    cid = ms.marker.client_id
    db.delete(ms)
    db.commit()
    return back(f"/clients/{cid}?tab=markers")


# --- Сессии ------------------------------------------------------------------

@router.get("/clients/{cid}/sessions/new")
def session_new(cid: int, request: Request, db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    next_number = max([x.number for x in c.sessions], default=0) + 1
    return render(request, "session_new.html", client=c, next_number=next_number, error=None)


@router.post("/clients/{cid}/sessions")
async def session_create(cid: int, request: Request, background: BackgroundTasks,
                         db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    form = await request.form()
    next_number = max([x.number for x in c.sessions], default=0) + 1
    if not s(form, "session_date"):
        return render(request, "session_new.html", client=c, next_number=next_number,
                      error="Укажите дату встречи — она не подставляется автоматически.")
    transcript = s(form, "transcript")
    tfile = form.get("transcript_file")
    if tfile is not None and getattr(tfile, "filename", ""):
        raw = await tfile.read()
        transcript = (transcript + "\n\n" if transcript else "") + raw.decode("utf-8", errors="replace")
    number = int(s(form, "number") or next_number)
    ses = Session(client_id=cid, number=number, session_date=parse_date(form.get("session_date"), True),
                  duration_min=int(s(form, "duration_min")) if s(form, "duration_min") else None,
                  methods=",".join(m for m in form.getlist("methods") if m in METHODS),
                  transcript=transcript, reflection=s(form, "reflection"))
    db.add(ses)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return render(request, "session_new.html", client=c, next_number=next_number,
                      error=f"Сессия №{number} у {c.code} уже есть")
    audio = form.get("audio")
    audio_id = None
    if audio is not None and getattr(audio, "filename", ""):
        audio_id = await _store_audio(db, ses, audio)
    if c.status in ("inquiry", "booked"):
        c.status = "active"
        log_event(db, c, "status", "Статус: В работе", ses.session_date)
    db.commit()
    if audio_id:
        background.add_task(jobs.run_transcription, audio_id)
    return back(f"/sessions/{ses.id}")


async def _store_audio(db: DB, ses: Session, upload) -> int:
    ext = Path(upload.filename).suffix.lower()
    if ext not in AUDIO_EXT:
        raise HTTPException(400, f"Неизвестный формат аудио: {ext}")
    stored = f"{uuid.uuid4().hex}{ext}"
    with open(settings.audio_dir / stored, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            f.write(chunk)
    af = AudioFile(session_id=ses.id, filename=upload.filename, stored_name=stored)
    db.add(af)
    db.flush()
    return af.id


@router.get("/sessions/{sid}")
def session_view(sid: int, request: Request, db: DB = Depends(get_db)):
    ses = get_or_404(db, Session, sid)
    analyses = [(a, json.loads(a.result_json) if a.result_json else None) for a in ses.analyses]
    return render(request, "session.html", s=ses, client=ses.client, analyses=analyses)


@router.post("/sessions/{sid}")
async def session_save(sid: int, request: Request, db: DB = Depends(get_db)):
    ses = get_or_404(db, Session, sid)
    form = await request.form()
    ses.session_date = parse_date(form.get("session_date"), required=True)
    ses.number = int(s(form, "number") or ses.number)
    ses.duration_min = int(s(form, "duration_min")) if s(form, "duration_min") else None
    ses.methods = ",".join(m for m in form.getlist("methods") if m in METHODS)
    for key in ("synopsis", "transcript", *SESSION_TEXT_FIELDS, *SESSION_SELF_FIELDS):
        if key in form:
            setattr(ses, key, s(form, key))
    ses.review_status = s(form, "review_status") or ses.review_status
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Сессия с таким номером у клиента уже есть")
    return back(f"/sessions/{sid}")


@router.post("/sessions/{sid}/audio")
async def session_audio(sid: int, request: Request, background: BackgroundTasks,
                        db: DB = Depends(get_db)):
    ses = get_or_404(db, Session, sid)
    form = await request.form()
    audio = form.get("audio")
    if audio is None or not getattr(audio, "filename", ""):
        raise HTTPException(400, "Выберите аудиофайл")
    audio_id = await _store_audio(db, ses, audio)
    db.commit()
    background.add_task(jobs.run_transcription, audio_id)
    return back(f"/sessions/{sid}")


@router.post("/audio/{aid}/retry")
def audio_retry(aid: int, background: BackgroundTasks, db: DB = Depends(get_db)):
    af = get_or_404(db, AudioFile, aid)
    af.status = "queued"
    db.commit()
    background.add_task(jobs.run_transcription, aid)
    return back(f"/sessions/{af.session_id}")


@router.post("/sessions/{sid}/delete")
async def session_delete(sid: int, db: DB = Depends(get_db)):
    ses = get_or_404(db, Session, sid)
    cid = ses.client_id
    for af in ses.audio_files:
        (settings.audio_dir / af.stored_name).unlink(missing_ok=True)
    db.delete(ses)
    db.commit()
    return back(f"/clients/{cid}?tab=sessions")


# --- Разборы ---------------------------------------------------------------

@router.post("/sessions/{sid}/analyze/{analyzer}")
def session_analyze(sid: int, analyzer: str, background: BackgroundTasks, db: DB = Depends(get_db)):
    ses = get_or_404(db, Session, sid)
    if analyzer not in ANALYZERS:
        raise HTTPException(400, "Неизвестный разборщик")
    if not ses.transcript.strip():
        raise HTTPException(400, "Сначала добавьте транскрипт")
    a = Analysis(kind="session", client_id=ses.client_id, session_id=sid, analyzer=analyzer)
    if analyzer == "claude":
        a.status = "waiting_import"
        db.add(a)
        db.commit()
        return back(f"/analyses/{a.id}")
    db.add(a)
    db.commit()
    background.add_task(jobs.run_analysis, a.id)
    return back(f"/sessions/{sid}#analyses")


@router.post("/clients/{cid}/dynamics/{analyzer}")
def client_dynamics(cid: int, analyzer: str, background: BackgroundTasks, db: DB = Depends(get_db)):
    c = get_or_404(db, Client, cid)
    if analyzer not in ANALYZERS:
        raise HTTPException(400, "Неизвестный разборщик")
    if not jobs.dynamics_sessions(c):
        raise HTTPException(400, "Нет заполненных сессий для анализа динамики")
    a = Analysis(kind="dynamics", client_id=cid, analyzer=analyzer)
    if analyzer == "claude":
        a.status = "waiting_import"
        db.add(a)
        db.commit()
        return back(f"/analyses/{a.id}")
    db.add(a)
    db.commit()
    background.add_task(jobs.run_analysis, a.id)
    return back(f"/clients/{cid}?tab=dynamics")


def claude_export(a: Analysis) -> str:
    if a.kind == "session":
        system, user = pipeline.build_session_prompt(a.session)
    else:
        system, user = pipeline.build_dynamics_prompt(a.client, jobs.dynamics_sessions(a.client))
    return pipeline.claude_export_text(system, user)


@router.get("/analyses/{aid}")
def analysis_view(aid: int, request: Request, db: DB = Depends(get_db)):
    a = get_or_404(db, Analysis, aid)
    result = json.loads(a.result_json) if a.result_json else None
    export = claude_export(a) if a.analyzer == "claude" else None
    return render(request, "analysis.html", a=a, result=result, export=export, error=None)


@router.post("/analyses/{aid}/import")
async def analysis_import(aid: int, request: Request, db: DB = Depends(get_db)):
    a = get_or_404(db, Analysis, aid)
    form = await request.form()
    try:
        jobs.import_claude_answer(db, a, s(form, "answer"))
    except (ValueError, json.JSONDecodeError) as e:
        return render(request, "analysis.html", a=a, result=None, export=claude_export(a),
                      error=f"Не удалось прочитать ответ: {e}. Скопируйте ответ целиком, вместе с блоком JSON.")
    return back(f"/analyses/{aid}")


@router.post("/analyses/{aid}/retry")
def analysis_retry(aid: int, background: BackgroundTasks, db: DB = Depends(get_db)):
    a = get_or_404(db, Analysis, aid)
    if a.analyzer == "claude":
        return back(f"/analyses/{aid}")
    a.status = "queued"
    db.commit()
    background.add_task(jobs.run_analysis, aid)
    return back(f"/analyses/{aid}")


@router.post("/analyses/{aid}/delete")
def analysis_delete(aid: int, db: DB = Depends(get_db)):
    a = get_or_404(db, Analysis, aid)
    url = f"/sessions/{a.session_id}" if a.session_id else f"/clients/{a.client_id}?tab=dynamics"
    db.delete(a)
    db.commit()
    return back(url)


@router.post("/analyses/{aid}/apply")
async def analysis_apply(aid: int, request: Request, db: DB = Depends(get_db)):
    """Переносит выбранные психологом поля из разбора в запись сессии."""
    a = get_or_404(db, Analysis, aid)
    if a.kind != "session" or not a.result_json:
        raise HTTPException(400, "Этот разбор нельзя перенести в сессию")
    form = await request.form()
    chosen = set(form.getlist("take"))
    result = json.loads(a.result_json)
    ses = a.session
    if "synopsis" in chosen:
        ses.synopsis = result.get("synopsis", "")
    for key in SESSION_TEXT_FIELDS:
        if key in chosen:
            setattr(ses, key, result["fields"].get(key, ""))
    if chosen:
        a.applied = True
        if ses.review_status == "draft":
            ses.review_status = "review"
    db.commit()
    return back(f"/sessions/{ses.id}")


# --- Хронология и выгрузка --------------------------------------------------

def build_timeline(c: Client) -> list[dict]:
    items = []
    for e in c.events:
        items.append({"date": e.happened_on, "kind": e.kind, "text": e.text, "url": None})
    for ses in c.sessions:
        text = f"Сессия №{ses.number}"
        if ses.method_labels:
            text += f" ({ses.method_labels})"
        if ses.focus:
            text += f": {ses.focus}"
        items.append({"date": ses.session_date, "kind": "session", "text": text, "url": f"/sessions/{ses.id}"})
    for m in c.markers:
        for ms in m.measurements:
            items.append({"date": ms.measured_on, "kind": "measure",
                          "text": f"{m.name}: {ms.value:g}" + (f" — {ms.comment}" if ms.comment else ""),
                          "url": None})
    items.sort(key=lambda x: (x["date"], x["kind"] != "status"))
    return items


@router.get("/clients/{cid}/print")
def client_print(cid: int, request: Request, date_from: str = "", date_to: str = "",
                 transcripts: int = 0, db: DB = Depends(get_db)):
    """Печатная версия для супервизии: сохраняется в PDF через «Печать» в браузере."""
    c = get_or_404(db, Client, cid)
    d1, d2 = parse_date(date_from), parse_date(date_to)
    sessions = [x for x in c.sessions if (not d1 or x.session_date >= d1) and (not d2 or x.session_date <= d2)]
    return render(request, "client_print.html", client=c, sessions=sessions, d1=d1, d2=d2,
                  transcripts=bool(transcripts), timeline=build_timeline(c), today=date.today())
