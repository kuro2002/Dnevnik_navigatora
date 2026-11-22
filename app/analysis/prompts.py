"""Тексты инструкций для разборщиков.

Одни и те же инструкции и один и тот же формат ответа используются для всех
моделей (Ollama, YandexGPT, Claude), чтобы разборы можно было сравнивать.
Меняя текст, увеличивайте PROMPT_VERSION — версия сохраняется с каждым разбором.
"""
from __future__ import annotations

import json

from ..models import METHODS, SESSION_TEXT_FIELDS

PROMPT_VERSION = "s1"
DYNAMICS_PROMPT_VERSION = "d1"

COMMON_RULES = """\
Ты помогаешь практикующему интегративному психологу-гипнологу (КПТ, ACT, гипноз и регрессионная \
работа, экспозиция, работа с частями, когерентная терапия) документировать сессии и видеть \
собственный профессиональный рост. Клинические решения и ответственность остаются у психолога. \
Ты не супервизор и не заменяешь супервизию.

Правила:
1. Разделяй: слова клиента; то, что наблюдаемо произошло в записи; впечатления психолога; \
твои гипотезы. Гипотезы явно помечай как гипотезы.
2. Не заполняй пробелы догадками. Если чего-то нет в записи, пиши пустую строку и укажи это \
в «gaps». «Не найдено в записи» не значит «психолог этого не сделал».
3. Для каждого существенного вывода приводи дословную реплику или фрагмент (и таймкод, если он есть).
4. Транскрипт получен автоматическим распознаванием: возможны ошибки слов и перепутанные \
говорящие. Если смысл фрагмента зависит от такой ошибки, отметь это в «asr_issues».
5. Образы и переживания из гипноза и регрессии — субъективный материал сессии, \
а не подтверждённые исторические события.
6. Не ставь диагнозов, не делай окончательных клинических выводов, не выставляй общую \
оценку «качества психолога». Не оценивай работу психолога только по улучшению клиента.
7. Тон — конкретный и уважительный, без обвинений и без пустой похвалы. Психолог хочет \
видеть и реальные упущения, и реальные сильные решения.
8. Обратная связь психологу строится так: наблюдение → подтверждающий фрагмент → \
возможное значение → альтернативное объяснение → вопрос или вариант для проверки.
9. Пиши по-русски. Отвечай ТОЛЬКО одним JSON-объектом без пояснений вокруг.
"""

SESSION_SCHEMA = {
    "synopsis": "краткий синопсис встречи, 5–10 предложений",
    "fields": {k: "текст или пустая строка, если в записи этого нет" for k in SESSION_TEXT_FIELDS},
    "methods_observed": [{"method": "название метода", "evidence": "фрагмент"}],
    "observations": [{
        "kind": "strength | difficulty | neutral",
        "observation": "что произошло",
        "evidence": "дословный фрагмент или таймкод",
        "possible_meaning": "что это может значить",
        "alternative": "альтернативное объяснение",
        "question": "вопрос психологу или вариант для проверки",
    }],
    "marker_evidence": [{"marker": "название маркера", "evidence": "что сказал клиент", "value": "число, если клиент его назвал, иначе пусто"}],
    "gaps": ["чего не хватает в записи для заполнения карточки"],
    "contradictions": ["противоречия между записью, целями и прошлыми сессиями"],
    "clarifying_questions": ["3–5 конкретных вопросов психологу, чтобы уточнить запись"],
    "asr_issues": ["места, где распознавание речи могло исказить смысл"],
}

DYNAMICS_SCHEMA = {
    "summary": "общая картина динамики за период, 5–8 предложений",
    "by_goal": [{
        "goal": "цель",
        "direction": "улучшение | без изменений | ухудшение | недостаточно данных",
        "changes": "конкретные изменения в жизни клиента",
        "evidence": "на чём основано: сессия, дата, фрагмент, замер",
        "contrary": "что противоречит этому выводу",
    }],
    "interaction": [{
        "observation": "качество взаимодействия и согласованность работы",
        "evidence": "фрагмент или сессия",
        "possible_meaning": "", "alternative": "", "question": "",
    }],
    "psychologist_skills": [{
        "kind": "strength | difficulty | growth",
        "observation": "повторяющийся паттерн в действиях психолога",
        "evidence": "сессии и фрагменты",
        "possible_meaning": "", "alternative": "", "question": "",
    }],
    "regress_or_risks": ["признаки ухудшения или застоя, с опорой на данные"],
    "data_gaps": ["каких данных не хватает для достоверной оценки"],
    "supervision_questions": ["вопросы, которые стоит отнести на супервизию"],
}


def _schema_text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2)


