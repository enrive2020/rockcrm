"""Воронка заявок: доска, карточка, стадии, пробный урок, конверсия, отчёт.

Правило этапа одно: **стадия и история пишутся вместе**. История без стадии
или стадия без истории одинаково ломают отчёт, а отчёт — это единственное,
ради чего школа вообще заводит воронку: она хочет знать, сколько стоит
пришедший ученик и какой источник его привёл.

Продажа абонемента при конверсии идёт через billing.sell_subscription —
тот же код, что и в этапе 2. Второй реализации быть не должно: запись
в журнал абонемента живёт в одном месте, иначе остаток однажды разъедется.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from . import billing, journal, phone, repository as repo
from .errors import ApiError, not_found
from .rules import (
    LOST,
    NO_ANSWER_ATTEMPTS,
    STAGE_ORDER,
    STAGE_TITLES,
    STAGES,
    humanize_duration,
)

# Напоминание о пробном уходит за сутки: раньше — забудут, позже — уже
# не переставят день.
TRIAL_REMINDER_LEAD_TIME = dt.timedelta(days=1)

# Сколько карточек отдаётся в одной колонке доски. Полсотни — это заведомо
# больше, чем помещается на экран, и заведомо меньше, чем пятьсот отказов,
# которые никто не пролистывает. Верхняя граница есть, чтобы ?limit=100000
# не возвращал ту же выборку без ограничения, ради которой всё затевалось.
DEFAULT_PAGE = 50
MAX_PAGE = 200


# ---------------------------------------------------------------------------
# Сборка ответов
# ---------------------------------------------------------------------------


def _tz(row: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(row["branch_timezone"] or "Asia/Almaty")


def _moment(value: dt.datetime | None, tz: ZoneInfo) -> str | None:
    return None if value is None else repo.iso(value, tz)


def _assigned(row: dict[str, Any]) -> dict[str, str] | None:
    if row["assigned_to"] is None:
        return None
    return {"id": str(row["assigned_to"]), "name": row["assigned_name"]}


def _trial_brief(
    cur: psycopg.Cursor,
    row: dict[str, Any],
    tz: ZoneInfo,
    occupied: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Краткое описание пробного вместе с конфликтами по кабинету и педагогу.

    occupied — занятость, посчитанная заранее одним запросом на всю доску.
    Если её не передали (карточка одной заявки), спрашиваем по этому слоту:
    ради единственной заявки собирать пакетный запрос смысла нет.
    """
    if row["lesson_id"] is None:
        return None
    if occupied is not None:
        occupants = occupied.get(str(row["lesson_id"]), [])
    else:
        occupants = repo.slot_occupants(
            cur,
            str(row["trial_room_id"]),
            str(row["trial_teacher_id"]),
            row["trial_starts_at"],
            row["trial_ends_at"],
            exclude_lesson_id=str(row["lesson_id"]),
        )
    return {
        "lesson_id": str(row["lesson_id"]),
        "starts_at": repo.iso(row["trial_starts_at"], tz),
        "teacher": row["trial_teacher"],
        "room": row["trial_room"],
        "status": row["trial_status"],
        "conflicts": [_conflict(row, other) for other in occupants],
    }


def _conflict(trial: dict[str, Any], other: dict[str, Any]) -> dict[str, str]:
    """Пересечение по кабинету или по преподавателю, названное словами."""
    if other["room_id"] == trial["trial_room_id"]:
        kind, message = "room", f"Кабинет «{other['room_name']}» занят: {other['title']}"
    else:
        kind = "teacher"
        message = f"Преподаватель {other['teacher_name']} занят: {other['title']}"
    return {"kind": kind, "with_lesson_id": str(other["id"]), "message": message}


def _flags(row: dict[str, Any], trial: dict[str, Any] | None, now: dt.datetime) -> list[str]:
    """То, на что администратор обязан среагировать.

    Флаг — это не украшение карточки, а причина взять её в работу сейчас,
    поэтому каждый из них проверяемый факт, а не оценка.
    """
    flags: list[str] = []
    if int(row["contact_attempts"]) >= NO_ANSWER_ATTEMPTS:
        flags.append("no_answer")
    if (
        row["next_action_at"] is not None
        and row["next_action_at"] < now
        and row["stage"] not in ("won", LOST)
    ):
        flags.append("overdue")
    if trial is not None:
        tz = _tz(row)
        if row["trial_starts_at"].astimezone(tz).date() == now.astimezone(tz).date():
            flags.append("trial_today")
        if trial["conflicts"]:
            flags.append("trial_conflict")
    return flags


