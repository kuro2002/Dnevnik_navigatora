"""Модель данных Дневника Навигатора.

Принципы:
- клиент идентифицируется только шифром (например, «К-014»), без ФИО;
- дата встречи и дата загрузки — разные поля, дата встречи вводится явно;
- у маркеров хранится история замеров, а не только «старт / текущий»;
- ИИ-разборы хранятся отдельно от записи сессии и попадают в неё
  только после проверки психологом.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


# --- Справочники значений (код -> подпись) -------------------------------

CLIENT_TYPES = {
    "adult": "Взрослый",
    "couple": "Пара",
    "teen": "Подросток",
    "parent": "Родитель",
    "parent_child": "Родитель и ребёнок",
}

CLIENT_STATUSES = {
    "inquiry": "Обращение (ещё не в работе)",
    "booked": "Записан(а)",
    "active": "В работе",
    "pause": "Пауза",
    "finished": "Завершил(а) работу",
    "stopped": "Прекратил(а) без завершения",
}

PHASES = {
    "": "Не указана",
    "mapping": "Фаза 1 — Картирование",
    "resource": "Фаза 2 — Ресурс",
    "integration": "Фаза 3 — Интеграция",
}

WHO = {
    "client": "Клиент",
    "partner1": "Партнёр 1",
    "partner2": "Партнёр 2",
    "parent": "Родитель",
    "teen": "Подросток",
    "child": "Ребёнок",
    "deep": "Глубинный запрос (формулировка психолога)",
}

GOAL_STATUSES = {
    "active": "Действует",
    "revised": "Пересмотрена",
    "achieved": "Достигнута",
    "dropped": "Снята",
}

HYPOTHESIS_STATUSES = {
    "working": "Рабочая",
    "supported": "Подтверждается",
    "contradicted": "Есть противоречащие данные",
    "dropped": "Отброшена",
}

MEASURE_SOURCES = {
    "client": "Оценка клиента",
    "psychologist": "Наблюдение психолога",
}

METHODS = {
    "cbt": "КПТ",
    "act": "ACT",
    "hypnosis": "Гипноз",
    "regression": "Регрессия",
    "exposure": "Экспозиция",
    "parts": "Работа с частями",
    "coherence": "Когерентная терапия",
    "other": "Другое",
}

REVIEW_STATUSES = {
    "draft": "Черновик",
    "review": "Нужна проверка",
    "checked": "Проверено психологом",
}

ANALYZERS = {
    "ollama": "Локальная модель (Ollama)",
    "yandex": "YandexGPT",
    "claude": "Claude (вручную)",
}

ANALYSIS_STATUSES = {
    "queued": "В очереди",
    "running": "Выполняется",
    "done": "Готово",
    "error": "Ошибка",
    "waiting_import": "Ждёт ответа из Claude",
}

TRANSCRIPTION_STATUSES = {
    "queued": "Ждёт компьютер с транскрибатором",
    "running": "Расшифровывается",
    "done": "Готово",
    "error": "Ошибка",
}


# --- Таблицы ---------------------------------------------------------------

class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    client_type: Mapped[str] = mapped_column(String(20), default="adult")
    status: Mapped[str] = mapped_column(String(20), default="active")
    phase: Mapped[str] = mapped_column(String(20), default="")
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    context: Mapped[str] = mapped_column(Text, default="")
    contract: Mapped[str] = mapped_column(Text, default="")
    intake: Mapped[str] = mapped_column(Text, default="")
    status_reason: Mapped[str] = mapped_column(Text, default="")
    next_contact: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    goals: Mapped[list[Goal]] = relationship(back_populates="client", cascade="all, delete-orphan", order_by="Goal.id")
    hypotheses: Mapped[list[Hypothesis]] = relationship(back_populates="client", cascade="all, delete-orphan", order_by="Hypothesis.id")
    markers: Mapped[list[Marker]] = relationship(back_populates="client", cascade="all, delete-orphan", order_by="Marker.id")
    sessions: Mapped[list[Session]] = relationship(back_populates="client", cascade="all, delete-orphan", order_by="Session.session_date")
    events: Mapped[list[ClientEvent]] = relationship(back_populates="client", cascade="all, delete-orphan", order_by="ClientEvent.happened_on")


class ClientEvent(Base):
    """Журнал изменений по клиенту: смена фазы, статуса, целей. Нужен для хронологии."""
    __tablename__ = "client_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    happened_on: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(32))  # phase / status / goal / note
    text: Mapped[str] = mapped_column(Text)
    client: Mapped[Client] = relationship(back_populates="events")


class Goal(Base):
    """Запрос словами конкретного участника + перевод на язык процесса."""
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    who: Mapped[str] = mapped_column(String(20), default="client")
    words: Mapped[str] = mapped_column(Text)
    process: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_on: Mapped[date] = mapped_column(Date, default=date.today)
    review_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    client: Mapped[Client] = relationship(back_populates="goals")


class Hypothesis(Base):
    """Рабочая гипотеза. Допускает пересмотр — это не диагноз."""
    __tablename__ = "hypotheses"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    grounds: Mapped[str] = mapped_column(Text, default="")
    contrary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="working")
    created_on: Mapped[date] = mapped_column(Date, default=date.today)
    client: Mapped[Client] = relationship(back_populates="hypotheses")


class Marker(Base):
    """Цель-маркер: что и как измеряем. Значения — в Measurement."""
    __tablename__ = "markers"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    how: Mapped[str] = mapped_column(String(200), default="шкала 0–10")
    anchors: Mapped[str] = mapped_column(Text, default="")
    lower_is_better: Mapped[bool] = mapped_column(Boolean, default=False)
    client: Mapped[Client] = relationship(back_populates="markers")
    goal: Mapped[Goal | None] = relationship()
    measurements: Mapped[list[Measurement]] = relationship(
        back_populates="marker", cascade="all, delete-orphan", order_by="Measurement.measured_on")

    @property
    def start(self):
        return self.measurements[0] if self.measurements else None

    @property
    def current(self):
        return self.measurements[-1] if self.measurements else None


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    marker_id: Mapped[int] = mapped_column(ForeignKey("markers.id", ondelete="CASCADE"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True)
    measured_on: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="client")
    comment: Mapped[str] = mapped_column(Text, default="")
    marker: Mapped[Marker] = relationship(back_populates="measurements")


# Поля записи сессии, которые может предложить ИИ и которые проверяет психолог.
SESSION_TEXT_FIELDS = {
    "focus": "Фокус сессии и связь с целями",
    "complaint": "Жалоба / с чем пришёл(ла) сегодня",
    "client_words": "Данные: слова клиента и факты",
    "between_sessions": "Изменения между встречами",
    "professional_move": "Профессиональный ход (что я делала)",
    "client_response": "Реакция клиента",
    "rationale": "Чем обоснован ход (теория)",
    "shift": "Сдвиг (шкала / факт)",
    "conclusions": "Выводы",
    "recommendations": "Рекомендации и домашнее задание",
    "client_feedback": "Обратная связь клиента о встрече",
    "agreements": "Договорённости",
    "open_questions": "Незакрытые вопросы",
    "next_step": "Что дальше: шаг для клиента и для меня",
}

# Поля только для психолога — ИИ их не заполняет.
SESSION_SELF_FIELDS = {
    "reflection": "Моя рефлексия",
    "do_differently": "Что бы я сделала иначе",
    "supervision_question": "Вопрос к супервизии",
}


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("client_id", "number", name="uq_session_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    session_date: Mapped[date] = mapped_column(Date)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    methods: Mapped[str] = mapped_column(String(200), default="")  # коды через запятую
    transcript: Mapped[str] = mapped_column(Text, default="")
    synopsis: Mapped[str] = mapped_column(Text, default="")

    focus: Mapped[str] = mapped_column(Text, default="")
    complaint: Mapped[str] = mapped_column(Text, default="")
    client_words: Mapped[str] = mapped_column(Text, default="")
    between_sessions: Mapped[str] = mapped_column(Text, default="")
    professional_move: Mapped[str] = mapped_column(Text, default="")
    client_response: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    shift: Mapped[str] = mapped_column(Text, default="")
    conclusions: Mapped[str] = mapped_column(Text, default="")
    recommendations: Mapped[str] = mapped_column(Text, default="")
    client_feedback: Mapped[str] = mapped_column(Text, default="")
    agreements: Mapped[str] = mapped_column(Text, default="")
    open_questions: Mapped[str] = mapped_column(Text, default="")
    next_step: Mapped[str] = mapped_column(Text, default="")

    reflection: Mapped[str] = mapped_column(Text, default="")
    do_differently: Mapped[str] = mapped_column(Text, default="")
    supervision_question: Mapped[str] = mapped_column(Text, default="")

    review_status: Mapped[str] = mapped_column(String(20), default="draft")

    client: Mapped[Client] = relationship(back_populates="sessions")
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Analysis.id")
    audio_files: Mapped[list[AudioFile]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AudioFile.id")

    @property
    def method_codes(self) -> list[str]:
        return [m for m in self.methods.split(",") if m]

    @property
    def method_labels(self) -> str:
        return ", ".join(METHODS.get(m, m) for m in self.method_codes)


class Analysis(Base):
    """Результат одного разборщика. kind: session — разбор встречи, dynamics — динамика клиента."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="session")
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True)
    analyzer: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[Session | None] = relationship(back_populates="analyses")
    client: Mapped[Client] = relationship()


class AudioFile(Base):
    """Аудио сессии, ожидающее транскрибации на компьютере психолога."""
    __tablename__ = "audio_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(300))
    stored_name: Mapped[str] = mapped_column(String(300))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    session: Mapped[Session] = relationship(back_populates="audio_files")
