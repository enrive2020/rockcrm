"""Кабинет родителя: сборка ресурсов `/me/*` и заявки в школу (issue #5).

Почему это отдельный модуль, а не ветка в `students.py`
-------------------------------------------------------
Карточка ученика собирается ВЫЧИТАНИЕМ: берём всё, что знает школа, и убираем
то, чего родителю знать нельзя. Такая сборка ошибается молча — добавленное
завтра поле уедет родителю, если про него забыли, а в карточке лежат долг
семьи, риск оттока, внутренние заметки и ставка преподавателя. Половина
из этого ещё и обидна.

Здесь всё собирается СЛОЖЕНИЕМ: в ответ попадает только то, что положили
осознанно, и ошибка выглядит как отсутствующее поле, а не как утечка.
Ради этого модуль намеренно повторяет часть работы `students.py` вместо того,
чтобы звать его функции: общий сборщик рано или поздно обзаведётся
административным полем, и оно приедет сюда само.

Состав детей берётся ИЗ СЕССИИ (`authz.visible_student_ids`), а не из
параметров запроса. Идентификатор в пути только сверяется со списком;
неизвестный — 404, а не 403, по тому же правилу, что и везде: разница между
«нет такого» и «есть, но не ваше» сама по себе утечка.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from . import journal
from . import repository as repo
from .errors import ApiError, not_found
from .rules import DEFAULT_RULES, ledger_title

# Сколько дней показывает расписание без `from`/`to`. Неделя — ровно тот
# горизонт, на который родитель планирует: «когда вести на этой неделе».
DEFAULT_SCHEDULE_DAYS = 7

# «Скоро кончится»: остаток два и меньше либо срок в пределах недели.
# Порог считает сервер, а не интерфейс: «мало» — правило школы, и школа,
# продающая абонементы по 4 занятия, читает его иначе, чем интерфейс.
LOW_BALANCE = 2
ENDS_SOON_DAYS = 7

RESCHEDULE = "reschedule"
RENEW = "renew"


# ---------------------------------------------------------------------------
# Общие кусочки ответа
# ---------------------------------------------------------------------------


def _subscription(sub: Any, today: dt.date) -> dict[str, Any] | None:
    """Абонемент глазами родителя.

    `None` — не ошибка, а повод показать «нужно продление»: у ученика между
    абонементами действующего нет, и выдумывать пустой значило бы показать
    остаток 0 там, где абонемента не существует вовсе.

    Ни цены, ни скидки, ни долга: родитель платит на ресепшене, а кабинет
    отвечает на вопрос «сколько занятий осталось».
    """
    if sub is None:
        return None
    days_left = (sub.valid_until - today).days
    return {
        "lessons_balance": sub.lessons_balance,
        "lessons_total": sub.lessons_total,
        "makeups_balance": sub.makeups_balance,
        "valid_until": sub.valid_until.isoformat(),
        "status": sub.status,
        "ends_soon": sub.lessons_balance <= LOW_BALANCE or days_left <= ENDS_SOON_DAYS,
    }


def _notice_hours(sub: Any) -> int:
    """Порог отмены в часах. Берётся из абонемента: проданный абонемент —
    договор на условиях момента покупки, и менять его правила задним числом
    настройкой школы нельзя (то же правило, что у отметки посещаемости)."""
    if sub is None:
        return int(DEFAULT_RULES["cancel_notice_hours"])
    return int(sub.rule("cancel_notice_hours"))


def _may_request_reschedule(
    lesson_status: str, attendance: str | None, starts_at: dt.datetime,
    notice_hours: int, now: dt.datetime,
) -> str | None:
    """Причина, по которой перенос попросить нельзя. None = можно.

    Одна функция на два потребителя: флаг `can_request_reschedule`
    в расписании и проверка при создании заявки. Второй реализации «для
    сохранения» нет намеренно — иначе кабинет покажет кнопку, а сервер
    ответит отказом, и это будет выглядеть как поломка, а не как правило.
    """
    if attendance is not None or lesson_status == "held":
        return "Занятие уже проведено — перенести его нечем. Позвоните на ресепшен."
    if lesson_status == "cancelled":
        return "Занятие уже отменено. Новое время назначит администратор."
    if starts_at - now < dt.timedelta(hours=notice_hours):
        return (
            f"До занятия меньше {notice_hours} ч — по правилам школы перенос "
            f"в этот срок уже не оформляется. Позвоните на ресепшен, "
            f"там решат вопрос вручную."
        )
    return None


# ---------------------------------------------------------------------------
# GET /me/children
# ---------------------------------------------------------------------------


def children(cur: psycopg.Cursor, student_ids: list[str]) -> list[dict[str, Any]]:
    """Свои дети с остатком абонемента и ближайшим занятием.

    Главный экран кабинета: «когда вести» и «сколько осталось» — два вопроса,
    ради которых кабинет открывают, и оба обязаны быть в первом же ответе,
    без похода за карточкой каждого ребёнка.
    """
    now = dt.datetime.now(dt.timezone.utc)
    next_lessons = repo.family_next_lessons(cur, student_ids, now)

    out = []
    for row in repo.family_children(cur, student_ids):
        student_id = str(row["id"])
        tz = ZoneInfo(row["timezone"] or "Asia/Almaty")
        # «Сегодня» берётся в поясе филиала ребёнка: у школы с филиалом
        # в другом городе день начинается в другой момент, и абонемент,
        # истекающий сегодня, не должен считаться истёкшим на час раньше.
        today = dt.datetime.now(tz).date()
        sub = repo.active_subscription(cur, student_id, today)

        upcoming = next_lessons.get(student_id)
        out.append(
            {
                "student_id": student_id,
                "name": row["name"],
                "full_name": row["full_name"],
                "age": row["age"],
                "discipline": row["discipline"],
                "teacher": None if not row["teacher"] else {"name": row["teacher"]},
                "branch": None
                if not row["branch_name"]
                else {"name": row["branch_name"], "address": row["branch_address"]},
                "subscription": _subscription(sub, today),
                "next_lesson": None
                if upcoming is None
                else {
                    "lesson_id": str(upcoming["lesson_id"]),
                    "starts_at": repo.iso(
                        upcoming["starts_at"], ZoneInfo(upcoming["timezone"] or "Asia/Almaty")
                    ),
                    "room": upcoming["room"],
                },
            }
        )
    return out


# ---------------------------------------------------------------------------
# GET /me/schedule
# ---------------------------------------------------------------------------


def schedule(
    cur: psycopg.Cursor,
    student_ids: list[str],
    tz_name: str,
    date_from: dt.date | None,
    date_to: dt.date | None,
) -> dict[str, Any]:
    """Расписание ВСЕХ своих детей сразу.

    Родителю нужно знать, когда вести кого, а не листать детей по одному:
    двое детей в разных филиалах в один вечер — обычное дело, и увидеть это
    он должен одним экраном.
    """
    tz = ZoneInfo(tz_name)
    today = dt.datetime.now(tz).date()
    start_day = date_from or today
    # Неделя ВКЛЮЧИТЕЛЬНО: «с 11 по 17» человек читает как семь дней,
    # и полуоткрытый интервал заставил бы интерфейс прибавлять день.
    end_day = date_to or start_day + dt.timedelta(days=DEFAULT_SCHEDULE_DAYS - 1)
    if end_day < start_day:
        raise ApiError(
            400, "bad_period", "Конец периода раньше начала — проверьте даты."
        )

    start = dt.datetime.combine(start_day, dt.time(0, 0), tzinfo=tz)
    end = dt.datetime.combine(end_day, dt.time(0, 0), tzinfo=tz) + dt.timedelta(days=1)

    rows = repo.family_lessons(cur, student_ids, start, end)
    open_requests = repo.open_reschedule_requests(
        cur, [str(row["lesson_id"]) for row in rows]
    )

    # Порог отмены у каждого ребёнка свой: он лежит в его абонементе.
    notice: dict[str, int] = {
        sid: _notice_hours(repo.active_subscription(cur, sid, today))
        for sid in student_ids
    }
    now = dt.datetime.now(dt.timezone.utc)

    lessons = []
    for row in rows:
        lesson_tz = ZoneInfo(row["timezone"] or tz_name)
        lesson_id = str(row["lesson_id"])
        student_id = str(row["student_id"])
        pending = open_requests.get(lesson_id)
        blocked = _may_request_reschedule(
            row["status"],
            row["attendance"],
            row["starts_at"],
            notice.get(student_id, int(DEFAULT_RULES["cancel_notice_hours"])),
            now,
        )
        lessons.append(
            {
                "lesson_id": lesson_id,
                "student_id": student_id,
                "student_name": row["student_name"],
                "starts_at": repo.iso(row["starts_at"], lesson_tz),
                "ends_at": repo.iso(row["ends_at"], lesson_tz),
                "duration_min": row["duration_min"],
                "teacher": row["teacher"],
                "branch": row["branch"],
                "room": row["room"],
                "kind": row["kind"],
                "status": row["status"],
                "attendance": row["attendance"],
                "can_request_reschedule": blocked is None,
                # Уже поданная заявка — состояние, а не запрет: без него
                # кабинет предложил бы подать её второй раз и получил бы 409
                # там, где всё в порядке.
                "reschedule_request": None
                if pending is None
                else {"request_id": str(pending["id"]), "status": pending["status"]},
            }
        )

    return {
        "period": {"from": start_day.isoformat(), "to": end_day.isoformat()},
        "lessons": lessons,
    }


# ---------------------------------------------------------------------------
# GET /me/children/{student_id}
# ---------------------------------------------------------------------------


def child(cur: psycopg.Cursor, student_id: str) -> dict[str, Any] | None:
    """История одного ребёнка: за что списано, что задали, что играем.

    Репертуар — то, ради чего родитель платит и чего не видно в цифрах
    остатка. Он собирается из тегов заметок, и только тех, что видны семье.
    """
    row = repo.family_child(cur, student_id)
    if row is None:
        return None

    tz_name = row["timezone"] or "Asia/Almaty"
    today = dt.datetime.now(ZoneInfo(tz_name)).date()
    sub = repo.active_subscription(cur, student_id, today)

    history = []
    for entry in repo.family_history(cur, student_id, tz_name):
        note = None
        if entry["note_body"]:
            note = {
                "body": entry["note_body"],
                "homework": entry["note_homework"],
                "tags": list(entry["note_tags"] or []),
            }
        history.append(
            {
                "date": entry["day"].isoformat(),
                # Занятие есть не у каждого движения: покупка и заморозка
                # ни к какому уроку не привязаны.
                "starts_at": None
                if entry["starts_at"] is None
                else repo.iso(entry["starts_at"], ZoneInfo(tz_name)),
                "title": ledger_title(entry["kind"], entry["attendance"], entry["reason"]),
                "attendance": entry["attendance"],
                "lessons_delta": int(entry["lessons_delta"]),
                "makeups_delta": int(entry["makeups_delta"]),
                "note": note,
            }
        )

    progress = repo.family_progress(cur, student_id)
    return {
        "student_id": student_id,
        "name": row["full_name"],
        "age": row["age"],
        "discipline": row["discipline"],
        "teacher": row["teacher"],
        "started_on": row["started_on"].isoformat(),
        "subscription": _subscription(sub, today),
        "makeups": [
            {"expires_on": m["expires_on"].isoformat(), "days_left": int(m["days_left"])}
            for m in repo.family_makeups(cur, student_id)
        ],
        "history": history,
        "progress": {
            "lessons_attended": int(progress["lessons_attended"] or 0),
            "months": int(progress["months"] or 0),
            "repertoire": repo.family_repertoire(cur, student_id),
        },
    }


# ---------------------------------------------------------------------------
# Заявки: перенос и продление
#
# Родитель не двигает расписание сам: слот может быть занят, преподаватель
# может быть занят, а перенос одного занятия иногда тянет за собой второе.
# Поэтому из кабинета уходит заявка, а решение принимает администратор.
# ---------------------------------------------------------------------------


def _queue_task(
    cur: psycopg.Cursor,
    tenant_id: str,
    *,
    kind: str,
    title: str,
    student_id: str,
    request_id: str,
) -> None:
    """Задача администратору. Заявка, которую никто не увидит в своей очереди,
    ничем не отличается от несделанной."""
    cur.execute(
        """
        INSERT INTO task (tenant_id, kind, title, student_id, due_at, dedup_key)
        VALUES (%s, %s, %s, %s, now(), %s)
        """,
        (tenant_id, kind, title, student_id, f"family_request:{request_id}"),
    )


def _notify(
    cur: psycopg.Cursor,
    tenant_id: str,
    *,
    person_id: str | None,
    to_address: str | None,
    template: str,
    payload: dict[str, Any],
    dedup_key: str,
) -> None:
    """Сообщение в очередь.

    Адресат — тот, кто подал заявку: обещание «ответ придёт сообщением» даёт
    кабинет, и выполнять его некому, кроме очереди. Без адреса сообщения
    не ставим: строка без телефона не уедет никуда, а `to_address`
    объявлен NOT NULL.
    """
    if not to_address:
        return
    cur.execute(
        """
        INSERT INTO notification
          (tenant_id, person_id, channel, template, payload, to_address, dedup_key)
        VALUES (%s, %s, 'whatsapp', %s, %s, %s, %s)
        ON CONFLICT (tenant_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        """,
        (
            tenant_id,
            person_id,
            template,
            json.dumps(payload, ensure_ascii=False),
            to_address,
            dedup_key,
        ),
    )


def _requester_contact(cur: psycopg.Cursor, person_id: str) -> str | None:
    cur.execute("SELECT phone FROM person WHERE id = %s", (person_id,))
    row = cur.fetchone()
    return None if row is None else row["phone"]


def request_reschedule(
    cur: psycopg.Cursor,
    tenant_id: str,
    user_id: str,
    person_id: str,
    student_ids: list[str],
    lesson_id: str,
    reason: str | None,
    preferred: list[dt.datetime],
) -> dict[str, Any]:
    """Заявка на перенос занятия — заявка, а не перенос."""
    lesson = repo.family_lesson(cur, lesson_id, student_ids)
    if lesson is None:
        # Чужое занятие неотличимо от несуществующего.
        raise not_found("Занятие не найдено")

    student_id = str(lesson["student_id"])
    sub = repo.active_subscription(
        cur, student_id, lesson["starts_at"].astimezone(
            ZoneInfo(lesson["timezone"] or "Asia/Almaty")
        ).date()
    )
    blocked = _may_request_reschedule(
        lesson["status"],
        lesson["attendance"],
        lesson["starts_at"],
        _notice_hours(sub),
        dt.datetime.now(dt.timezone.utc),
    )
    if blocked is not None:
        raise ApiError(422, "reschedule_not_allowed", blocked)

    if repo.pending_family_request(cur, RESCHEDULE, student_id, lesson_id) is not None:
        raise ApiError(
            409,
            "request_exists",
            "Заявка на перенос этого занятия уже отправлена. "
            "Администратор ответит сообщением.",
        )

    request_id = repo.create_family_request(
        cur,
        tenant_id,
        kind=RESCHEDULE,
        requested_by=user_id,
        student_id=student_id,
        lesson_id=lesson_id,
        reason=reason,
        preferred=preferred,
    )

    tz = ZoneInfo(lesson["timezone"] or "Asia/Almaty")
    starts_at = repo.iso(lesson["starts_at"], tz)
    _queue_task(
        cur,
        tenant_id,
        # У `task.kind` нет вида «перенос»: список видов закрыт схемой,
        # а расширять его миграцией ради заголовка задачи незачем —
        # сама заявка лежит в `family_request` и по ней всё видно.
        kind="custom",
        title=f"Перенос занятия: {lesson['student_name']}, {starts_at[:16].replace('T', ' ')}",
        student_id=student_id,
        request_id=request_id,
    )
    _notify(
        cur,
        tenant_id,
        person_id=person_id,
        to_address=_requester_contact(cur, person_id),
        template="family_request_received",
        payload={
            "request_id": request_id,
            "kind": RESCHEDULE,
            "student_name": lesson["student_name"],
            "starts_at": starts_at,
        },
        dedup_key=f"family_request_received:{request_id}",
    )
    journal.audit(
        cur,
        tenant_id,
        user_id,
        "family_request.create",
        "family_request",
        request_id,
        {"kind": RESCHEDULE, "lesson_id": lesson_id, "student_id": student_id},
    )

    return {
        "request_id": request_id,
        "status": "pending",
        "lesson": {"starts_at": starts_at, "student_name": lesson["student_name"]},
        "message": "Заявка передана администратору. Ответ придёт сообщением.",
    }


def request_renew(
    cur: psycopg.Cursor,
    tenant_id: str,
    user_id: str,
    person_id: str,
    student_id: str,
    comment: str | None,
) -> dict[str, Any]:
    """Заявка на продление абонемента.

    Оплаты в кабинете нет и в этот этап она не входила: приём платежей через
    провайдера — отдельная интеграция, а обещать оплату, которой нет, хуже,
    чем её отсутствие. Поэтому кабинет ведёт к тому, кто оплату примет.
    """
    row = repo.family_child(cur, student_id)
    if row is None:
        raise not_found("Ученик не найден")

    if repo.pending_family_request(cur, RENEW, student_id, None) is not None:
        raise ApiError(
            409,
            "request_exists",
            "Заявка на продление уже отправлена. Администратор свяжется с вами.",
        )

    request_id = repo.create_family_request(
        cur,
        tenant_id,
        kind=RENEW,
        requested_by=user_id,
        student_id=student_id,
        lesson_id=None,
        reason=comment,
        preferred=[],
    )
    _queue_task(
        cur,
        tenant_id,
        kind="renew_subscription",
        title=f"Продление по заявке из кабинета: {row['full_name']}",
        student_id=student_id,
        request_id=request_id,
    )
    _notify(
        cur,
        tenant_id,
        person_id=person_id,
        to_address=_requester_contact(cur, person_id),
        template="family_request_received",
        payload={"request_id": request_id, "kind": RENEW, "student_name": row["full_name"]},
        dedup_key=f"family_request_received:{request_id}",
    )
    journal.audit(
        cur,
        tenant_id,
        user_id,
        "family_request.create",
        "family_request",
        request_id,
        {"kind": RENEW, "student_id": student_id},
    )

    return {
        "request_id": request_id,
        "status": "pending",
        "student": {"student_id": student_id, "name": row["full_name"]},
        "message": (
            "Заявка передана администратору. Он свяжется с вами, "
            "чтобы оформить продление."
        ),
    }


# ---------------------------------------------------------------------------
# Очередь администратора
# ---------------------------------------------------------------------------


def _request_out(row: dict[str, Any]) -> dict[str, Any]:
    """Заявка для администратора. Здесь ограничений на состав нет: это экран
    школы, а не кабинета, — но и лишнего в нём нет тоже."""
    tz = ZoneInfo(row["timezone"] or "Asia/Almaty")
    lesson = None
    if row["lesson_id"] is not None:
        lesson = {
            "lesson_id": str(row["lesson_id"]),
            "starts_at": repo.iso(row["lesson_starts_at"], tz),
            "status": row["lesson_status"],
            "teacher": row["lesson_teacher"],
            "branch": row["lesson_branch"],
            "room": row["lesson_room"],
        }
    return {
        "request_id": str(row["id"]),
        "kind": row["kind"],
        "status": row["status"],
        "created_at": repo.iso(row["created_at"], tz),
        "student": {"student_id": str(row["student_id"]), "name": row["student_name"]},
        "requested_by": {
            "name": row["requested_by_name"],
            "phone": row["requested_by_phone"],
        },
        "lesson": lesson,
        "reason": row["reason"],
        "preferred": [repo.iso(moment, tz) for moment in (row["preferred"] or [])],
        "answer": row["answer"],
        "answered_by": row["answered_by_name"],
        "answered_at": None
        if row["answered_at"] is None
        else repo.iso(row["answered_at"], tz),
        "moved_to": None if row["moved_to"] is None else str(row["moved_to"]),
    }


def list_requests(
    cur: psycopg.Cursor, status: str | None, kind: str | None, limit: int
) -> dict[str, Any]:
    counts = repo.family_request_counts(cur)
    return {
        "counts": {
            "pending": int(counts["pending"]),
            "reschedule": int(counts["reschedule"]),
            "renew": int(counts["renew"]),
        },
        "requests": [
            _request_out(row) for row in repo.list_family_requests(cur, status, kind, limit)
        ],
    }


def answer_request(
    cur: psycopg.Cursor,
    tenant_id: str,
    user_id: str,
    request_id: str,
    status: str,
    answer: str | None,
    moved_to: str | None,
) -> dict[str, Any]:
    """Рассмотрение заявки администратором.

    Отказ без объяснения хуже отказа: родитель всё равно позвонит, только уже
    раздражённым, — поэтому `answer` при отказе обязателен. У согласия он
    необязателен: «перенесли на четверг» видно по самому расписанию.
    """
    current = repo.get_family_request(cur, request_id)
    if current is None:
        raise not_found("Заявка не найдена")
    if status == "declined" and not (answer or "").strip():
        raise ApiError(
            422,
            "answer_required",
            "Напишите, почему перенос не состоится: отказ без объяснения "
            "родитель всё равно придёт выяснять на ресепшен.",
        )
    if moved_to is not None and current["kind"] != RESCHEDULE:
        raise ApiError(
            422,
            "moved_to_not_applicable",
            "Занятие указывают только у заявки на перенос.",
        )

    updated = repo.answer_family_request(
        cur,
        request_id,
        status=status,
        answer=answer,
        moved_to=moved_to,
        answered_by=user_id,
    )
    if not updated:
        # Условие `status = 'pending'` в UPDATE — ключ гонки: два
        # администратора, открывшие очередь одновременно, не ответят дважды.
        raise ApiError(
            409,
            "already_answered",
            "Заявка уже рассмотрена. Повторный ответ ничего не меняет.",
        )

    cur.execute(
        """
        UPDATE task SET done_at = now(), done_by = %s
        WHERE dedup_key = %s AND done_at IS NULL
        """,
        (user_id, f"family_request:{request_id}"),
    )

    row = repo.get_family_request(cur, request_id)
    _notify(
        cur,
        tenant_id,
        person_id=_requester_person(cur, request_id),
        to_address=row["requested_by_phone"],
        template="family_request_answered",
        payload={
            "request_id": request_id,
            "kind": row["kind"],
            "status": status,
            "answer": answer,
            "student_name": row["student_name"],
        },
        dedup_key=f"family_request_answered:{request_id}",
    )
    journal.audit(
        cur,
        tenant_id,
        user_id,
        "family_request.answer",
        "family_request",
        request_id,
        {"status": status, "moved_to": moved_to},
    )

    out = _request_out(row)
    out["message"] = (
        "Заявка принята, родителю уйдёт сообщение."
        if status == "accepted"
        else "Заявка отклонена, родителю уйдёт объяснение."
    )
    return out


def _requester_person(cur: psycopg.Cursor, request_id: str) -> str | None:
    cur.execute(
        """
        SELECT u.person_id FROM family_request fr
        JOIN app_user u ON u.id = fr.requested_by
        WHERE fr.id = %s
        """,
        (request_id,),
    )
    row = cur.fetchone()
    return None if row is None else str(row["person_id"])