def _card(
    cur: psycopg.Cursor,
    row: dict[str, Any],
    now: dt.datetime,
    occupied: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Карточка заявки на доске и в отдельном экране — одна и та же сборка."""
    tz = _tz(row)
    trial = _trial_brief(cur, row, tz, occupied)
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "phone": row["phone"],
        "student_name": row["student_name"],
        "student_age": row["student_age"],
        "discipline": row["discipline"],
        "source": row["source"],
        "stage": row["stage"],
        "created_at": repo.iso(row["created_at"], tz),
        # Сколько заявка стоит на текущей стадии. Именно это показывает
        # застрявшие: «3 дня» в колонке дозвона — потерянный клиент.
        "waiting_for": humanize_duration((now - row["stage_since"]).total_seconds()),
        "next_action_at": _moment(row["next_action_at"], tz),
        "contact_attempts": int(row["contact_attempts"]),
        "assigned_to": _assigned(row),
        "trial": trial,
        "flags": _flags(row, trial, now),
    }


def _full(cur: psycopg.Cursor, row: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    tz = _tz(row)
    card = _card(cur, row, now)
    card.update(
        {
            # min_age едет вместе с направлением, а не выводится клиентом:
            # контракт требует предупредить, когда ребёнок младше порога
            # («барабаны с 5, скрипка с 7»), а порог — настройка школы.
            # Готовая фраза лежит в age_warning, но интерфейсу нужен и сам
            # порог: подсветить поле возраста и назвать число в подсказке
            # по тексту предупреждения нечем.
            "discipline": (
                None
                if row["discipline_id"] is None
                else {
                    "id": str(row["discipline_id"]),
                    "name": row["discipline"],
                    "min_age": (
                        int(row["discipline_min_age"])
                        if row["discipline_min_age"] is not None
                        else None
                    ),
                }
            ),
            "branch": (
                None
                if row["branch_id"] is None
                else {"id": str(row["branch_id"]), "name": row["branch"]}
            ),
            "lost_reason": row["lost_reason"],
            "utm": dict(row["utm"] or {}),
            "promo_code": row["promo_code"],
            "external_id": row["external_id"],
            "history": [
                {
                    "at": repo.iso(h["changed_at"], tz),
                    "from": h["from_stage"],
                    "to": h["to_stage"],
                    "by": h["changed_by"],
                }
                for h in repo.lead_history(cur, str(row["id"]))
            ],
            "converted": {
                "student_id": str(row["student_id"]) if row["student_id"] else None,
                "person_id": str(row["person_id"]) if row["person_id"] else None,
            },
            # Сверх контракта: возраст ниже минимального для направления —
            # предупреждение, а не запрет. Решает администратор, а не сервер.
            "age_warning": _age_warning(row),
        }
    )
    return card


def _age_warning(row: dict[str, Any]) -> str | None:
    age, minimum = row["student_age"], row["discipline_min_age"]
    if age is None or minimum is None or age >= minimum:
        return None
    return (
        f"На направление «{row['discipline']}» берут с {minimum} лет, "
        f"а ученику {age}. Решает администратор."
    )


# ---------------------------------------------------------------------------
# Доска и карточка
# ---------------------------------------------------------------------------


def board(
    cur: psycopg.Cursor,
    stage: str | None,
    source: str | None,
    assigned_to: str | None,
    branch_id: str | None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """Доска воронки: по странице карточек в каждой колонке.

    Раньше отдавалась вся воронка целиком — 688 заявок и 1,3 секунды на живой
    школе, потому что колонка «Отказ» тянула пятьсот карточек, которые никто
    не читает. Теперь колонка отдаёт `limit` верхних, а `count` продолжает
    показывать, сколько их всего; `has_more` и `next_offset` (оба сверх
    контракта и необязательные) говорят интерфейсу, что можно долистать.
    """
    if stage is not None and stage not in STAGES:
        raise ApiError(
            400, "bad_stage", f"Неизвестная стадия «{stage}». Допустимые: {', '.join(STAGES)}."
        )
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))

    now = dt.datetime.now(dt.timezone.utc)
    rows = repo.list_leads(cur, stage, source, assigned_to, branch_id, limit, offset)
    counts = repo.lead_stage_counts(cur, stage, source, assigned_to, branch_id)

    # Занятость слотов — один запрос на всю доску вместо запроса на карточку.
    occupied = repo.slot_occupants_bulk(
        cur,
        [
            {
                "lesson_id": row["lesson_id"],
                "room_id": row["trial_room_id"],
                "teacher_id": row["trial_teacher_id"],
                "starts_at": row["trial_starts_at"],
                "ends_at": row["trial_ends_at"],
            }
            for row in rows
            if row["lesson_id"] is not None
        ],
    )

    cards: dict[str, list[dict[str, Any]]] = {name: [] for name in STAGES}
    for row in rows:
        cards[row["stage"]].append(_card(cur, row, now, occupied))

    # Фильтр по стадии возвращает одну колонку, а не шесть, из которых пять
    # пустые: пустая колонка на доске означает «здесь ничего нет», а не
    # «сюда не смотрели».
    shown = [stage] if stage is not None else list(STAGES)
    columns = []
    for name in shown:
        total = counts.get(name, {}).get("total", 0)
        loaded = offset + len(cards[name])
        columns.append(
            {
                "stage": name,
                "title": STAGE_TITLES[name],
                # Счётчик колонки — сколько заявок в этой стадии всего,
                # а не сколько уместилось на странице. Иначе администратор
                # решил бы, что отказов пятьдесят, и перестал бы искать
                # причины в тех четырёхстах, которых доска не показала.
                "count": total,
                "leads": cards[name],
                "has_more": loaded < total,
                "next_offset": loaded if loaded < total else None,
            }
        )
    return {"columns": columns, "summary": _summary(cur, counts)}


def _summary(cur: psycopg.Cursor, counts: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Шапка доски. Конверсия и средний путь берутся из истории, а не с доски.

    На доске лежат текущие стадии, и заявка, дошедшая до покупки, в колонке
    «пробный проведён» уже не лежит: считать конверсию по колонкам значило бы
    делить на тех, кто ещё не решил, и занижать её ровно на купивших.

    `total` и `overdue` считает база по всей выборке, а не страница: шапка
    отвечает на «сколько всего» и «сколько горит», и оба числа обязаны
    остаться верными, сколько бы карточек ни было загружено.
    """
    total = sum(item["total"] for item in counts.values())
    overdue = sum(item["overdue"] for item in counts.values())

    # Период — год: конверсия по трём заявкам за неделю не значит ничего.
    until = dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=365)
    reached = repo.funnel_stage_pairs(cur, since, until)
    held = [stages for stages in reached.values() if "trial_held" in stages]
    won = [stages for stages in held if "won" in stages]
    days = repo.funnel_days_to_won(cur, since, until)

    return {
        "total": total,
        "overdue": overdue,
        "conversion_trial_to_won_pct": round(len(won) / len(held) * 100) if held else 0,
        "avg_days_to_won": round(days, 1) if days is not None else None,
    }


