"""Отметка посещаемости и её отмена.

Одно действие администратора порождает четыре записи: отметку, движение
по абонементу, начисление преподавателю и строку аудита. Всё это происходит
в одной транзакции — частично применённая отметка означала бы, что занятие
списано, а зарплата не начислена (или наоборот), и сверить это потом
невозможно.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg

from . import journal, repository as repo
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


def require_actor(cur: psycopg.Cursor, user_id: str) -> str:
    """Проверяет, что X-User-Id — настоящая учётная запись этой школы.

    TODO: заглушка авторизации. На следующем этапе идентичность приезжает
    из токена; пока проверяем хотя бы существование, иначе аудит писался бы
    на несуществующего автора, а внешний ключ ронял бы отметку в 500.
    """
    cur.execute("SELECT id FROM app_user WHERE id = %s AND is_active", (user_id,))
    if cur.fetchone() is None:
        raise ApiError(
            401,
            "unknown_user",
            "Заголовок X-User-Id указывает на неизвестную учётную запись.",
        )
    return user_id


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

    # Уникальный индекс attendance (lesson_id, student_id) не частичный: отменённая
    # отметка продолжает занимать место. Ловим это заранее — сообщение «сначала
    # отмените прежнюю» сразу после отмены звучало бы издевательством.
    cur.execute(
        "SELECT id, mark, revoked_at FROM attendance WHERE lesson_id = %s AND student_id = %s",
        (lesson_id, student_id),
    )
    existing = cur.fetchone()
    if existing is not None:
        if existing["revoked_at"] is None:
            raise ApiError(
                409,
                "already_marked",
                "Ученик уже отмечен на этом занятии. Сначала отмените прежнюю отметку.",
                {"attendance_id": str(existing["id"]), "mark": existing["mark"]},
            )
        raise ApiError(
            409,
            "mark_revoked",
            "Отметка по этому ученику на этом занятии уже отменялась. Повторно "
            "отметить то же занятие база не даёт — проведите его как отработку.",
            {"attendance_id": str(existing["id"])},
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

    # 1. Отметка. Уникальный индекс (lesson_id, student_id) ловит двойной клик
    #    и повторную доставку запроса — ошибка переводится в 409 выше по стеку.
    cur.execute(
        """
        INSERT INTO attendance (tenant_id, lesson_id, student_id, mark, marked_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, marked_at
        """,
        (tenant_id, lesson_id, student_id, mark, actor_id),
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
            )
            # Отработка — отдельная валюта с собственным сроком: без поштучного
            # учёта «сгорает через 30 дней» не с чего отсчитывать.
            ttl = int(subscription.rule("makeup_ttl_days"))
            cur.execute(
                """
                INSERT INTO makeup_credit
                    (tenant_id, subscription_id, student_id, granted_for, expires_on)
                VALUES (%s, %s, %s, %s, current_date + %s)
                """,
                (tenant_id, subscription.id, student_id, lesson_id, ttl),
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
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Отмена отметки
# ---------------------------------------------------------------------------


def revoke_mark(
    cur: psycopg.Cursor, tenant_id: str, actor_id: str, attendance_id: str
) -> dict[str, Any]:
    cur.execute(
        """
        SELECT a.id, a.lesson_id, a.student_id, a.mark, a.revoked_at, l.teacher_id
        FROM attendance a
        JOIN lesson l ON l.id = a.lesson_id
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
            )
            lessons_back += -entry["lessons_delta"]
        if entry["makeups_delta"]:
            # Обратной операции для makeup_grant в перечне kind нет: makeup_use
            # означает «отработку потратили на занятие», а здесь её отзывают.
            # Ближайшее честное — adjust с явной причиной.
            _add_entry(
                cur,
                tenant_id,
                subscription_id,
                kind="adjust",
                lessons_delta=0,
                makeups_delta=-entry["makeups_delta"],
                attendance_id=None,
                lesson_id=str(att["lesson_id"]),
                reason="Отмена ошибочной отметки: отработка отозвана",
                actor_id=actor_id,
                reverses_id=entry["id"],
            )
            makeups_back += -entry["makeups_delta"]
            # Неиспользованная отработка, выданная по ошибке, удаляется:
            # её срок жизни иначе продолжал бы тикать, а сама она осталась бы
            # доступной для записи на занятие.
            cur.execute(
                """
                DELETE FROM makeup_credit
                WHERE subscription_id = %s AND granted_for = %s
                  AND used_at IS NULL AND expired_at IS NULL
                """,
                (subscription_id, att["lesson_id"]),
            )

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
        },
    )

    return {
        "attendance_id": attendance_id,
        "mark": att["mark"],
        "revoked_at": revoked_at.isoformat(),
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
