"""Отметка посещаемости и её отмена.

Одно действие администратора порождает четыре записи: отметку, движение
по абонементу, начисление преподавателю и строку аудита. Всё это происходит
в одной транзакции — частично применённая отметка означала бы, что занятие
списано, а зарплата не начислена (или наоборот), и сверить это потом
невозможно.

Дата операции приходит параметром `effective_date` и по умолчанию равна
сегодняшнему дню филиала (ADR-001). Отдельно от неё живёт дата занятия:
абонемент ищется по дате урока, потому что списывается именно за урок,
а `effective_date` отвечает на другой вопрос — когда администратор внёс
запись. Их путаница и была причиной issue #15.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

import psycopg

from . import journal, opdate, repository as repo
from .errors import ApiError, not_found
from .rules import MARKS, MarkEffect, compute_effect, low_balance_alert


def _lesson_or_404(cur: psycopg.Cursor, lesson_id: str) -> dict[str, Any]:
    lesson = repo.get_lesson(cur, lesson_id)
    if lesson is None:
        # Чужой тенант сюда тоже попадает: RLS отсекает строку до нас.
        raise not_found("Занятие не найдено")
    return lesson


def _require_participant(cur: psycopg.Cursor, lesson: dict[str, Any], student_id: str) -> str:
    participants = repo.lesson_participants(cur, lesson)
    for p in participants:
        if str(p["student_id"]) == student_id:
            return p["name"]
    if not participants and lesson["lead_id"] is not None:
        raise ApiError(
            422,
            "lead_lesson",
            "Пробное занятие оформлено на заявку. Отметить посещаемость можно "
            "только после конверсии заявки в ученика.",
            {"lead_id": str(lesson["lead_id"])},
        )
    raise ApiError(
        404,
        "not_a_participant",
        "Этот ученик не участвует в занятии.",
        {"student_id": student_id},
    )


# Проверки «а существует ли такой автор» здесь больше нет: она была нужна,
# пока автор приезжал заголовком X-User-Id и мог быть выдуман. Теперь автор —
# это учётная запись из сессии, найденная в базе при каждом запросе
# (authz.load_actor), и записать аудит на несуществующего человека
# технически невозможно.


def preview_effect(
    cur: psycopg.Cursor, lesson: dict[str, Any], student_id: str, mark: str
) -> tuple[MarkEffect, Any]:
    """Расчёт последствий без записи. Ровно та же функция, что и при применении."""
    subscription = repo.active_subscription(cur, student_id, lesson["starts_at"].date())
    amount, percent = repo.teacher_rate(cur, lesson)
    return compute_effect(mark, subscription, amount, percent), subscription


# ---------------------------------------------------------------------------
# Применение отметки
# ---------------------------------------------------------------------------


def apply_mark(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    lesson_id: str,
    student_id: str,
    mark: str,
    effective_date: dt.date | None = None,
) -> dict[str, Any]:
    if mark not in MARKS:
        raise ApiError(
            400,
            "bad_mark",
            f"Неизвестная отметка «{mark}». Допустимые: {', '.join(MARKS)}.",
        )

    lesson = _lesson_or_404(cur, lesson_id)
    if lesson["status"] == "cancelled":
        raise ApiError(409, "lesson_cancelled", "Занятие отменено — отмечать нечего.")
    _require_participant(cur, lesson, student_id)

    # Дата операции проверяется до всякой записи: окно правки школы, а поверх
    # него — закрытый зарплатный период, который не открывает никакое окно.
    when = opdate.resolve(cur, tenant_id, effective_date, lesson["branch_timezone"])
    opdate.refuse_closed_payroll(cur, when)

    # Действующая отметка — ровно одна на пару (занятие, ученик); это же
    # держит частичный индекс attendance_active_uniq. Проверяем и здесь,
    # чтобы в ответе была прежняя отметка: «уже отмечен» без «чем именно»
    # не говорит администратору, надо ли вообще что-то менять.
    #
    # Отменённые строки в условие не входят намеренно. Пока индекс был
    # сплошным, отменённая отметка продолжала занимать ключ, и после отмены
    # ошибочной отметки занятие оставалось неотмеченным навсегда — тупик,
    # из которого приложение умело только вежливо объясниться.
    cur.execute(
        """
        SELECT id, mark FROM attendance
        WHERE lesson_id = %s AND student_id = %s AND revoked_at IS NULL
        """,
        (lesson_id, student_id),
    )
    existing = cur.fetchone()
    if existing is not None:
        raise ApiError(
            409,
            "already_marked",
            "Ученик уже отмечен на этом занятии. Сначала отмените прежнюю отметку.",
            {"attendance_id": str(existing["id"]), "mark": existing["mark"]},
        )

    # Блокируем абонемент до конца транзакции: без этого два одновременных
    # списания с остатком 1 оба прошли бы проверку и увели баланс в минус.
    subscription = repo.active_subscription(
        cur, student_id, lesson["starts_at"].date(), for_update=True
    )
    amount, percent = repo.teacher_rate(cur, lesson)
    effect = compute_effect(mark, subscription, amount, percent)

    if effect.blocked_reason:
        raise ApiError(
            422,
            "no_lessons_left",
            effect.blocked_reason,
            {"subscription_id": subscription.id if subscription else None},
        )

    # 1. Отметка. Частичный индекс attendance_active_uniq ловит двойной клик
    #    и повторную доставку запроса в гонке, когда проверка выше прошла
    #    у обоих запросов, — ошибка переводится в 409 выше по стеку.
    columns = ["tenant_id", "lesson_id", "student_id", "mark", "marked_by"]
    values: list[Any] = [tenant_id, lesson_id, student_id, mark, actor_id]
    # Пометка «внесено задним числом» приезжает миграцией 009; до неё
    # отметка пишется как раньше — см. opdate.marks_backdating().
    if opdate.marks_backdating(cur, "attendance"):
        columns.append("backdated")
        values.append(when.backdated)
    cur.execute(
        f"INSERT INTO attendance ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(values))}) RETURNING id, marked_at",
        values,
    )
    row = cur.fetchone()
    attendance_id = str(row["id"])

    # 2. Журнал абонемента. Остаток пересчитает триггер — сами его не трогаем.
    if subscription is not None:
        if effect.lessons_delta != 0:
            _add_entry(
                cur,
                tenant_id,
                subscription.id,
                kind="charge" if effect.lessons_delta < 0 else "adjust",
                lessons_delta=effect.lessons_delta,
                makeups_delta=0,
                attendance_id=attendance_id,
                lesson_id=lesson_id,
                reason=f"Отметка «{mark}»",
                actor_id=actor_id,
                backdated=when.backdated,
            )
        if effect.makeups_delta != 0:
            _add_entry(
                cur,
                tenant_id,
                subscription.id,
                kind="makeup_grant",
                lessons_delta=0,
                makeups_delta=effect.makeups_delta,
                attendance_id=attendance_id,
                lesson_id=lesson_id,
                reason=f"Отметка «{mark}»",
                actor_id=actor_id,
                backdated=when.backdated,
            )
            # Отработка — отдельная валюта с собственным сроком: без поштучного
            # учёта «сгорает через 30 дней» не с чего отсчитывать.
            #
            # Срок считается от даты пропущенного занятия, а не от current_date.
            # «Сгорает через 30 дней» родитель понимает как 30 дней от урока,
            # который не состоялся, — эта дата есть в договоре и он может её
            # проверить. От даты ввода отметки срок зависел бы от расторопности
            # администратора: отметка, внесённая через неделю, давала бы
            # отработке лишнюю неделю жизни, и двум родителям с одинаково
            # пропущенным уроком назвали бы разные даты.
            #
            # Дата берётся в поясе филиала: занятие 20:00 в Алматы для сервера
            # в UTC приходится ещё на предыдущие сутки, и срок начинался бы
            # на день раньше, чем стоит в расписании.
            ttl = int(subscription.rule("makeup_ttl_days"))
            cur.execute(
                """
                INSERT INTO makeup_credit
                    (tenant_id, subscription_id, student_id, granted_for, expires_on)
                VALUES (%s, %s, %s, %s, (%s::timestamptz AT TIME ZONE %s)::date + %s)
                """,
                (
                    tenant_id,
                    subscription.id,
                    student_id,
                    lesson_id,
                    lesson["starts_at"],
                    lesson["branch_timezone"] or "Asia/Almaty",
                    ttl,
                ),
            )

    # 3. Зарплата. Нулевое начисление не пишем — это шум в ведомости.
    if effect.teacher_amount != 0:
        cur.execute(
            """
            INSERT INTO payroll_entry
                (tenant_id, staff_id, lesson_id, attendance_id, kind, amount, calc)
            VALUES (%s, %s, %s, %s, 'lesson', %s, %s)
            """,
            (
                tenant_id,
                lesson["teacher_id"],
                lesson_id,
                attendance_id,
                effect.teacher_amount,
                json.dumps(effect.payroll_calc, ensure_ascii=False),
            ),
        )

    # 4. Занятие считается проведённым после первой отметки (контракт).
    if lesson["status"] == "planned":
        cur.execute("UPDATE lesson SET status = 'held', updated_at = now() WHERE id = %s", (lesson_id,))
    lesson_status = "held" if lesson["status"] == "planned" else lesson["status"]

    _audit(
        cur,
        tenant_id,
        actor_id,
        "attendance.mark",
        "attendance",
        attendance_id,
        {
            "lesson_id": lesson_id,
            "student_id": student_id,
            "mark": mark,
            "lessons_delta": effect.lessons_delta,
            "makeups_delta": effect.makeups_delta,
            "teacher_amount": effect.teacher_amount,
            "subscription_id": subscription.id if subscription else None,
            "effective_date": when.on.isoformat(),
            "backdated": when.backdated,
        },
    )

    alerts = []
    if subscription is not None:
        alert = low_balance_alert(effect.lessons_after)
        if alert:
            alerts.append(alert)

    return {
        "attendance_id": attendance_id,
        "mark": mark,
        "applied": {
            "lessons_delta": effect.lessons_delta,
            "lessons_after": effect.lessons_after,
            "makeups_delta": effect.makeups_delta,
            "makeups_after": effect.makeups_after,
            "teacher_amount": effect.teacher_amount,
            "teacher_id": str(lesson["teacher_id"]),
            "subscription_id": subscription.id if subscription else None,
        },
        "lesson_status": lesson_status,
        # Дата операции и пометка — наружу: интерфейс обязан показать, что
        # запись внесена задним числом, ровно там, где её только что внесли.
        "effective_date": when.on.isoformat(),
        "backdated": when.backdated,
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Отмена отметки
# ---------------------------------------------------------------------------


def revoke_mark(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    attendance_id: str,
    effective_date: dt.date | None = None,
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT a.id, a.lesson_id, a.student_id, a.mark, a.revoked_at, l.teacher_id,
               coalesce(b.timezone, t.timezone) AS branch_timezone
        FROM attendance a
        JOIN lesson l ON l.id = a.lesson_id
        JOIN tenant t ON t.id = a.tenant_id
        LEFT JOIN branch b ON b.id = l.branch_id
        WHERE a.id = %s
        FOR UPDATE OF a
        """,
        (attendance_id,),
    )
    att = cur.fetchone()
    if att is None:
        raise not_found("Отметка не найдена")
    if att["revoked_at"] is not None:
        raise ApiError(409, "already_revoked", "Эта отметка уже отменена.")

    # Отмена пишет корректировку в зарплату, поэтому подчиняется тем же
    # границам, что и сама отметка: окну правки и закрытому периоду.
    when = opdate.resolve(cur, tenant_id, effective_date, att["branch_timezone"])
    opdate.refuse_closed_payroll(cur, when)

    # Гасим ровно те записи, которые породила отметка: суммы берём из журнала,
    # а не пересчитываем правилами заново. Правила школы могли смениться между
    # отметкой и её отменой, и пересчёт вернул бы не то, что списывал.
    cur.execute(
        """
        SELECT id, subscription_id, kind, lessons_delta, makeups_delta
        FROM subscription_entry
        WHERE attendance_id = %s AND kind IN ('charge', 'makeup_grant')
        ORDER BY id
        """,
        (attendance_id,),
    )
    entries = cur.fetchall()

    subscription_id: str | None = None
    lessons_back = 0
    makeups_back = 0
    for entry in entries:
        subscription_id = str(entry["subscription_id"])
        if entry["lessons_delta"]:
            _add_entry(
                cur,
                tenant_id,
                subscription_id,
                kind="refund",
                lessons_delta=-entry["lessons_delta"],
                makeups_delta=0,
                attendance_id=None,  # уникальный индекс держит одну charge на отметку
                lesson_id=str(att["lesson_id"]),
                reason="Отмена ошибочной отметки",
                actor_id=actor_id,
                reverses_id=entry["id"],
                backdated=when.backdated,
            )
            lessons_back += -entry["lessons_delta"]
        if entry["makeups_delta"] > 0:
            # Сначала отзываем сами отработки, и только потом пишем журнал:
            # компенсировать можно ровно то, что удалось отозвать. Отработка,
            # уже потраченная на занятие или сгоревшая по сроку, из баланса
            # ушла — вычесть её второй раз значит увести баланс в минус
            # и показать родителю «−1 отработка», которой никогда не было.
            #
            # Неиспользованная отработка при этом удаляется, а не помечается:
            # иначе её срок продолжал бы тикать, а сама она осталась бы
            # доступной для записи на занятие.
            #
            # LIMIT по числу выданных: одна отметка отзывает ровно столько
            # отработок, сколько сама начислила, даже если по этому занятию
            # их в базе почему-то больше.
            cur.execute(
                """
                DELETE FROM makeup_credit
                WHERE ctid IN (
                    SELECT ctid FROM makeup_credit
                    WHERE subscription_id = %s AND granted_for = %s
                      AND used_at IS NULL AND expired_at IS NULL
                    ORDER BY expires_on DESC, created_at DESC
                    LIMIT %s
                )
                RETURNING id
                """,
                (subscription_id, att["lesson_id"], int(entry["makeups_delta"])),
            )
            revoked = len(cur.fetchall())
            if revoked:
                # Обратной операции для makeup_grant в перечне kind нет:
                # makeup_use означает «отработку потратили на занятие»,
                # а здесь её отзывают. Ближайшее честное — adjust с причиной.
                _add_entry(
                    cur,
                    tenant_id,
                    subscription_id,
                    kind="adjust",
                    lessons_delta=0,
                    makeups_delta=-revoked,
                    attendance_id=None,
                    lesson_id=str(att["lesson_id"]),
                    reason="Отмена ошибочной отметки: отработка отозвана",
                    actor_id=actor_id,
                    reverses_id=entry["id"],
                    backdated=when.backdated,
                )
                makeups_back += -revoked

    # Зарплата гасится корректировкой, а не удалением строки: закрытый период
    # не пересчитывается, правки уходят следующим (spec.md §6.2).
    cur.execute(
        """
        SELECT id, staff_id, amount FROM payroll_entry
        WHERE attendance_id = %s AND kind = 'lesson'
        """,
        (attendance_id,),
    )
    teacher_amount_back = 0
    for pay in cur.fetchall():
        cur.execute(
            """
            INSERT INTO payroll_entry
                (tenant_id, staff_id, lesson_id, attendance_id, kind, amount, calc, reverses_id)
            VALUES (%s, %s, %s, NULL, 'correction', %s, %s, %s)
            """,
            (
                tenant_id,
                pay["staff_id"],
                att["lesson_id"],
                -pay["amount"],
                json.dumps({"reason": "revoke", "attendance_id": attendance_id}, ensure_ascii=False),
                pay["id"],
            ),
        )
        teacher_amount_back += -int(pay["amount"])

    cur.execute(
        "UPDATE attendance SET revoked_at = now(), revoked_by = %s WHERE id = %s RETURNING revoked_at",
        (actor_id, attendance_id),
    )
    revoked_at = cur.fetchone()["revoked_at"]

    # Если действующих отметок не осталось, занятие снова «запланировано»:
    # иначе held без единой отметки выглядел бы проведённым и ушёл бы в отчёты.
    cur.execute(
        "SELECT count(*) AS n FROM attendance WHERE lesson_id = %s AND revoked_at IS NULL",
        (att["lesson_id"],),
    )
    remaining = int(cur.fetchone()["n"])
    if remaining == 0:
        cur.execute(
            "UPDATE lesson SET status = 'planned', updated_at = now() WHERE id = %s AND status = 'held'",
            (att["lesson_id"],),
        )
    cur.execute("SELECT status FROM lesson WHERE id = %s", (att["lesson_id"],))
    lesson_status = cur.fetchone()["status"]

    lessons_after = None
    makeups_after = None
    if subscription_id is not None:
        cur.execute(
            "SELECT lessons_balance, makeups_balance FROM subscription WHERE id = %s",
            (subscription_id,),
        )
        balance = cur.fetchone()
        lessons_after = int(balance["lessons_balance"])
        makeups_after = int(balance["makeups_balance"])

    _audit(
        cur,
        tenant_id,
        actor_id,
        "attendance.revoke",
        "attendance",
        attendance_id,
        {
            "lesson_id": str(att["lesson_id"]),
            "student_id": str(att["student_id"]),
            "mark": att["mark"],
            "lessons_delta": lessons_back,
            "makeups_delta": makeups_back,
            "teacher_amount": teacher_amount_back,
            "subscription_id": subscription_id,
            "effective_date": when.on.isoformat(),
            "backdated": when.backdated,
        },
    )

    return {
        "attendance_id": attendance_id,
        "mark": att["mark"],
        "revoked_at": revoked_at.isoformat(),
        "effective_date": when.on.isoformat(),
        "backdated": when.backdated,
        "reverted": {
            "lessons_delta": lessons_back,
            "lessons_after": lessons_after,
            "makeups_delta": makeups_back,
            "makeups_after": makeups_after,
            "teacher_amount": teacher_amount_back,
            "teacher_id": str(att["teacher_id"]),
            "subscription_id": subscription_id,
        },
        "lesson_status": lesson_status,
    }


# ---------------------------------------------------------------------------


# Запись в журналы живёт в journal.py: у неё теперь два потребителя —
# отметка посещаемости и продажа абонемента.
_add_entry = journal.add_entry
_audit = journal.audit