def card(cur: psycopg.Cursor, lead_id: str) -> dict[str, Any] | None:
    row = repo.get_lead(cur, lead_id)
    if row is None:
        return None
    return _full(cur, row, dt.datetime.now(dt.timezone.utc))


def _reload(cur: psycopg.Cursor, lead_id: str) -> dict[str, Any]:
    return _full(cur, repo.get_lead(cur, lead_id), dt.datetime.now(dt.timezone.utc))


# ---------------------------------------------------------------------------
# Заведение и смена стадии
# ---------------------------------------------------------------------------


def _record_stage(
    cur: psycopg.Cursor,
    tenant_id: str,
    lead_id: str,
    from_stage: str | None,
    to_stage: str,
    actor_id: str | None,
) -> None:
    """Строка истории. Пишется в одной транзакции со сменой самой стадии.

    Первая строка — с from_stage = NULL: без неё стадия `new` не имела бы
    момента входа, и отчёт не смог бы посчитать ни её конверсию, ни время,
    которое заявка провисела нетронутой.
    """
    cur.execute(
        """
        INSERT INTO lead_stage_history (tenant_id, lead_id, from_stage, to_stage, changed_by)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (tenant_id, lead_id, from_stage, to_stage, actor_id),
    )


def create_lead(
    cur: psycopg.Cursor, tenant_id: str, actor_id: str | None, body: Any
) -> dict[str, Any]:
    """Заведение вручную: администратор принял звонок."""
    normalized = phone.normalize(body.phone)
    if body.phone and normalized is None:
        raise ApiError(
            400,
            "bad_phone",
            f"Телефон «{body.phone}» не похож на номер. Ожидается формат +7 701 555 24 18.",
        )

    if normalized:
        existing = repo.open_lead_with_phone(cur, normalized)
        if existing is not None:
            raise ApiError(
                409,
                "duplicate_lead",
                f"Заявка на этот номер уже есть: «{existing['name']}», "
                f"стадия «{STAGE_TITLES[existing['stage']]}». "
                "Работайте с ней, иначе воронка посчитает одного человека дважды.",
                {"lead_id": str(existing["id"]), "stage": existing["stage"]},
            )

    lead_id = _insert_lead(
        cur,
        tenant_id,
        actor_id,
        name=body.name,
        phone_value=normalized,
        student_name=body.student_name,
        student_age=body.student_age,
        discipline_id=body.discipline_id,
        branch_id=body.branch_id,
        source=body.source,
        utm={},
        promo_code=body.promo_code,
        external_id=None,
        comment=body.comment,
    )
    journal.audit(
        cur, tenant_id, actor_id, "lead.create", "lead", lead_id, {"source": body.source}
    )
    return _reload(cur, lead_id)


def _insert_lead(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str | None,
    *,
    name: str,
    phone_value: str | None,
    student_name: str | None,
    student_age: int | None,
    discipline_id: str | None,
    branch_id: str | None,
    source: str,
    utm: dict[str, Any],
    promo_code: str | None,
    external_id: str | None,
    comment: str | None,
) -> str:
    """Вставка заявки вместе с первой строкой истории — всегда вместе.

    Комментарий администратора складывается в utm: отдельной колонки под него
    в схеме нет, а терять «удобно после 18:00» нельзя — это ровно то, ради
    чего перезванивают.
    """
    payload = dict(utm)
    if comment:
        payload["comment"] = comment

    cur.execute(
        """
        INSERT INTO lead (tenant_id, name, phone, student_name, student_age,
                          discipline_id, branch_id, source, utm, promo_code,
                          external_id, stage)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new')
        RETURNING id
        """,
        (
            tenant_id, name, phone_value, student_name, student_age,
            discipline_id, branch_id, source,
            json.dumps(payload, ensure_ascii=False), promo_code, external_id,
        ),
    )
    lead_id = str(cur.fetchone()["id"])
    _record_stage(cur, tenant_id, lead_id, None, "new", actor_id)
    return lead_id


def update_lead(
    cur: psycopg.Cursor, tenant_id: str, actor_id: str, lead_id: str, body: Any
) -> dict[str, Any]:
    """Смена стадии, ответственного, напоминания и счётчика попыток."""
    lead = repo.get_lead(cur, lead_id, for_update=True)
    if lead is None:
        raise not_found("Заявка не найдена")

    fields = body.model_dump(exclude_unset=True)
    new_stage = fields.get("stage")

    if new_stage is not None:
        _check_stage_change(lead, new_stage, fields)

    if "assigned_to" in fields and fields["assigned_to"] is not None:
        if not repo.user_exists(cur, fields["assigned_to"]):
            raise not_found("Сотрудник не найден")

    sets, params = [], {}
    for column in ("stage", "lost_reason", "next_action_at", "contact_attempts", "assigned_to"):
        if column in fields:
            sets.append(f"{column} = %({column})s")
            params[column] = fields[column]
    # Сбрасываем причину отказа при уходе из lost: «дорого» на заявке,
    # вернувшейся в дозвон, читалось бы как действующее решение клиента.
    if new_stage is not None and new_stage != LOST and "lost_reason" not in fields:
        sets.append("lost_reason = NULL")

    if not sets:
        return _reload(cur, lead_id)

    params["id"] = lead_id
    cur.execute(
        f"UPDATE lead SET {', '.join(sets)}, updated_at = now() WHERE id = %(id)s", params
    )

    if new_stage is not None and new_stage != lead["stage"]:
        _record_stage(cur, tenant_id, lead_id, lead["stage"], new_stage, actor_id)
        journal.audit(
            cur, tenant_id, actor_id, "lead.stage", "lead", lead_id,
            {"from": lead["stage"], "to": new_stage, "lost_reason": fields.get("lost_reason")},
        )

    return _reload(cur, lead_id)


def _check_stage_change(lead: dict[str, Any], new_stage: str, fields: dict[str, Any]) -> None:
    if new_stage not in STAGES:
        raise ApiError(
            400, "bad_stage", f"Неизвестная стадия «{new_stage}». Допустимые: {', '.join(STAGES)}."
        )
    if new_stage == "won":
        # Выигрыш ставит только конверсия. Иначе в воронке появятся выигранные
        # заявки без ученика, и отчёт «сколько стоит пришедший ученик» начнёт
        # считать тех, кто никуда не пришёл.
        raise ApiError(
            422,
            "won_requires_conversion",
            "Стадию «Абонемент куплен» вручную не ставят: она появляется после "
            "оформления ученика. Нажмите «Оформить ученика».",
        )
    if new_stage == LOST and not fields.get("lost_reason") and not lead["lost_reason"]:
        raise ApiError(
            422,
            "lost_reason_required",
            "Укажите причину отказа — без неё воронка не показывает, что чинить.",
            {"allowed": ["price", "location", "schedule", "competitor", "no_answer",
                         "not_ready", "other"]},
        )


# ---------------------------------------------------------------------------
# Пробный урок
# ---------------------------------------------------------------------------


def book_trial(
    cur: psycopg.Cursor, tenant_id: str, actor_id: str, lead_id: str, body: Any
) -> dict[str, Any]:
    """Занятие в расписании и перевод заявки в trial_booked — одной транзакцией."""
    lead = repo.get_lead(cur, lead_id, for_update=True)
    if lead is None:
        raise not_found("Заявка не найдена")
    if lead["stage"] in ("won", LOST):
        raise ApiError(
            422,
            "lead_closed",
            f"Заявка закрыта (стадия «{STAGE_TITLES[lead['stage']]}») — "
            "пробный назначать не на что.",
        )

    room = repo.get_room(cur, body.room_id)
    if room is None:
        raise not_found("Кабинет не найден")
    teacher = repo.get_staff(cur, body.teacher_id)
    if teacher is None:
        raise not_found("Преподаватель не найден")

    discipline = (
        repo.get_discipline(cur, str(lead["discipline_id"]))
        if lead["discipline_id"] is not None
        else None
    )
    _check_room_fits(room, discipline)

    starts_at = body.starts_at
    ends_at = starts_at + dt.timedelta(minutes=body.duration_min)

    if not body.overbook_ack:
        occupants = repo.slot_occupants(
            cur, str(room["id"]), str(teacher["id"]), starts_at, ends_at
        )
        if occupants:
            other = occupants[0]
            busy = (
                f"Кабинет «{other['room_name']}»"
                if other["room_id"] == room["id"]
                else f"Преподаватель {other['teacher_name']}"
            )
            raise ApiError(
                409,
                "slot_busy",
                f"{busy} занят в это время: {other['title']}. "
                "Выберите другое время или подтвердите овербукинг (overbook_ack).",
                {
                    "lesson_id": str(other["id"]),
                    "starts_at": other["starts_at"].isoformat(),
                    "title": other["title"],
                },
            )

    cur.execute(
        """
        INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, discipline_id,
                            lead_id, kind, starts_at, ends_at, status, overbook_ack)
        VALUES (%s, %s, %s, %s, %s, %s, 'trial', %s, %s, 'planned', %s)
        RETURNING id
        """,
        (
            tenant_id, room["branch_id"], teacher["id"], room["id"],
            lead["discipline_id"], lead_id, starts_at, ends_at, body.overbook_ack,
        ),
    )
    lesson_id = str(cur.fetchone()["id"])

    if lead["stage"] != "trial_booked":
        cur.execute(
            "UPDATE lead SET stage = 'trial_booked', updated_at = now() WHERE id = %s",
            (lead_id,),
        )
        _record_stage(cur, tenant_id, lead_id, lead["stage"], "trial_booked", actor_id)

    queued = _queue_trial_reminder(cur, tenant_id, lead, lesson_id, starts_at)

    journal.audit(
        cur, tenant_id, actor_id, "lead.trial", "lead", lead_id,
        {
            "lesson_id": lesson_id,
            "starts_at": starts_at.isoformat(),
            "teacher_id": str(teacher["id"]),
            "room_id": str(room["id"]),
            "price": body.price,
            "overbook_ack": body.overbook_ack,
            "notification_queued": queued,
        },
    )

    tz = _tz(lead)
    return {
        "lesson_id": lesson_id,
        "stage": "trial_booked",
        "starts_at": repo.iso(starts_at, tz),
        "teacher": teacher["name"],
        "room": room["name"],
        "notification_queued": queued,
    }


def _check_room_fits(room: dict[str, Any], discipline: dict[str, Any] | None) -> None:
    """Кабинет обязан подходить направлению.

    Барабаны без установки — это не занятие, а пришедший зря родитель.
    Требования направления сверяются с характеристиками кабинета: обе
    стороны уже описаны в схеме (`discipline.room_reqs`, `room.features`).
    """
    if discipline is None:
        return
    required = dict(discipline["room_reqs"] or {})
    features = dict(room["features"] or {})
    missing = [key for key, value in required.items() if value and not features.get(key)]
    if missing:
        raise ApiError(
            422,
            "room_unsuitable",
            f"Кабинет «{room['name']}» не подходит направлению «{discipline['name']}»: "
            f"не хватает {', '.join(missing)}.",
            {"room_id": str(room["id"]), "missing": missing},
        )


def _queue_trial_reminder(
    cur: psycopg.Cursor,
    tenant_id: str,
    lead: dict[str, Any],
    lesson_id: str,
    starts_at: dt.datetime,
) -> bool:
    """Напоминание в очередь, а не прямой вызов провайдера.

    Сообщение ложится на диск в той же транзакции, что и занятие: если
    транзакция откатится, напоминание о несуществующем пробном не уйдёт,
    а если провайдер лежит — оно дождётся его в очереди.

    dedup_key = trial_reminder:<lesson_id>: повторное назначение того же
    пробного не должно слать второе сообщение. ON CONFLICT DO NOTHING,
    а не проверка «есть ли уже»: между проверкой и вставкой помещается
    второй такой же запрос.
    """
    if not lead["phone"]:
        return False
    cur.execute(
        """
        INSERT INTO notification (tenant_id, channel, template, payload, to_address,
                                  send_after, dedup_key)
        VALUES (%s, 'whatsapp', 'trial_reminder', %s, %s, %s, %s)
        ON CONFLICT (tenant_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        RETURNING id
        """,
        (
            tenant_id,
            json.dumps(
                {"lead_id": str(lead["id"]), "lesson_id": lesson_id,
                 "name": lead["student_name"] or lead["name"]},
                ensure_ascii=False,
            ),
            lead["phone"],
            starts_at - TRIAL_REMINDER_LEAD_TIME,
            f"trial_reminder:{lesson_id}",
        ),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Конверсия
# ---------------------------------------------------------------------------


def convert(
    cur: psycopg.Cursor, tenant_id: str, actor_id: str, lead_id: str, body: Any
) -> dict[str, Any]:
    """Заявка → персона → семья → ученик → (необязательно) абонемент.

    Самая тяжёлая операция этапа и целиком одна транзакция: ученик без семьи
    или семья без ученика — это данные, которые потом никто не соберёт руками.
    """
    lead = repo.get_lead(cur, lead_id, for_update=True)
    if lead is None:
        raise not_found("Заявка не найдена")
    if lead["student_id"] is not None:
        raise ApiError(
            409,
            "already_converted",
            "Заявка уже оформлена в ученика.",
            {"student_id": str(lead["student_id"])},
        )

    payer_person = _resolve_payer(cur, tenant_id, lead, body)
    student_person = _resolve_student_person(cur, tenant_id, lead, body, payer_person)
    family_id = _resolve_family(cur, tenant_id, payer_person, student_person)

    branch_id = body.student.branch_id or lead["branch_id"]
    discipline_id = body.student.discipline_id or lead["discipline_id"]
    cur.execute(
        """
        INSERT INTO student (tenant_id, person_id, family_id, branch_id,
                             discipline_id, main_teacher_id, started_on)
        VALUES (%s, %s, %s, %s, %s, %s, current_date)
        RETURNING id
        """,
        (
            tenant_id, student_person["id"], family_id, branch_id,
            discipline_id, body.student.main_teacher_id,
        ),
    )
    student_id = str(cur.fetchone()["id"])

    subscription_id = None
    if body.subscription is not None:
        # Тот же код, что и в этапе 2: журнал, платёж, аудит и правила
        # на момент продажи. Второй реализации продажи не существует.
        sold = billing.sell_subscription(
            cur, tenant_id, actor_id, student_id, body.subscription
        )
        subscription_id = sold["subscription_id"]

    cur.execute(
        """
        UPDATE lead SET stage = 'won', person_id = %s, student_id = %s, updated_at = now()
        WHERE id = %s
        """,
        (student_person["id"], student_id, lead_id),
    )
    _record_stage(cur, tenant_id, lead_id, lead["stage"], "won", actor_id)

    journal.audit(
        cur, tenant_id, actor_id, "lead.convert", "lead", lead_id,
        {
            "student_id": student_id,
            "person_id": str(student_person["id"]),
            "payer_person_id": str(payer_person["id"]),
            "family_id": family_id,
            "subscription_id": subscription_id,
            "person_reused": bool(payer_person.get("reused")),
        },
    )

    return {
        "student_id": student_id,
        "person_id": str(student_person["id"]),
        "family_id": family_id,
        "subscription_id": subscription_id,
        "stage": "won",
    }


def _create_person(
    cur: psycopg.Cursor,
    tenant_id: str,
    first_name: str,
    last_name: str | None,
    phone_value: str | None,
    birth_date: dt.date | None = None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO person (tenant_id, first_name, last_name, phone, birth_date, pd_consent_at)
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING id, first_name, last_name, phone
        """,
        (tenant_id, first_name, last_name, phone_value, birth_date),
    )
    return dict(cur.fetchone())


