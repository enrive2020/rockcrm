"""Жизненный цикл абонемента: продажа, продление, заморозка и её снятие.

Каждая операция — одна транзакция. Абонемент без записи `purchase` в журнале
означал бы остаток, взявшийся ниоткуда, а весь смысл журнала в том, что он
объясняет каждое занятие. Заморозка без записи в журнале, без сдвига срока
или без отмены занятий внутри интервала — то же самое с другой стороны:
половина операции, которую никто потом не восстановит.

Остаток здесь нигде не считается: его ведёт триггер базы от журнала.
Все ответы читают `lessons_balance` из базы уже после вставки записей.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from . import journal, opdate, repository as repo
from .errors import ApiError, not_found
from .rules import (
    DEFAULT_RULES,
    days_word,
    freeze_reason,
    lessons_word,
    makeup_expire_reason,
    unfreeze_reason,
)


def _charged(price: Decimal, discount_pct: Decimal) -> int:
    """Сумма к оплате после скидки, в целых тенге.

    Округление ROUND_HALF_UP, а не банковское: школа выставляет счёт человеку,
    и 48 599,5 ₸ должно стать 48 600, как посчитал бы администратор на бумаге.
    """
    total = Decimal(price) * (Decimal(100) - Decimal(discount_pct)) / Decimal(100)
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# «Сегодня» больше не считается здесь: и оно, и дата операции приезжают
# из opdate.resolve(). Два источника «сегодня» в продаже и в заморозке
# однажды разошлись бы на сутки — на границе часового пояса это вопрос
# времени, а не удачи.


# ---------------------------------------------------------------------------
# Продажа и продление
# ---------------------------------------------------------------------------


def sell_subscription(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    student_id: str,
    body: Any,
) -> dict[str, Any]:
    """Продажа абонемента. Продление — та же операция: следующий абонемент.

    Всё в одной транзакции: `subscription`, запись `purchase` в журнал,
    перенос остатка при необходимости, платёж, аудит.
    """
    student = repo.get_student(cur, student_id)
    if student is None:
        raise not_found("Ученик не найден")

    plan = repo.get_plan(cur, body.plan_id)
    if plan is None:
        # Тариф чужой школы отсекает RLS — до нас он не доезжает, и ответ
        # обязан быть тем же, что и для несуществующего.
        raise not_found("Тариф не найден")
    if not plan["is_active"]:
        raise ApiError(
            422,
            "plan_inactive",
            f"Тариф «{plan['name']}» снят с продажи. Выберите действующий тариф.",
            {"plan_id": str(plan["id"])},
        )

    tz_name = student["branch_timezone"]
    # Дата операции: по умолчанию сегодня в поясе филиала, но администратор
    # может внести продажу задним числом в пределах окна школы (ADR-001).
    when = opdate.resolve(cur, tenant_id, body.effective_date, tz_name)
    # Первый день действия по умолчанию равен дате операции, а не «сегодня»:
    # абонемент, проданный за прошлый понедельник, с понедельника и действует,
    # иначе неделя занятий осталась бы без покрытия и списывать было бы не с чего.
    starts_on = body.starts_on or when.on
    # valid_days — срок жизни в днях, включая первый: месячный абонемент,
    # проданный 1 августа, действует до 31-го, а не до 1 сентября.
    valid_until = starts_on + dt.timedelta(days=int(plan["valid_days"]) - 1)

    # Правила копируются из настроек школы НА МОМЕНТ ПРОДАЖИ, а не из
    # предыдущего абонемента: старый жил по условиям своей покупки,
    # новый живёт по текущим (spec.md §4.2).
    rules = dict(DEFAULT_RULES)
    rules.update(repo.tenant_rules(cur, tenant_id))

    family_id = student["family_id"]
    discount_pct = body.discount_pct
    if discount_pct is None:
        discount_pct = _family_discount(cur, family_id)
    discount_pct = Decimal(str(discount_pct))

    _refuse_overlap(cur, student_id, starts_on, valid_until, rules)

    price = Decimal(plan["price"])
    charged = _charged(price, discount_pct)
    lessons_total = int(plan["lessons_count"])

    cur.execute(
        """
        INSERT INTO subscription
            (tenant_id, student_id, family_id, plan_id, lessons_total, price,
             discount_pct, promo_code, rules, valid_from, valid_until, sold_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tenant_id,
            student_id,
            family_id,
            plan["id"],
            lessons_total,
            price,
            discount_pct,
            body.promo_code,
            json.dumps(rules, ensure_ascii=False),
            starts_on,
            valid_until,
            actor_id,
        ),
    )
    subscription_id = str(cur.fetchone()["id"])

    # Журнал наполняется сразу: без записи `purchase` остаток был бы нулём,
    # и первая же отметка получила бы 422 «списывать нечего».
    journal.add_entry(
        cur,
        tenant_id,
        subscription_id,
        kind="purchase",
        lessons_delta=lessons_total,
        makeups_delta=0,
        reason=f"Продажа абонемента «{plan['name']}»",
        actor_id=actor_id,
        backdated=when.backdated,
    )

    carried, carry_note = _carry_over(
        cur, tenant_id, actor_id, student_id, subscription_id, starts_on, rules,
        body.carry_over, when.backdated,
    )

    payment_id = None
    paid = 0
    if body.payment is not None:
        cur.execute(
            """
            INSERT INTO payment
                (tenant_id, family_id, subscription_id, amount, method, accepted_by, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                family_id,
                subscription_id,
                body.payment.amount,
                body.payment.method,
                actor_id,
                f"Оплата абонемента «{plan['name']}»",
            ),
        )
        payment_id = str(cur.fetchone()["id"])
        paid = int(body.payment.amount)

    debt = max(charged - paid, 0)

    # Остаток читаем из базы: его посчитал триггер по журналу. Сложить
    # lessons_total и carried в коде значило бы завести второй источник
    # правды об остатке — ровно то, против чего построена схема.
    cur.execute(
        "SELECT lessons_balance, valid_from, valid_until FROM subscription WHERE id = %s",
        (subscription_id,),
    )
    saved = cur.fetchone()

    journal.audit(
        cur,
        tenant_id,
        actor_id,
        "subscription.sell",
        "subscription",
        subscription_id,
        {
            "student_id": student_id,
            "plan_id": str(plan["id"]),
            "lessons_total": lessons_total,
            "price": int(price),
            "discount_pct": float(discount_pct),
            "promo_code": body.promo_code,
            "charged": charged,
            "carried_over": carried,
            "payment_id": payment_id,
            "debt": debt,
            "valid_from": starts_on.isoformat(),
            "valid_until": valid_until.isoformat(),
            "effective_date": when.on.isoformat(),
            "backdated": when.backdated,
        },
    )

    return {
        "subscription_id": subscription_id,
        "lessons_total": lessons_total,
        "lessons_balance": int(saved["lessons_balance"]),
        "valid_from": saved["valid_from"].isoformat(),
        "valid_until": saved["valid_until"].isoformat(),
        "price": int(price),
        "discount_pct": float(discount_pct),
        "charged": charged,
        "carried_over": carried,
        "carry_over_note": carry_note,
        "payment_id": payment_id,
        "debt": debt,
        "effective_date": when.on.isoformat(),
        "backdated": when.backdated,
    }


def _family_discount(cur: psycopg.Cursor, family_id: Any) -> Decimal:
    """Скидка семьи как значение по умолчанию.

    Скидка «за второго ребёнка» задана на семье, и заставлять администратора
    вводить её руками при каждой продаже значит однажды её потерять.
    """
    if family_id is None:
        return Decimal(0)
    cur.execute("SELECT discount_pct FROM family WHERE id = %s", (family_id,))
    row = cur.fetchone()
    return Decimal(row["discount_pct"]) if row else Decimal(0)


def _refuse_overlap(
    cur: psycopg.Cursor,
    student_id: str,
    starts_on: dt.date,
    valid_until: dt.date,
    rules: dict[str, Any],
) -> None:
    """Второй действующий абонемент на тот же период.

    Блокируем только абонементы с непотраченным остатком: исчерпанный
    абонемент на текущий месяц — это обычная причина продать новый прямо
    сейчас, и запрещать такую продажу было бы вредительством.
    """
    if rules.get("allow_overlapping_subscriptions"):
        return
    cur.execute(
        """
        SELECT id, valid_from, valid_until, lessons_balance
        FROM subscription
        WHERE student_id = %(student)s
          AND status IN ('active', 'frozen')
          AND lessons_balance > 0
          AND valid_from  <= %(until)s
          AND valid_until >= %(from)s
        ORDER BY valid_until
        LIMIT 1
        """,
        {"student": student_id, "from": starts_on, "until": valid_until},
    )
    clash = cur.fetchone()
    if clash is None:
        return
    balance = int(clash["lessons_balance"])
    raise ApiError(
        422,
        "subscription_overlap",
        f"У ученика уже есть абонемент до {clash['valid_until']:%d.%m.%Y}, "
        f"на нём осталось {balance} {lessons_word(balance)}. "
        f"Продлевать нужно с {clash['valid_until'] + dt.timedelta(days=1):%d.%m.%Y}.",
        {
            "subscription_id": str(clash["id"]),
            "valid_until": clash["valid_until"].isoformat(),
            "lessons_balance": balance,
        },
    )


def _carry_over(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    student_id: str,
    new_subscription_id: str,
    starts_on: dt.date,
    rules: dict[str, Any],
    requested: bool,
    backdated: bool = False,
) -> tuple[int, str | None]:
    """Перенос неиспользованного остатка на новый абонемент.

    Возвращает (сколько перенесено, объяснение для администратора).
    Объяснение обязательно: по умолчанию правило равно нулю, перенос
    не делается, и молчаливый `carried_over: 0` выглядел бы как сбой.
    """
    limit = int(rules.get("carry_over_lessons", 0) or 0)
    if not requested:
        return 0, None
    if limit <= 0:
        return 0, (
            "Перенос остатка выключен в настройках школы "
            "(правило carry_over_lessons = 0) — занятия не перенесены."
        )

    cur.execute(
        """
        SELECT id, lessons_balance, valid_until
        FROM subscription
        WHERE student_id = %s
          AND id <> %s
          AND status IN ('active', 'frozen', 'exhausted', 'expired')
          AND lessons_balance > 0
        ORDER BY valid_until DESC
        LIMIT 1
        FOR UPDATE
        """,
        (student_id, new_subscription_id),
    )
    previous = cur.fetchone()
    if previous is None:
        return 0, "Переносить нечего: на прошлом абонементе не осталось занятий."

    carried = min(int(previous["lessons_balance"]), limit)
    previous_id = str(previous["id"])

    # Две записи, а не одна: остаток обоих абонементов ведёт триггер от
    # журнала, и списание со старого обязано быть видно в его журнале —
    # иначе родитель увидит, что занятия исчезли без объяснения.
    journal.add_entry(
        cur,
        tenant_id,
        previous_id,
        kind="transfer_out",
        lessons_delta=-carried,
        makeups_delta=0,
        reason=f"Перенос остатка на абонемент с {starts_on:%d.%m.%Y}",
        actor_id=actor_id,
        backdated=backdated,
    )
    journal.add_entry(
        cur,
        tenant_id,
        new_subscription_id,
        kind="transfer_in",
        lessons_delta=carried,
        makeups_delta=0,
        reason="Перенос остатка с прошлого абонемента",
        actor_id=actor_id,
        backdated=backdated,
    )
    note = (
        f"Перенесено {carried} {lessons_word(carried)} "
        f"(лимит школы — {limit})."
    )
    return carried, note


# ---------------------------------------------------------------------------
# Заморозка
# ---------------------------------------------------------------------------


def create_hold(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    subscription_id: str,
    body: Any,
) -> dict[str, Any]:
    """Заморозка: запись, сдвиг срока, отмена занятий внутри интервала, аудит.

    Интервал полуоткрытый, [from, to): так его хранит daterange в схеме,
    и число замороженных дней равно разности дат. «С 14 по 25» — это 11 дней
    и занятия 14–24 числа.
    """
    sub = repo.subscription_context(cur, subscription_id)
    if sub is None:
        raise not_found("Абонемент не найден")

    tz_name = sub["branch_timezone"] or "Asia/Almaty"
    tz = ZoneInfo(tz_name)
    # Дата операции: с неё и отсчитывается «прошлое» для заморозки (ADR-001).
    when = opdate.resolve(cur, tenant_id, body.effective_date, tz_name)
    today = when.on

    if body.to <= body.from_:
        raise ApiError(
            422,
            "bad_period",
            "Дата окончания заморозки должна быть позже даты начала. "
            "Последний день заморозки в интервал не входит.",
        )
    if body.from_ < today:
        # Заморозка не может начинаться раньше даты операции. Раньше здесь
        # стояли системные часы, и внести каникулы за прошлую неделю было
        # нельзя вовсе; теперь администратор говорит, каким числом вносит,
        # а глубину этого «вчера» ограничивает окно школы backdating_days.
        #
        # Смысл запрета остался прежним: заморозка раньше даты операции
        # пересчитала бы занятия, которые в этот интервал уже отмечены,
        # и сдвинула бы срок, который родителю уже назвали.
        raise ApiError(
            422,
            "hold_in_past",
            f"Заморозка не может начинаться раньше даты операции "
            f"{today:%d.%m.%Y}.",
            {"today": today.isoformat(), "effective_date": when.on.isoformat()},
        )

    days = (body.to - body.from_).days

    # Пересечение проверяем раньше лимита дней: наложенный интервал сначала
    # съел бы лимит и вернул бы «дней не осталось» — сообщение верное
    # по цифрам и бесполезное по сути. Ограничение исключения в базе остаётся
    # последней линией обороны на случай гонки двух администраторов
    # и переводится в тот же 409 в errors.py.
    cur.execute(
        """
        SELECT id, lower(period) AS from_day, upper(period) AS to_day
        FROM subscription_hold
        WHERE subscription_id = %s AND period && daterange(%s, %s, '[)')
        LIMIT 1
        """,
        (subscription_id, body.from_, body.to),
    )
    clash = cur.fetchone()
    if clash is not None:
        raise ApiError(
            409,
            "hold_overlap",
            f"Интервал пересекается с заморозкой "
            f"{clash['from_day']:%d.%m.%Y}–{clash['to_day']:%d.%m.%Y}. "
            "Заморозки одного абонемента не могут перекрываться: срок сдвинулся бы "
            "дважды за одни и те же дни.",
            {
                "hold_id": str(clash["id"]),
                "from": clash["from_day"].isoformat(),
                "to": clash["to_day"].isoformat(),
            },
        )

    rules = dict(DEFAULT_RULES)
    rules.update(sub["rules"] or {})
    limit = int(rules.get("freeze_days_per_year", 0))
    used = repo.freeze_days_used(cur, str(sub["student_id"]), body.from_.year)
    left = max(limit - used, 0)
    if days > left:
        raise ApiError(
            422,
            "freeze_limit",
            f"Лимит заморозки — {limit} {days_word(limit)} в год. "
            f"За {body.from_.year} уже использовано {used}, осталось {left}, "
            f"а запрошено {days}.",
            {"limit": limit, "used": used, "days_left": left, "requested": days},
        )

    valid_until_before = sub["valid_until"]

    # Ограничение исключения subscription_hold_..._excl ловит пересечение
    # заморозок и переводится в 409 в errors.py: два интервала на одни и те же
    # дни продлили бы срок дважды за одни сутки.
    cur.execute(
        """
        INSERT INTO subscription_hold (tenant_id, subscription_id, period, reason, created_by)
        VALUES (%s, %s, daterange(%s, %s, '[)'), %s, %s)
        RETURNING id
        """,
        (tenant_id, subscription_id, body.from_, body.to, body.reason, actor_id),
    )
    hold_id = str(cur.fetchone()["id"])

    cur.execute(
        """
        UPDATE subscription
        SET valid_until = valid_until + %s
        WHERE id = %s
        RETURNING valid_until
        """,
        (days, subscription_id),
    )
    valid_until_after = cur.fetchone()["valid_until"]

    # Статус приводится в соответствие с заморозками. Схема объявляет `frozen`,
    # триггер его бережёт, три запроса на него смотрят — но никто его
    # не проставлял, и замороженный абонемент был неотличим от действующего:
    # администратор не видел, что ученик на каникулах, и спрашивал бы,
    # почему тот не ходит.
    status = _sync_freeze_status(cur, subscription_id, today)

    cancelled = _cancel_lessons(cur, str(sub["student_id"]), body.from_, body.to, tz)

    # Заморозка идёт в журнал абонемента наравне со списаниями. Баланс она
    # не двигает (обе дельты нулевые — схема разрешает это только виду
    # 'freeze'), но родителю, спрашивающему «почему абонемент кончается
    # позже», отвечают именно журналом, а не аудитом: аудита он не видит.
    entry_id = journal.add_entry(
        cur,
        tenant_id,
        subscription_id,
        kind="freeze",
        lessons_delta=0,
        makeups_delta=0,
        reason=freeze_reason(body.from_, body.to, days, body.reason),
        actor_id=actor_id,
        backdated=when.backdated,
    )

    journal.audit(
        cur,
        tenant_id,
        actor_id,
        "subscription.hold",
        "subscription",
        subscription_id,
        {
            "hold_id": hold_id,
            "entry_id": entry_id,
            "from": body.from_.isoformat(),
            "to": body.to.isoformat(),
            "days": days,
            "reason": body.reason,
            "valid_until_before": valid_until_before.isoformat(),
            "valid_until_after": valid_until_after.isoformat(),
            "lessons_cancelled": len(cancelled),
            "lesson_ids": cancelled,
            "effective_date": when.on.isoformat(),
            "backdated": when.backdated,
        },
    )

    return {
        "hold_id": hold_id,
        "days": days,
        "valid_until_before": valid_until_before.isoformat(),
        "valid_until_after": valid_until_after.isoformat(),
        "lessons_cancelled": len(cancelled),
        "freeze_days_left": max(left - days, 0),
        "status": status,
        "effective_date": when.on.isoformat(),
        "backdated": when.backdated,
        "message": (
            f"Заморозка на {days} {days_word(days)}: "
            f"срок сдвинут на {valid_until_after:%d.%m.%Y}, "
            f"отменено занятий — {len(cancelled)}."
        ),
    }


# Статус абонемента, выведенный из фактов: действующая ли заморозка, остался
# ли остаток, не истёк ли срок. Единственное определение на систему — им
# пользуются и заморозка, и её снятие, и ночная сверка.
#
# Здесь единственное место, где абонемент становится `expired`. Триггер
# subscription_recalc этого больше не делает (миграция 009, ADR-001 пункт 5):
# он вычисляет статус из фактов, не зная «сегодня». Прежде простая вставка
# строки в журнал объявляла соседний абонемент истёкшим по системным часам,
# и абонемент, проданный задним числом, становился `expired` в ту же секунду —
# следующая отметка не находила, с чего списывать.
#
# Обе даты берутся из одного параметра %(today)s — дня филиала или даты
# операции. Разные источники «сегодня» в двух соседних ветках однажды дали бы
# абонемент, который одновременно и заморожен, и истёк.
_STATUS_FROM_FACTS = """
    CASE
      -- Отменённый абонемент замораживать нечего и незачем.
      WHEN s.status = 'cancelled' THEN s.status
      WHEN EXISTS (
        SELECT 1 FROM subscription_hold h
        WHERE h.subscription_id = s.id AND h.period @> %(today)s::date
      ) THEN 'frozen'
      WHEN s.lessons_balance <= 0 THEN 'exhausted'
      WHEN s.valid_until < %(today)s::date THEN 'expired'
      ELSE 'active'
    END
"""


def _sync_freeze_status(cur: psycopg.Cursor, subscription_id: str, today: dt.date) -> str:
    """Приводит статус одного абонемента в соответствие с его заморозками.

    Статус `frozen` ставится, только если заморозка действует **сегодня**.
    Заморозка, назначенная на будущее, статус не меняет — и это осознанно:
    до её начала абонемент действительно действует, занятия идут, списания
    идут. Пометить его замороженным заранее значило бы показать
    администратору «на каникулах» ученика, который сегодня придёт на урок,
    а сам факт будущей заморозки виден в `holds` карточки.

    Обратная сторона: границу дат приложение само не переходит — оно
    вызывается только при создании и снятии заморозки. Календарь двигает
    sync_statuses(), см. её описание.
    """
    cur.execute(
        f"""
        UPDATE subscription s
        SET status = {_STATUS_FROM_FACTS}
        WHERE s.id = %(id)s
        RETURNING status
        """,
        {"id": subscription_id, "today": today},
    )
    return cur.fetchone()["status"]


def sync_statuses(cur: psycopg.Cursor, today: dt.date) -> list[dict[str, Any]]:
    """Ночная сверка статусов абонементов школы. Возвращает изменённые.

    Без неё половина заморозки повисает: `create_hold` переводит абонемент
    в `frozen` в день начала, а обратно его никто не возвращает — заморозка
    кончилась две недели назад, а карточка всё ещё показывает каникулы.
    Хуже того, триггер пересчёта бережёт `frozen` (`WHEN s.status IN
    ('cancelled','frozen')`), поэтому застрявший статус не даст абонементу
    стать ни `exhausted`, ни `expired`.

    Заморозка, назначенная на будущее, тоже становится действующей именно
    здесь — в день своего начала.

    Задание идемпотентно: оно не «переключает», а приводит статус к тому,
    что следует из фактов, и второй прогон подряд не меняет ничего.
    Запускать раз в сутки: `python -m scripts.sync_statuses`.
    """
    cur.execute(
        f"""
        WITH want AS (
          SELECT s.id, {_STATUS_FROM_FACTS} AS status
          FROM subscription s
        )
        UPDATE subscription s
        SET status = want.status
        FROM want
        WHERE want.id = s.id AND s.status IS DISTINCT FROM want.status
        RETURNING s.id, s.student_id, s.status
        """,
        {"today": today},
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Сгорание отработок по сроку
#
# У каждой отработки есть expires_on, но до сих пор его никто не гасил:
# просроченные висели в балансе вечно, и школа продолжала быть должна
# занятия, право на которые истекло полгода назад. Срок без задания,
# которое его применяет, — это не срок, а строчка в договоре.
# ---------------------------------------------------------------------------

# За сколько дней предупредить родителя (spec.md §8: «Отработка сгорает —
# родителю, WhatsApp, за 5 дней»). Пять дней — это ещё выходные плюс будний
# день: успеть договориться о слоте и прийти.
MAKEUP_WARN_DAYS = 5


def expire_makeups(
    cur: psycopg.Cursor, tenant_id: str, today: dt.date
) -> list[dict[str, Any]]:
    """Гасит просроченные отработки школы. Возвращает сгоревшие.

    Идемпотентность держится на самой отработке, а не на отметке «задание
    отработало»: ключ — `expired_at`. UPDATE выбирает только строки, где он
    пустой, и возвращает ровно те, которые изменил, поэтому запись в журнал
    делается на то же множество, что и списание. Второй прогон подряд
    не находит ничего и не пишет ничего; прогон после сбоя безопасен.

    Сгорает отработка на следующий день после `expires_on`, а не в сам этот
    день: «действует до 1 сентября» родитель понимает как «первого ещё можно»,
    и карточка ученика показывает `days_left = 0` как последний день, а не
    как вчерашний. Отсюда строгое `<`.

    Число сгораний ограничено остатком отработок абонемента. В согласованных
    данных ограничение не срабатывает — остаток и есть число непогашенных
    отработок, — но если журнал и таблица разошлись, задание не имеет права
    увести баланс в минус и показать родителю «−1 отработка».
    """
    cur.execute(
        """
        WITH due AS (
          SELECT mc.id,
                 mc.subscription_id,
                 mc.student_id,
                 mc.expires_on,
                 s.makeups_balance,
                 row_number() OVER (
                   PARTITION BY mc.subscription_id ORDER BY mc.expires_on, mc.id
                 ) AS rn
          FROM makeup_credit mc
          JOIN subscription s ON s.id = mc.subscription_id
          WHERE mc.used_at IS NULL
            AND mc.expired_at IS NULL
            AND mc.expires_on < %(today)s
        )
        UPDATE makeup_credit mc
        SET expired_at = now()
        FROM due
        WHERE mc.id = due.id AND due.rn <= due.makeups_balance
        RETURNING mc.id, mc.subscription_id, mc.student_id, due.expires_on
        """,
        {"today": today},
    )
    burned = cur.fetchall()

    for row in burned:
        # Строка журнала обязательна: остаток отработок ведёт триггер от
        # журнала, и погасить кредит, не записав движение, значило бы
        # оставить баланс прежним, а отработку — исчезнувшей.
        #
        # lesson_id намеренно пустой, хотя занятие известно (granted_for):
        # с ним журнал датировал бы сгорание днём пропущенного урока —
        # месяцем раньше того, когда отработка действительно сгорела.
        journal.add_entry(
            cur,
            tenant_id,
            str(row["subscription_id"]),
            kind="makeup_expire",
            lessons_delta=0,
            makeups_delta=-1,
            reason=makeup_expire_reason(row["expires_on"]),
            # Автора нет: сгорание — следствие календаря, а не чьего-то
            # решения. Приписать его администратору, запустившему задание,
            # значило бы соврать в единственном месте, где ищут виноватого.
            actor_id=None,
        )
        journal.audit(
            cur,
            tenant_id,
            None,
            "makeup.expire",
            "makeup_credit",
            str(row["id"]),
            {
                "subscription_id": str(row["subscription_id"]),
                "student_id": str(row["student_id"]),
                "expires_on": row["expires_on"].isoformat(),
            },
        )
    return burned


def warn_expiring_makeups(
    cur: psycopg.Cursor, tenant_id: str, today: dt.date
) -> list[dict[str, Any]]:
    """Ставит в очередь предупреждение родителю о скором сгорании отработки.

    Окно, а не ровно пятый день: задание, пропустившее сутки из-за сбоя,
    иначе молча потеряло бы всех, у кого срок пришёлся на этот день.
    От повторов бережёт не узость окна, а `dedup_key` — уникальный индекс
    `notification_dedup` не даст вставить второе сообщение о той же
    отработке, сколько бы раз задание ни отработало.

    Адрес — телефон плательщика семьи, а не ученика: сообщение получает тот,
    кто принимает решение и водит ребёнка, а у ребёнка своего телефона
    обычно и нет. Без телефона строка пропускается: `notification`
    без адреса — это сообщение, которое никогда не уйдёт.
    """
    cur.execute(
        """
        INSERT INTO notification
            (tenant_id, person_id, channel, template, payload, to_address, dedup_key)
        SELECT %(tenant)s,
               coalesce(payer.id, sp.id),
               'whatsapp',
               'makeup_expiring',
               jsonb_build_object(
                 'makeup_id',    mc.id,
                 'student_id',   mc.student_id,
                 'student_name', btrim(concat_ws(' ', sp.first_name, sp.last_name)),
                 'expires_on',   mc.expires_on,
                 'days_left',    mc.expires_on - %(today)s::date
               ),
               coalesce(payer.phone, sp.phone),
               'makeup_expiring:' || mc.id
        FROM makeup_credit mc
        JOIN student st ON st.id = mc.student_id
        JOIN person  sp ON sp.id = st.person_id
        LEFT JOIN family f     ON f.id = st.family_id
        LEFT JOIN person payer ON payer.id = f.payer_id
        WHERE mc.used_at IS NULL
          AND mc.expired_at IS NULL
          AND st.archived_at IS NULL
          AND mc.expires_on >= %(today)s::date
          AND mc.expires_on <= %(today)s::date + %(lead)s
          AND coalesce(payer.phone, sp.phone) IS NOT NULL
        ON CONFLICT (tenant_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        RETURNING id
        """,
        {"tenant": tenant_id, "today": today, "lead": MAKEUP_WARN_DAYS},
    )
    return cur.fetchall()


def _cancel_lessons(
    cur: psycopg.Cursor, student_id: str, day_from: dt.date, day_to: dt.date, tz: ZoneInfo
) -> list[str]:
    """Отменяет запланированные занятия внутри заморозки — без списания.

    Отдельной строки журнала на каждое отменённое занятие не пишем: занятия
    не сгорели, они просто не состоятся, а весь период уже описан одной
    записью `freeze`. Строка на урок означала бы движение, которого не было.

    Границы дня берутся в поясе филиала: в UTC 14 августа для Алматы
    начинается 13-го в 19:00, и занятие вечера 13-го отменилось бы зря.
    """
    start = dt.datetime.combine(day_from, dt.time(0, 0), tzinfo=tz)
    end = dt.datetime.combine(day_to, dt.time(0, 0), tzinfo=tz)
    cur.execute(
        """
        UPDATE lesson
        SET status = 'cancelled', updated_at = now()
        WHERE student_id = %s
          AND status = 'planned'
          AND starts_at >= %s
          AND starts_at <  %s
        RETURNING id
        """,
        (student_id, start, end),
    )
    return [str(row["id"]) for row in cur.fetchall()]


def release_hold(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    subscription_id: str,
    hold_id: str,
    effective_date: dt.date | None = None,
) -> dict[str, Any]:
    """Снятие заморозки: срок возвращается назад, занятия — нет.

    Отменённые занятия не восстанавливаются: их слоты за это время могли
    занять другие ученики, и «восстановление» привело бы к двойной брони
    кабинета. Честнее сказать администратору, что их нужно поставить заново.
    """
    sub = repo.subscription_context(cur, subscription_id)
    if sub is None:
        raise not_found("Абонемент не найден")

    when = opdate.resolve(cur, tenant_id, effective_date, sub["branch_timezone"])

    cur.execute(
        """
        SELECT id, lower(period) AS from_day, upper(period) AS to_day,
               (upper(period) - lower(period)) AS days
        FROM subscription_hold
        WHERE id = %s AND subscription_id = %s
        """,
        (hold_id, subscription_id),
    )
    hold = cur.fetchone()
    if hold is None:
        raise not_found("Заморозка не найдена")

    days = int(hold["days"])
    valid_until_before = sub["valid_until"]

    # Сколько занятий отменила эта заморозка — знает аудит. Пересчитывать
    # по расписанию нельзя: часть отменённых занятий уже могли поставить
    # заново, и цифра получилась бы другой, чем при создании.
    cur.execute(
        """
        SELECT payload FROM audit_log
        WHERE action = 'subscription.hold' AND payload ->> 'hold_id' = %s
        ORDER BY id DESC LIMIT 1
        """,
        (hold_id,),
    )
    audited = cur.fetchone()
    payload = audited["payload"] if audited else {}
    lessons_cancelled = int(payload.get("lessons_cancelled", 0))
    frozen_entry_id = payload.get("entry_id")

    cur.execute(
        "UPDATE subscription SET valid_until = valid_until - %s WHERE id = %s RETURNING valid_until",
        (days, subscription_id),
    )
    valid_until_after = cur.fetchone()["valid_until"]

    # Строку заморозки в журнале не правим и не удаляем — база бы этого
    # и не позволила. Снятие гасится компенсирующей записью со ссылкой
    # на исходную: в истории остаётся и то, что абонемент замораживали,
    # и то, что заморозку сняли.
    entry_id = journal.add_entry(
        cur,
        tenant_id,
        subscription_id,
        kind="freeze",
        lessons_delta=0,
        makeups_delta=0,
        reason=unfreeze_reason(hold["from_day"], hold["to_day"], valid_until_after),
        actor_id=actor_id,
        reverses_id=frozen_entry_id,
        backdated=when.backdated,
    )

    # Сама заморозка из subscription_hold удаляется: это не журнал, а текущее
    # состояние. Пока строка жива, ограничение исключения держит интервал
    # занятым, а годовой лимит считает эти дни израсходованными — то есть
    # снятая заморозка продолжала бы действовать.
    cur.execute("DELETE FROM subscription_hold WHERE id = %s", (hold_id,))

    # Прежний статус нигде не сохранён, и хранить его негде — колонки под него
    # в схеме нет. Он и не нужен: статус выводится из фактов (есть ли заморозка
    # на сегодня, остался ли остаток, не истёк ли срок) теми же ветками, что
    # и в триггере. Восстановленный из снимка статус разошёлся бы с фактами:
    # за время заморозки абонемент мог исчерпаться или истечь.
    #
    # Вызов идёт после journal.add_entry: триггер subscription_recalc бережёт
    # `frozen` и вернул бы его обратно, отработав следом.
    status = _sync_freeze_status(cur, subscription_id, when.on)

    rules = dict(DEFAULT_RULES)
    rules.update(sub["rules"] or {})
    limit = int(rules.get("freeze_days_per_year", 0))
    used = repo.freeze_days_used(cur, str(sub["student_id"]), hold["from_day"].year)

    journal.audit(
        cur,
        tenant_id,
        actor_id,
        "subscription.hold.release",
        "subscription",
        subscription_id,
        {
            "hold_id": hold_id,
            "entry_id": entry_id,
            "reverses_id": frozen_entry_id,
            "days": days,
            "valid_until_before": valid_until_before.isoformat(),
            "valid_until_after": valid_until_after.isoformat(),
            "lessons_cancelled": lessons_cancelled,
            "effective_date": when.on.isoformat(),
            "backdated": when.backdated,
        },
    )

    message = (
        f"Заморозка снята, срок вернулся на {valid_until_after:%d.%m.%Y}."
    )
    if lessons_cancelled:
        message += (
            f" Отменённых занятий — {lessons_cancelled}; они не восстановлены, "
            "поставьте их заново в расписании."
        )

    return {
        "hold_id": hold_id,
        "days_returned": days,
        "valid_until_before": valid_until_before.isoformat(),
        "valid_until_after": valid_until_after.isoformat(),
        "lessons_cancelled": lessons_cancelled,
        "lessons_restored": 0,
        "freeze_days_left": max(limit - used, 0),
        "status": status,
        "effective_date": when.on.isoformat(),
        "backdated": when.backdated,
        "message": message,
    }