def session_system_prompt() -> str:
    field_list = "\n".join(f"- {k}: {v}" for k, v in SESSION_TEXT_FIELDS.items())
    return (
        COMMON_RULES
        + "\nЗадача: разобрать запись одной сессии и подготовить черновик карточки сессии, "
        "который психолог проверит и исправит.\n\nПоля карточки (ключи в fields):\n"
        + field_list
        + "\n\nФормат ответа (JSON):\n"
        + _schema_text(SESSION_SCHEMA)
    )


def client_context(client, previous_sessions=()) -> str:
    """Контекст клиента без персональных данных: шифр, цели, гипотезы, маркеры."""
    lines = [f"Шифр клиента: {client.code}"]
    goals = [g for g in client.goals if g.status in ("active", "revised")]
    if goals:
        lines.append("Согласованные цели и запросы:")
        for g in goals:
            line = f"- ({g.who}) словами: «{g.words}»"
            if g.process:
                line += f"; на языке процесса: {g.process}"
            lines.append(line)
    hyps = [h for h in client.hypotheses if h.status != "dropped"]
    if hyps:
        lines.append("Рабочие гипотезы психолога (не диагнозы):")
        for h in hyps:
            lines.append(f"- {h.text} [{h.status}]")
    if client.markers:
        lines.append("Цели-маркеры:")
        for m in client.markers:
            hist = ", ".join(f"{x.measured_on:%d.%m}: {x.value:g}" for x in m.measurements[-6:])
            lines.append(f"- {m.name} ({m.how}){' — история: ' + hist if hist else ''}")
    for s in previous_sessions:
        bits = [f"Сессия №{s.number} от {s.session_date:%d.%m.%Y}"]
        for key in ("focus", "agreements", "next_step", "open_questions"):
            val = getattr(s, key)
            if val:
                bits.append(f"{SESSION_TEXT_FIELDS[key]}: {val}")
        lines.append("Прошлая встреча — " + "; ".join(bits))
    return "\n".join(lines)


def session_user_prompt(session, transcript_or_notes: str, is_notes: bool = False,
                        previous_sessions=()) -> str:
    methods = ", ".join(METHODS.get(m, m) for m in session.method_codes) or "не указаны"
    head = [
        client_context(session.client, previous_sessions),
        f"\nСессия №{session.number}, дата встречи: {session.session_date:%d.%m.%Y}.",
        f"Методы по словам психолога: {methods}.",
    ]
    if session.reflection:
        head.append(f"Рефлексия психолога после встречи: {session.reflection}")
    if is_notes:
        head.append("\nТранскрипт был длинным, ниже — конспект по частям с дословными цитатами:\n")
    else:
        head.append("\nТранскрипт сессии:\n")
    return "\n".join(head) + transcript_or_notes


CHUNK_SYSTEM = (
    COMMON_RULES.replace("Отвечай ТОЛЬКО одним JSON-объектом без пояснений вокруг.",
                         "Отвечай обычным текстом.")
    + "\nЗадача: это часть длинного транскрипта сессии. Составь подробный конспект этой части: "
    "темы, что делал психолог, как реагировал клиент, договорённости, оценки по шкалам, "
    "моменты несогласия или непонимания. Для каждого пункта приводи дословную цитату "
    "и таймкод, если он есть. Ничего не добавляй от себя."
)


def chunk_user_prompt(index: int, total: int, chunk: str) -> str:
    return f"Часть {index} из {total}:\n\n{chunk}"


def dynamics_system_prompt() -> str:
    return (
        COMMON_RULES
        + "\nЗадача: по записям нескольких сессий и замерам оценить динамику работы с клиентом "
        "по трём направлениям, не смешивая их: 1) изменения в повседневной жизни клиента "
        "относительно его целей; 2) качество взаимодействия и согласованность работы; "
        "3) действия психолога и развитие его навыков. Одной транскрипции недостаточно для "
        "оценки результата — опирайся на цели, замеры, обратную связь клиента и примеры "
        "между встречами, а если их нет, так и скажи.\n\nФормат ответа (JSON):\n"
        + _schema_text(DYNAMICS_SCHEMA)
    )


def dynamics_user_prompt(client, sessions) -> str:
    parts = [client_context(client)]
    for s in sessions:
        parts.append(f"\n=== Сессия №{s.number}, {s.session_date:%d.%m.%Y}"
                     f" (статус записи: {s.review_status}) ===")
        if s.method_labels:
            parts.append(f"Методы: {s.method_labels}")
        if s.synopsis:
            parts.append(f"Синопсис: {s.synopsis}")
        for key, label in SESSION_TEXT_FIELDS.items():
            val = getattr(s, key)
            if val:
                parts.append(f"{label}: {val}")
        for key, label in (("reflection", "Рефлексия психолога"),
                           ("do_differently", "Что бы сделала иначе")):
            val = getattr(s, key)
            if val:
                parts.append(f"{label}: {val}")
    return "\n".join(parts)