def _resolve_payer(
    cur: psycopg.Cursor, tenant_id: str, lead: dict[str, Any], body: Any
) -> dict[str, Any]:
    """Плательщик: существующая персона с тем же телефоном или новая.

    Переиспользование обязательно. Тот же родитель приведёт второго ребёнка,
    и два профиля на него означают потерянную семейную скидку, разъехавшуюся
    историю платежей и невозможность посчитать долг семьи.
    """
    if body.payer is not None:
        first, last = body.payer.first_name, body.payer.last_name
        raw_phone = body.payer.phone or lead["phone"]
    else:
        # Плательщика не передали — платит сам ученик (взрослый).
        # Имя берём из заявки: это тот, кто оставил номер.
        first, last = _split_name(body.student.first_name, body.student.last_name)
        raw_phone = lead["phone"]

    normalized = phone.normalize(raw_phone)
    if raw_phone and normalized is None:
        raise ApiError(
            400, "bad_phone", f"Телефон плательщика «{raw_phone}» не похож на номер."
        )

    if normalized:
        existing = repo.person_by_phone(cur, normalized)
        if existing is not None:
            person = dict(existing)
            person["reused"] = True
            return person

    person = _create_person(cur, tenant_id, first, last, normalized)
    person["reused"] = False
    return person


