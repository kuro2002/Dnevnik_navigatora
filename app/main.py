"""Веб-приложение Дневника Навигатора."""
from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import models
from .config import settings
from .db import init_engine

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals.update(
    CLIENT_TYPES=models.CLIENT_TYPES, CLIENT_STATUSES=models.CLIENT_STATUSES, PHASES=models.PHASES,
    WHO=models.WHO, GOAL_STATUSES=models.GOAL_STATUSES, HYPOTHESIS_STATUSES=models.HYPOTHESIS_STATUSES,
    MEASURE_SOURCES=models.MEASURE_SOURCES, METHODS=models.METHODS, REVIEW_STATUSES=models.REVIEW_STATUSES,
    ANALYZERS=models.ANALYZERS, ANALYSIS_STATUSES=models.ANALYSIS_STATUSES,
    TRANSCRIPTION_STATUSES=models.TRANSCRIPTION_STATUSES,
    SESSION_TEXT_FIELDS=models.SESSION_TEXT_FIELDS, SESSION_SELF_FIELDS=models.SESSION_SELF_FIELDS,
)


def _fmt_date(d):
    return d.strftime("%d.%m.%Y") if d else ""


def _fmt_dt(d):
    return d.strftime("%d.%m.%Y %H:%M") if d else ""


templates.env.filters["d"] = _fmt_date
templates.env.filters["dt"] = _fmt_dt
templates.env.filters["fromjson"] = lambda v: json.loads(v) if v else {}

PUBLIC_PATHS = ("/login", "/static", "/health")


def create_app(start_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        init_engine()
        if start_background:
            from .jobs import start_retry_loop
            start_retry_loop()
        yield

    app = FastAPI(title="Дневник Навигатора", lifespan=lifespan)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not path.startswith(PUBLIC_PATHS) and not request.session.get("auth"):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    # SessionMiddleware добавляется последним, чтобы выполняться первым.
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key,
                       session_cookie="nav_session", max_age=60 * 60 * 24 * 14, same_site="lax")
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/login")
    def login_form(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        pw = str(form.get("password", ""))
        if settings.password and hmac.compare_digest(pw, settings.password):
            request.session["auth"] = True
            return RedirectResponse("/", status_code=303)
        err = "Неверный пароль" if settings.password else "Пароль не задан: укажите NAV_PASSWORD"
        return templates.TemplateResponse(request, "login.html", {"error": err}, status_code=401)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    from .routes import router
    app.include_router(router)
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