def _split_name(first: str, last: str | None) -> tuple[str, str | None]:
    return first, last


def _resolve_student_person(
    cur: psycopg.Cursor,
    tenant_id: str,
    lead: dict[str, Any],
    body: Any,
    payer_person: dict[str, Any],
) -> dict[str, Any]:
    """Персона ученика.

    Без плательщика ученик и есть плательщик — взрослый платит за себя,
    и заводить ему вторую персону значило бы развести один телефон на два
    профиля, чего не даст и уникальный индекс.
    """
    if body.payer is None:
        if body.student.birth_date is not None:
            cur.execute(
                "UPDATE person SET birth_date = %s, updated_at = now() WHERE id = %s",
                (body.student.birth_date, payer_person["id"]),
            )
        return payer_person

    return _create_person(
        cur,
        tenant_id,
        body.student.first_name,
        body.student.last_name,
        None,  # у ребёнка своего телефона нет: связь со школой идёт через родителя
        body.student.birth_date,
    )


def _resolve_family(
    cur: psycopg.Cursor,
    tenant_id: str,
    payer_person: dict[str, Any],
    student_person: dict[str, Any],
) -> str:
    """Семья плательщика: существующая или новая.

    Если персона уже где-то плательщик, второй ребёнок идёт в ту же семью —
    вместе со скидкой за второго ребёнка, ради которой семья и заведена.
    """
    family = repo.family_of_payer(cur, str(payer_person["id"]))
    if family is None:
        cur.execute(
            """
            INSERT INTO family (tenant_id, name, payer_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                payer_person.get("last_name") or payer_person.get("first_name"),
                payer_person["id"],
            ),
        )
        family_id = str(cur.fetchone()["id"])
        _add_member(cur, family_id, str(payer_person["id"]), "payer")
    else:
        family_id = str(family["id"])

    _add_member(cur, family_id, str(student_person["id"]), "student")
    return family_id


def _add_member(cur: psycopg.Cursor, family_id: str, person_id: str, relation: str) -> None:
    cur.execute(
        """
        INSERT INTO family_member (family_id, person_id, relation)
        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
        """,
        (family_id, person_id, relation),
    )


# ---------------------------------------------------------------------------
# Приём по вебхуку
# ---------------------------------------------------------------------------


def accept_webhook(
    cur: psycopg.Cursor, tenant_id: str, body: Any
) -> tuple[dict[str, Any], bool]:
    """Заявка от внешней системы. Возвращает (карточка, создана ли).

    Идемпотентность по external_id обязательна: LeadHub построен на ретраях,
    и без неё одна недоступность сети превращается в дубли в воронке,
    а отчёт начинает врать в ту же секунду.
    """
    if body.external_id:
        existing = repo.lead_by_external_id(cur, body.external_id)
        if existing is not None:
            return _full(cur, existing, dt.datetime.now(dt.timezone.utc)), False

    discipline_id = None
    if body.discipline:
        found = repo.discipline_by_name(cur, body.discipline)
        # Не нашли — заявку всё равно принимаем: терять лид из-за опечатки
        # в названии направления нельзя, администратор уточнит по телефону.
        discipline_id = str(found["id"]) if found else None

    try:
        # Точка сохранения: два ретрая LeadHub, пришедшие одновременно, дают
        # UniqueViolation на lead_external_uniq. Без вложенной транзакции
        # ошибка отравила бы всю внешнюю, и прочитать уже вставленную заявку
        # было бы нечем — а ответить надо именно ею.
        with cur.connection.transaction():
            lead_id = _insert_lead(
                cur,
                tenant_id,
                None,  # автора нет: заявку принесла система, а не человек
                name=body.name,
                phone_value=phone.normalize(body.phone),
                student_name=body.student_name,
                student_age=body.student_age,
                discipline_id=discipline_id,
                branch_id=body.branch_id,
                source=body.source,
                utm=dict(body.utm or {}),
                promo_code=body.promo_code,
                external_id=body.external_id,
                comment=body.comment,
            )
    except psycopg.errors.UniqueViolation:
        raced = repo.lead_by_external_id(cur, body.external_id)
        if raced is None:
            raise
        return _full(cur, raced, dt.datetime.now(dt.timezone.utc)), False

    journal.audit(
        cur, tenant_id, None, "lead.webhook", "lead", lead_id,
        {"external_id": body.external_id, "source": body.source},
    )
    return _reload(cur, lead_id), True


# ---------------------------------------------------------------------------
# Отчёт по воронке
# ---------------------------------------------------------------------------


def funnel(
    cur: psycopg.Cursor, since: dt.date, until: dt.date, tz_name: str
) -> dict[str, Any]:
    """Конверсия по стадиям и по источникам — то, ради чего этап затевался."""
    tz = ZoneInfo(tz_name)
    start = dt.datetime.combine(since, dt.time(0, 0), tzinfo=tz)
    # Верхняя граница включительно по дате: «по 11 августа» означает
    # «включая весь одиннадцатый», а не «до полуночи одиннадцатого».
    end = dt.datetime.combine(until + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz)

    reached = repo.funnel_stage_pairs(cur, start, end)

    stages = []
    for index, name in enumerate(STAGE_ORDER):
        entered = [s for s in reached.values() if name in s]
        later = STAGE_ORDER[index + 1:]
        moved_on = [s for s in entered if any(step in s for step in later)]
        stages.append(
            {
                "stage": name,
                "title": STAGE_TITLES[name],
                "entered": len(entered),
                # У последней стадии идти некуда: moved_on равен entered,
                # иначе воронка заканчивалась бы нулевой конверсией на победе.
                "moved_on": len(entered) if not later else len(moved_on),
                "conversion_pct": (
                    100 if not later and entered
                    else round(len(moved_on) / len(entered) * 100) if entered else 0
                ),
            }
        )

    sources = [
        {
            "source": row["source"],
            "leads": int(row["leads"]),
            "trials": int(row["trials"]),
            "won": int(row["won"]),
            "conversion_pct": round(int(row["won"]) / int(row["leads"]) * 100)
            if row["leads"]
            else 0,
            "avg_days_to_won": (
                round(float(row["avg_days_to_won"]), 1)
                if row["avg_days_to_won"] is not None
                else None
            ),
        }
        for row in repo.funnel_sources(cur, start, end)
    ]

    days = repo.funnel_days_to_won(cur, start, end)
    return {
        "period": {"from": since.isoformat(), "to": until.isoformat()},
        "stages": stages,
        "sources": sources,
        "lost_reasons": [
            {"reason": row["reason"], "count": int(row["count"])}
            for row in repo.funnel_lost_reasons(cur, start, end)
        ],
        "avg_days_to_won": round(days, 1) if days is not None else None,
    }
