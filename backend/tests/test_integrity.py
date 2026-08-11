"""Проверки целостности на сгенерированной истории.

Запускаются после scripts/simulate_history.py и проверяют не то, что код
что-то записал, а то, что записанное сходится само с собой спустя полгода
и тысячи операций:

    python -m scripts.simulate_history --students 165 --months 6 --seed 42 --reset
    python -m pytest tests/test_integrity.py -q -s

Почему это отдельный файл, а не расширение существующих тестов: остальные
тесты пересевают демо-данные перед каждым тестом и проверяют одну операцию
в изоляции. Здесь наоборот — данные накоплены заранее, ни один тест их
не меняет, и вопрос стоит один: сошлось или нет.

Проверки идут двумя разными глазами. Суммы и журналы читаются под
администратором базы (видно всё, включая то, что приложение обязано было
скрыть), а изоляция — под ролью приложения, потому что именно на неё
действуют политики RLS.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from app import config, rules
from scripts import simulate_history as sim

TENANT_A = sim.SIM_TENANT_A
TENANT_B = sim.SIM_TENANT_B
ADMIN_A = sim.SIM_ADMIN_A

ADMIN_URL = config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME


# ---------------------------------------------------------------------------
# Обвязка
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_data() -> None:
    """Перекрывает автопересев из conftest.py.

    Демо-посев здесь был бы не просто лишним, а разрушительным: он идёт перед
    каждым тестом и стоит секунды, а проверять нужно накопленную историю,
    которой пересев не касается. Собственные тенанты симуляции seed_demo
    не трогает, но и делать его работу двести раз подряд незачем.
    """
    yield


@pytest.fixture(scope="module")
def sql():
    """Администратор базы: видит всё, включая чужие тенанты.

    Именно это и нужно проверкам сходимости — им обязано быть видно то,
    что приложение прячет, иначе расхождение спряталось бы вместе с данными.
    """
    with psycopg.connect(ADMIN_URL, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(scope="module", autouse=True)
def require_simulation(sql) -> None:
    cur = sql.cursor()
    cur.execute("SELECT count(*) AS n FROM tenant WHERE id IN (%s, %s)", (TENANT_A, TENANT_B))
    if int(cur.fetchone()["n"]) != 2:
        pytest.skip("нет данных симуляции: сначала python -m scripts.simulate_history --reset")


def rows(conn, query: str, params: Any = None) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(query, params or {})
    return cur.fetchall()


def one(conn, query: str, params: Any = None) -> dict[str, Any]:
    result = rows(conn, query, params)
    assert result, f"запрос ничего не вернул: {query}"
    return result[0]


def app_rows(tenant_id: str, query: str, params: Any = None) -> list[dict[str, Any]]:
    """Чтение под ролью приложения с выставленным тенантом.

    Отдельное соединение, а не пул приложения: тест обязан ходить тем же
    путём, что и живой запрос (роль без BYPASSRLS, app.tenant_id в транзакции),
    но не зависеть от того, поднят ли пул.
    """
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute(query, params or {})
        return cur.fetchall()


BOTH = pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B], ids=["школа-1", "школа-2"])


# ---------------------------------------------------------------------------
# 1. Сходимость журнала абонемента
# ---------------------------------------------------------------------------


@BOTH
def test_ledger_matches_cached_balance(sql, tenant: str) -> None:
    """Сумма движений равна кэшу остатка — у каждого абонемента без исключения.

    Это главная проверка всей задачи. Кэш ведёт триггер базы, журнал пишет
    приложение; расхождение хотя бы в одном абонементе означает, что остаток
    получил второй источник правды, и дальше спорить с родителем не о чем.
    """
    diverged = rows(
        sql,
        """
        SELECT s.id,
               s.lessons_balance, coalesce(t.lessons, 0) AS lessons_sum,
               s.makeups_balance, coalesce(t.makeups, 0) AS makeups_sum
          FROM subscription s
          LEFT JOIN (
                SELECT subscription_id,
                       sum(lessons_delta) AS lessons,
                       sum(makeups_delta) AS makeups
                  FROM subscription_entry GROUP BY 1
          ) t ON t.subscription_id = s.id
         WHERE s.tenant_id = %(t)s
           AND (s.lessons_balance <> coalesce(t.lessons, 0)
             OR s.makeups_balance <> coalesce(t.makeups, 0))
        """,
        {"t": tenant},
    )
    assert diverged == [], f"кэш разошёлся с журналом у {len(diverged)} абонементов"


@BOTH
def test_balance_never_negative(sql, tenant: str) -> None:
    """Остаток не уходит в минус — ни сейчас, ни в любой момент истории.

    Текущего кэша мало: абонемент мог побывать в минусе и вернуться обратно
    компенсирующей записью. Считаем нарастающий итог по журналу в порядке
    записей — так, как его прочитал бы человек сверху вниз.
    """
    current = rows(
        sql,
        "SELECT id FROM subscription WHERE tenant_id = %(t)s "
        "AND (lessons_balance < 0 OR makeups_balance < 0)",
        {"t": tenant},
    )
    assert current == [], "у абонемента отрицательный остаток в кэше"

    dips = rows(
        sql,
        """
        SELECT subscription_id, min(running_lessons) AS min_lessons,
               min(running_makeups) AS min_makeups
          FROM (
            SELECT subscription_id,
                   sum(lessons_delta) OVER w AS running_lessons,
                   sum(makeups_delta) OVER w AS running_makeups
              FROM subscription_entry
             WHERE tenant_id = %(t)s
            WINDOW w AS (PARTITION BY subscription_id ORDER BY id
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
          ) x
         GROUP BY 1
        HAVING min(running_lessons) < 0 OR min(running_makeups) < 0
        """,
        {"t": tenant},
    )
    assert dips == [], f"нарастающий итог журнала уходил в минус у {len(dips)} абонементов"


@BOTH
def test_makeup_credits_match_balance(sql, tenant: str) -> None:
    """Открытых отработок ровно столько, сколько показывает баланс.

    Отработка — отдельная валюта с двумя представлениями: строкой
    в makeup_credit (у неё свой срок) и дельтой в журнале. Разъезд между ними
    означает либо отработку, которой нельзя воспользоваться, либо запись
    на занятие, за которое никто не платил.
    """
    diverged = rows(
        sql,
        """
        SELECT s.id, s.makeups_balance, count(mc.id) AS open_credits
          FROM subscription s
          LEFT JOIN makeup_credit mc
                 ON mc.subscription_id = s.id
                AND mc.used_at IS NULL AND mc.expired_at IS NULL
         WHERE s.tenant_id = %(t)s
         GROUP BY s.id, s.makeups_balance
        HAVING s.makeups_balance <> count(mc.id)
        """,
        {"t": tenant},
    )
    assert diverged == [], (
        f"баланс отработок не сходится со списком отработок у {len(diverged)} абонементов"
    )


# ---------------------------------------------------------------------------
# 1b. Непустота журнала
#
# Всё, что выше, — проверки СХОДИМОСТИ, и у них есть общая слепая зона:
# ноль сходится с нулём. Пустой журнал проходит их все, потому что сумма
# нуля движений равна нулевому остатку, и это не теоретическое замечание:
# ровно так 2 500 отметок из 2 900 не оставили следа в журнале, а проверки
# при этом были зелёными (issue #15, ADR-001).
#
# Поэтому здесь спрашивается не «сходится ли», а «есть ли вообще».
# Проверки построены так, что пустота их гарантированно роняет: они сравнивают
# журнал не с ним самим, а с фактами из соседних таблиц — с продажами
# и с отметками посещаемости.
# ---------------------------------------------------------------------------


# Занятие по этим отметкам состоялось, и правила спорят только о прогулах:
# «пришёл» и «опоздал» списывают занятие при любых настройках школы
# (rules._subscription_deltas). Поэтому именно на них строится проверка —
# ей не нужно знать правила конкретного абонемента.
BURNING_MARKS = ("came", "late")

# Абонемент считается «тем самым» для занятия, если совпало направление
# и дата урока попала в срок действия. Пояс — филиала, а не сервера:
# урок в 20:00 в Алматы для UTC приходится на предыдущие сутки, и абонемент,
# начавшийся в этот день, отсеялся бы зря.
_COVERING_SUBSCRIPTION = """
  EXISTS (
    SELECT 1
      FROM subscription s
      JOIN subscription_plan pl ON pl.id = s.plan_id
     WHERE s.student_id = a.student_id
       AND s.status <> 'cancelled'
       AND l.discipline_id IS NOT DISTINCT FROM pl.discipline_id
       AND (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date
           BETWEEN s.valid_from AND s.valid_until
  )
"""


@BOTH
def test_every_subscription_ledger_starts_with_a_purchase(sql, tenant: str) -> None:
    """Журнал абонемента не бывает пустым: он начинается с продажи.

    Остаток — сумма журнала, а не хранимое число. Абонемент без записи
    `purchase` или `transfer_in` означает остаток, взявшийся ниоткуда:
    либо продажа не дописала журнал, либо кэш кто-то правил руками.
    Проверка сходимости такой абонемент пропустит — ноль сойдётся с нулём.
    """
    empty = rows(
        sql,
        """
        SELECT s.id, s.status, s.lessons_total, s.lessons_balance, s.valid_from
          FROM subscription s
         WHERE s.tenant_id = %(t)s
           AND NOT EXISTS (
             SELECT 1 FROM subscription_entry e
              WHERE e.subscription_id = s.id
                AND e.kind IN ('purchase', 'transfer_in')
           )
        """,
        {"t": tenant},
    )
    assert empty == [], (
        f"у {len(empty)} абонементов журнал не начинается с продажи: "
        + ", ".join(f"{r['id']} ({r['status']}, выдано {r['lessons_total']})" for r in empty[:10])
    )


@BOTH
def test_marks_on_a_live_subscription_left_a_charge(sql, tenant: str) -> None:
    """Отметка «пришёл» при живом абонементе обязана оставить списание.

    Это та самая проверка, которой не хватало. Отметка есть, абонемент,
    покрывающий дату и направление, есть, а движения по журналу нет —
    значит, занятие проведено бесплатно и никто об этом не знает: остаток
    родителю назовут прежний, а занятие он уже отходил.

    Считается не выборкой, а долей от всех покрытых отметок: при поломке,
    из-за которой `active_subscription()` перестаёт отдавать абонемент,
    молчаливыми становятся не одна-две отметки, а тысячи сразу.
    """
    totals = one(
        sql,
        f"""
        SELECT count(*)::int AS covered,
               count(*) FILTER (WHERE NOT EXISTS (
                 SELECT 1 FROM subscription_entry e
                  WHERE e.attendance_id = a.id AND e.kind = 'charge'
               ))::int AS silent
          FROM attendance a
          JOIN lesson l ON l.id = a.lesson_id
          JOIN tenant t ON t.id = a.tenant_id
          LEFT JOIN branch b ON b.id = l.branch_id
         WHERE a.tenant_id = %(t)s
           AND a.revoked_at IS NULL
           AND a.mark = ANY(%(marks)s)
           AND {_COVERING_SUBSCRIPTION}
        """,
        {"t": tenant, "marks": list(BURNING_MARKS)},
    )
    covered, silent = int(totals["covered"]), int(totals["silent"])
    # Без нижней границы проверка вырождается вместе с данными: ноль отметок
    # даёт ноль молчаливых и зелёный результат на пустой базе.
    assert covered > 0, "нет ни одной отметки при живом абонементе — проверять нечего"
    assert silent == 0, (
        f"{silent} из {covered} отметок при живом абонементе не оставили списания "
        "в журнале: занятия проведены, а с абонемента не списано ничего"
    )


@BOTH
def test_used_subscription_has_a_non_empty_ledger(sql, tenant: str) -> None:
    """У абонемента с проведёнными занятиями журнал не пуст (ADR-001).

    Предыдущая проверка смотрит со стороны отметки, эта — со стороны
    абонемента, и это разные дыры. Отметок могло не быть вовсе, а абонемент
    всё равно обязан объяснить, куда делись занятия: если в его срок и по его
    направлению прошли уроки, в журнале есть списания.
    """
    silent = rows(
        sql,
        """
        WITH used AS (
          SELECT s.id, s.status, s.valid_from, s.valid_until, count(*) AS marks
            FROM subscription s
            JOIN subscription_plan pl ON pl.id = s.plan_id
            JOIN tenant t ON t.id = s.tenant_id
            JOIN attendance a ON a.student_id = s.student_id AND a.revoked_at IS NULL
            JOIN lesson l ON l.id = a.lesson_id
            LEFT JOIN branch b ON b.id = l.branch_id
           WHERE s.tenant_id = %(t)s
             AND s.status <> 'cancelled'
             AND a.mark = ANY(%(marks)s)
             AND l.discipline_id IS NOT DISTINCT FROM pl.discipline_id
             AND (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date
                 BETWEEN s.valid_from AND s.valid_until
           GROUP BY s.id
        )
        SELECT u.id, u.status, u.marks
          FROM used u
         WHERE NOT EXISTS (
           SELECT 1 FROM subscription_entry e
            WHERE e.subscription_id = u.id AND e.kind = 'charge'
         )
        """,
        {"t": tenant, "marks": list(BURNING_MARKS)},
    )
    assert silent == [], (
        f"у {len(silent)} абонементов прошли занятия, а журнал пуст: "
        + ", ".join(f"{r['id']} ({r['marks']} отметок)" for r in silent[:10])
    )


# ---------------------------------------------------------------------------
# 2. Отметка и её последствия
# ---------------------------------------------------------------------------


def _subscription_states(conn, tenant: str) -> dict[str, rules.SubscriptionState]:
    result = {}
    for row in rows(
        conn,
        """SELECT id, lessons_total, lessons_balance, makeups_balance,
                  valid_from, valid_until, status, rules, price
             FROM subscription WHERE tenant_id = %(t)s""",
        {"t": tenant},
    ):
        result[str(row["id"])] = rules.SubscriptionState(
            id=str(row["id"]),
            lessons_total=int(row["lessons_total"]),
            lessons_balance=int(row["lessons_balance"]),
            makeups_balance=int(row["makeups_balance"]),
            valid_until=row["valid_until"],
            status=row["status"],
            rules=row["rules"] or {},
            price=row["price"] or Decimal(0),
        )
    return result


def _attendance_effects(conn, tenant: str) -> tuple[dict, dict, dict]:
    marks = {
        str(row["id"]): row
        for row in rows(
            conn,
            """SELECT a.id, a.mark, a.revoked_at, a.lesson_id, a.student_id,
                      l.starts_at, l.kind AS lesson_kind, l.group_id, l.teacher_id
                 FROM attendance a JOIN lesson l ON l.id = a.lesson_id
                WHERE a.tenant_id = %(t)s""",
            {"t": tenant},
        )
    }
    entries: dict[str, list[dict]] = {}
    for row in rows(
        conn,
        """SELECT id, attendance_id, subscription_id, kind, lessons_delta, makeups_delta
             FROM subscription_entry
            WHERE tenant_id = %(t)s AND attendance_id IS NOT NULL""",
        {"t": tenant},
    ):
        entries.setdefault(str(row["attendance_id"]), []).append(row)

    payroll: dict[str, list[dict]] = {}
    for row in rows(
        conn,
        """SELECT id, attendance_id, kind, amount
             FROM payroll_entry
            WHERE tenant_id = %(t)s AND attendance_id IS NOT NULL""",
        {"t": tenant},
    ):
        payroll.setdefault(str(row["attendance_id"]), []).append(row)
    return marks, entries, payroll


@BOTH
def test_each_mark_produced_expected_entries(sql, tenant: str) -> None:
    """Одна отметка — одна запись в журнале и одно начисление. Или ноль.

    Ноль — законный ответ ровно в двух случаях: занятие шло без абонемента
    (разовая оплата) и правило абонемента не даёт движения. Всё остальное
    означает либо потерянное списание, либо двойное.

    Дельты не берутся на веру: они пересчитываются rules.compute_effect
    по правилам, зафиксированным в самом абонементе, — той же функцией,
    что рисует предпросмотр администратору.
    """
    marks, entries, payroll = _attendance_effects(sql, tenant)
    states = _subscription_states(sql, tenant)

    problems: list[str] = []
    checked = 0
    for attendance_id, mark in marks.items():
        own = entries.get(attendance_id, [])
        charges = [e for e in own if e["kind"] == "charge"]
        grants = [e for e in own if e["kind"] == "makeup_grant"]
        if len(charges) > 1 or len(grants) > 1:
            problems.append(f"{attendance_id}: {len(charges)} charge, {len(grants)} makeup_grant")
            continue
        if not own:
            # Отметка без движения. Проверяем, что так и было задумано:
            # либо абонемента не было вовсе, либо правило дало нули.
            continue

        state = states[str(own[0]["subscription_id"])]
        expected = rules.compute_effect(mark["mark"], state)
        got_lessons = sum(int(e["lessons_delta"]) for e in own)
        got_makeups = sum(int(e["makeups_delta"]) for e in own)
        if mark["revoked_at"] is None:
            if (got_lessons, got_makeups) != (expected.lessons_delta, expected.makeups_delta):
                problems.append(
                    f"{attendance_id} «{mark['mark']}»: журнал {got_lessons}/{got_makeups}, "
                    f"правила требуют {expected.lessons_delta}/{expected.makeups_delta}"
                )
            checked += 1

        lesson_pay = [p for p in payroll.get(attendance_id, []) if p["kind"] == "lesson"]
        if len(lesson_pay) > 1:
            problems.append(f"{attendance_id}: {len(lesson_pay)} начислений на одну отметку")

    assert checked > 100, f"проверять нечего: сошлось всего {checked} отметок"
    assert problems == [], "\n".join(problems[:20])


@BOTH
def test_mark_without_entries_had_no_subscription(sql, tenant: str) -> None:
    """Отметка без движения по журналу бывает только без абонемента.

    Занятие вне срока действия абонемента идёт разовой оплатой — это
    нормальная ситуация. Ненормальная — когда абонемент был, правило требовало
    списания, а в журнале пусто: занятие проведено бесплатно и никто
    об этом не знает.
    """
    silent = rows(
        sql,
        """
        SELECT a.id, a.mark, l.starts_at::date AS on_day, a.student_id
          FROM attendance a
          JOIN lesson l ON l.id = a.lesson_id
         WHERE a.tenant_id = %(t)s
           AND NOT EXISTS (SELECT 1 FROM subscription_entry e WHERE e.attendance_id = a.id)
        """,
        {"t": tenant},
    )
    states = _subscription_states(sql, tenant)
    unexplained = []
    for row in silent:
        covering = rows(
            sql,
            """SELECT id FROM subscription
                WHERE student_id = %(s)s AND valid_from <= %(d)s AND valid_until >= %(d)s
                ORDER BY created_at LIMIT 1""",
            {"s": row["student_id"], "d": row["on_day"]},
        )
        if not covering:
            continue
        state = states.get(str(covering[0]["id"]))
        if state is None:
            continue
        effect = rules.compute_effect(row["mark"], state)
        if effect.lessons_delta or effect.makeups_delta:
            unexplained.append(f"{row['id']} «{row['mark']}» {row['on_day']}")
    assert unexplained == [], (
        f"{len(unexplained)} отметок не оставили следа в журнале при живом абонементе: "
        + ", ".join(unexplained[:10])
    )


@BOTH
def test_revoked_marks_are_zeroed_out(sql, tenant: str) -> None:
    """Отменённая отметка гасится в ноль — и в абонементе, и в зарплате.

    Компенсирующая запись, а не удаление: исходная строка обязана остаться
    в журнале. Значит, сумма исходных и компенсирующих движений по такой
    отметке равна нулю, а начисление преподавателю — нулю вместе с ними.
    """
    revoked = rows(
        sql,
        "SELECT id, mark FROM attendance WHERE tenant_id = %(t)s AND revoked_at IS NOT NULL",
        {"t": tenant},
    )
    assert revoked, "в истории нет ни одной отменённой отметки — проверять нечего"

    problems = []
    for row in revoked:
        totals = one(
            sql,
            """
            SELECT coalesce(sum(lessons_delta), 0) AS lessons,
                   coalesce(sum(makeups_delta), 0) AS makeups,
                   count(*) AS n
              FROM subscription_entry
             WHERE attendance_id = %(a)s
                OR reverses_id IN (SELECT id FROM subscription_entry WHERE attendance_id = %(a)s)
            """,
            {"a": row["id"]},
        )
        if int(totals["lessons"]) or int(totals["makeups"]):
            problems.append(
                f"{row['id']} «{row['mark']}»: осталось {totals['lessons']}/{totals['makeups']}"
            )
        pay = one(
            sql,
            """
            SELECT coalesce(sum(amount), 0) AS total
              FROM payroll_entry
             WHERE attendance_id = %(a)s
                OR reverses_id IN (SELECT id FROM payroll_entry WHERE attendance_id = %(a)s)
            """,
            {"a": row["id"]},
        )
        if int(pay["total"]):
            problems.append(f"{row['id']}: начисление не погашено, осталось {pay['total']} ₸")

    assert problems == [], "\n".join(problems[:20])


@BOTH
def test_reversing_entries_point_at_something(sql, tenant: str) -> None:
    """Компенсирующая запись всегда ссылается на ту, которую гасит.

    Журнал обязан объяснять сам себя: запись «+1 занятие» без указания,
    что именно она отменяет, читается как подарок от школы.
    """
    orphans = rows(
        sql,
        """SELECT e.id, e.kind FROM subscription_entry e
            WHERE e.tenant_id = %(t)s AND e.kind = 'refund' AND e.reverses_id IS NULL""",
        {"t": tenant},
    )
    assert orphans == [], f"{len(orphans)} возвратов не ссылаются на исходную запись"


@BOTH
def test_charges_belong_to_covering_subscription(sql, tenant: str) -> None:
    """Занятие списывается с абонемента, который действовал в этот день.

    Списание с абонемента, чей срок не покрывает дату урока, — это чужие
    занятия на чужом договоре: остаток сойдётся, а объяснить его будет нечем.
    """
    wrong = rows(
        sql,
        """
        SELECT e.id, e.kind, l.starts_at::date AS on_day, s.valid_from, s.valid_until
          FROM subscription_entry e
          JOIN lesson l ON l.id = e.lesson_id
          JOIN subscription s ON s.id = e.subscription_id
         WHERE e.tenant_id = %(t)s
           AND e.kind IN ('charge', 'makeup_grant')
           AND (l.starts_at AT TIME ZONE 'Asia/Almaty')::date
               NOT BETWEEN s.valid_from AND s.valid_until
        """,
        {"t": tenant},
    )
    assert wrong == [], f"{len(wrong)} списаний ушло на абонемент, не покрывающий дату занятия"


# ---------------------------------------------------------------------------
# 3. Расписание
# ---------------------------------------------------------------------------


@BOTH
def test_no_unacknowledged_double_booking(sql, tenant: str) -> None:
    """Кабинет и преподаватель не заняты дважды без подтверждённого овербукинга.

    Запрет держит база (ограничения исключения), но проверять его надо
    именно на накопленных данных: ограничение отключается флагом
    overbook_ack, и достаточно одной строки, где флаг проставили не там.
    """
    clashes = rows(
        sql,
        """
        SELECT a.id AS a_id, b.id AS b_id,
               a.room_id = b.room_id AS same_room,
               a.teacher_id = b.teacher_id AS same_teacher,
               a.starts_at
          FROM lesson a JOIN lesson b
            ON a.id < b.id
           AND a.status <> 'cancelled' AND b.status <> 'cancelled'
           AND tstzrange(a.starts_at, a.ends_at, '[)')
            && tstzrange(b.starts_at, b.ends_at, '[)')
           AND (a.room_id = b.room_id OR a.teacher_id = b.teacher_id)
         WHERE a.tenant_id = %(t)s AND b.tenant_id = %(t)s
           AND NOT a.overbook_ack AND NOT b.overbook_ack
        """,
        {"t": tenant},
    )
    assert clashes == [], f"{len(clashes)} пар занятий пересекаются без подтверждения"


def test_acknowledged_overbooking_exists(sql) -> None:
    """У проверки выше есть что проверять.

    Без единого подтверждённого овербукинга предыдущий тест доказывал бы,
    что пустое множество пусто.
    """
    total = one(
        sql,
        "SELECT count(*) AS n FROM lesson WHERE tenant_id = %(t)s AND overbook_ack",
        {"t": TENANT_A},
    )
    assert int(total["n"]) > 0


@BOTH
def test_holds_of_one_subscription_do_not_overlap(sql, tenant: str) -> None:
    """Заморозки одного абонемента не пересекаются.

    Пересечение продлило бы срок дважды за одни и те же сутки — и лимит дней
    в году перестал бы что-либо ограничивать.
    """
    overlaps = rows(
        sql,
        """
        SELECT a.id AS a_id, b.id AS b_id, a.subscription_id
          FROM subscription_hold a JOIN subscription_hold b
            ON a.id < b.id AND a.subscription_id = b.subscription_id
           AND a.period && b.period
         WHERE a.tenant_id = %(t)s
        """,
        {"t": tenant},
    )
    assert overlaps == [], f"{len(overlaps)} пересекающихся заморозок"


def test_freeze_left_a_trace_in_the_ledger(sql) -> None:
    """Каждая живая заморозка видна в журнале абонемента.

    Родителю, который спрашивает «почему абонемент кончается позже»,
    отвечают журналом: аудит он не видит.
    """
    holds = one(
        sql,
        "SELECT count(*) AS n FROM subscription_hold WHERE tenant_id = %(t)s",
        {"t": TENANT_A},
    )
    entries = one(
        sql,
        "SELECT count(*) AS n FROM subscription_entry "
        "WHERE tenant_id = %(t)s AND kind = 'freeze' AND reverses_id IS NULL",
        {"t": TENANT_A},
    )
    assert int(holds["n"]) > 0, "в истории нет заморозок"
    assert int(entries["n"]) >= int(holds["n"]), (
        f"заморозок {holds['n']}, а записей в журнале {entries['n']}"
    )


# ---------------------------------------------------------------------------
# 4. Зарплата
# ---------------------------------------------------------------------------


@BOTH
def test_closed_payroll_periods_add_up(sql, tenant: str) -> None:
    """Сумма закрытого периода равна сумме начислений внутри него.

    Проверяются три вещи сразу: ни одно начисление внутри закрытого периода
    не осталось без периода (иначе ведомость недоплатила), ни одно чужое
    не приписано (переплатила), и суммы совпадают до тенге.
    """
    periods = rows(
        sql,
        """SELECT id, lower(period) AS from_day, upper(period) AS to_day
             FROM payroll_period
            WHERE tenant_id = %(t)s AND closed_at IS NOT NULL
            ORDER BY 2""",
        {"t": tenant},
    )
    assert periods, "ни одного закрытого зарплатного периода"

    for period in periods:
        assigned = one(
            sql,
            """SELECT count(*) AS n, coalesce(sum(amount), 0) AS total
                 FROM payroll_entry WHERE period_id = %(p)s""",
            {"p": period["id"]},
        )
        inside = one(
            sql,
            """SELECT count(*) AS n, coalesce(sum(amount), 0) AS total
                 FROM payroll_entry
                WHERE tenant_id = %(t)s
                  AND (created_at AT TIME ZONE 'Asia/Almaty')::date >= %(a)s
                  AND (created_at AT TIME ZONE 'Asia/Almaty')::date <  %(b)s""",
            {"t": tenant, "a": period["from_day"], "b": period["to_day"]},
        )
        assert int(assigned["n"]) == int(inside["n"]), (
            f"период {period['from_day']}–{period['to_day']}: "
            f"в периоде {assigned['n']} начислений, по датам {inside['n']}"
        )
        assert int(assigned["total"]) == int(inside["total"]), (
            f"период {period['from_day']}–{period['to_day']}: "
            f"{assigned['total']} ₸ против {inside['total']} ₸ по датам"
        )


@BOTH
def test_payroll_entry_stays_in_its_period(sql, tenant: str) -> None:
    """Начисление не может лежать в чужом периоде."""
    strays = rows(
        sql,
        """
        SELECT e.id, e.created_at, p.period
          FROM payroll_entry e JOIN payroll_period p ON p.id = e.period_id
         WHERE e.tenant_id = %(t)s
           AND NOT p.period @> (e.created_at AT TIME ZONE 'Asia/Almaty')::date
        """,
        {"t": tenant},
    )
    assert strays == [], f"{len(strays)} начислений лежат в периоде, которому не принадлежат"


@BOTH
def test_payroll_matches_marks(sql, tenant: str) -> None:
    """Начисление за занятие есть ровно у тех отметок, которым оно положено.

    Ставка фиксированная, поэтому сумму можно пересчитать: доля от ставки
    зависит только от отметки и правил абонемента.
    """
    checked = 0
    problems = []
    for row in rows(
        sql,
        """
        SELECT a.id, a.mark, a.revoked_at, l.kind AS lesson_kind, l.group_id,
               l.discipline_id, l.teacher_id, l.starts_at::date AS on_day,
               p.amount, p.calc
          FROM attendance a
          JOIN lesson l ON l.id = a.lesson_id
          LEFT JOIN payroll_entry p ON p.attendance_id = a.id AND p.kind = 'lesson'
         WHERE a.tenant_id = %(t)s AND a.revoked_at IS NULL
         LIMIT 4000
        """,
        {"t": tenant},
    ):
        rate = rows(
            sql,
            """SELECT amount FROM teacher_rate
                WHERE staff_id = %(s)s AND format = 'individual'
                  AND valid_from <= %(d)s LIMIT 1""",
            {"s": row["teacher_id"], "d": row["on_day"]},
        )
        if not rate or row["lesson_kind"] == "trial":
            continue
        share = 1.0 if row["mark"] in ("came", "late") else (
            1.0 if row["mark"] in ("no_show", "cancelled_late") else 0.0
        )
        expected = int(int(rate[0]["amount"]) * share)
        got = int(row["amount"]) if row["amount"] is not None else 0
        if expected != got:
            problems.append(f"{row['id']} «{row['mark']}»: {got} ₸ вместо {expected} ₸")
        checked += 1
    assert checked > 100
    assert problems == [], "\n".join(problems[:20])


# ---------------------------------------------------------------------------
# 5. Изоляция школ
# ---------------------------------------------------------------------------

# Все таблицы с собственным tenant_id и политикой изоляции — из db/005_rls.sql
# и db/010_auth.sql. Список продублирован сознательно: если в схеме появится
# таблица без политики, тест обязан об этом сказать, а не молча перестать
# её проверять.
TENANT_TABLES = [
    "person", "app_user", "branch", "room", "discipline", "staff", "family",
    "student", "study_group", "lesson_series", "lesson", "attendance", "lesson_note",
    "subscription_plan", "subscription", "subscription_hold", "makeup_credit",
    "payment", "teacher_rate", "payroll_period", "payroll_entry",
    "lead", "lead_stage_history", "notification", "task",
    "subscription_entry", "audit_log",
    # Вход: одноразовые коды и журнал попыток. Политика у них возможна и нужна —
    # к моменту работы с кодом школа уже названа (слаг приходит в запросе входа),
    # то есть app.tenant_id выставлен.
    "auth_code", "auth_attempt",
]

# Связующие таблицы без собственного tenant_id: их закрывает подзапрос
# к родителю, который сам под политикой (db/005_rls.sql).
LINK_TABLES = {"family_member", "group_member", "staff_discipline", "staff_branch"}

# Таблицы, у которых tenant_id есть, а политик нет — и это осознанно, а не
# недосмотр. Обе ищутся по хешу секрета ДО того, как тенант станет известен:
# тенант и есть то, что они сообщают. Политика на current_tenant() отсекла бы
# строку раньше, чем тенант выяснен, — и ни приём заявок по ключу
# (db/006_api_keys.sql), ни вход человека (db/010_auth.sql) не работали бы
# вовсе, потому что искать было бы нечего.
#
# Защита у них строится на двух других вещах. Первая: в таблице лежит хеш,
# а не секрет, поэтому даже полное чтение не даёт ни войти, ни слать заявки.
# Вторая: права. У api_key приложению не выданы INSERT и DELETE — выпуск
# и отзыв идут административным каналом; у user_session права полные, потому
# что выдавать и гасить сессии — это и есть работа приложения.
#
# Список закрытый и короткий намеренно: каждая новая строка здесь — это
# осознанное решение с обоснованием, а не «ну эта тоже пусть будет».
UNPOLICED_TABLES = {"api_key", "user_session"}


def test_every_tenant_table_is_covered_by_policy(sql) -> None:
    """Список таблиц теста не отстал от схемы — и схема не отстала от себя.

    Три разных способа проехать молча, и каждый закрыт отдельной проверкой:

    1. таблица с политиками появилась, а в списке теста её нет — тогда
       `test_first_school_sees_nothing_of_the_second` её не смотрит;
    2. таблица с `tenant_id` появилась вообще БЕЗ RLS — самый опасный случай:
       в выборку «таблицы с RLS» она не попадает, и первая проверка о ней
       никогда не узнает;
    3. RLS включён, а политик ноль — выборка отдаёт пусто, приложение видит
       «данных нет» вместо ошибки, и разбираться будут не с тем.
    """
    schema = rows(
        sql,
        """
        SELECT c.relname AS table_name,
               c.relrowsecurity AS rls,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,
               EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                          AND a.attnum > 0 AND NOT a.attisdropped) AS has_tenant_id
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r'
        """,
    )
    known = set(TENANT_TABLES) | LINK_TABLES

    unknown = {row["table_name"] for row in schema if row["rls"]} - known
    assert unknown == set(), f"в схеме появились таблицы с RLS, которых нет в тесте: {unknown}"

    # Таблица с tenant_id и без RLS не показалась бы в проверке выше вовсе:
    # её просто не было бы в выборке. Именно так утечка и заезжает тихо.
    unprotected = {
        row["table_name"]
        for row in schema
        if row["has_tenant_id"] and not row["rls"]
    } - UNPOLICED_TABLES
    assert unprotected == set(), (
        f"таблицы с tenant_id и без изоляции строк: {unprotected}. "
        "Либо добавьте политику в db/, либо — если политика здесь невозможна — "
        "внесите таблицу в UNPOLICED_TABLES вместе с объяснением, почему"
    )

    silent = {row["table_name"] for row in schema if row["rls"] and not row["policies"]}
    assert silent == set(), f"RLS включён, а политик нет — выборка молча пуста: {silent}"


def test_first_school_sees_nothing_of_the_second(sql) -> None:
    """Ни одна выборка первой школы не задевает вторую.

    Проверяется не «мало ли строк утекло», а полное отсутствие: под тенантом
    первой школы каждая таблица обязана отдать ноль строк второй школы —
    и, что не менее важно, отдать хоть что-то своё, иначе тест доказывал бы
    работу RLS на пустой базе.
    """
    leaks, empty = [], []
    for table in TENANT_TABLES:
        seen = app_rows(
            TENANT_A,
            f"SELECT count(*) FILTER (WHERE tenant_id = %(b)s) AS foreign_rows, "
            f"count(*) AS total FROM {table}",
            {"b": TENANT_B},
        )[0]
        if int(seen["foreign_rows"]):
            leaks.append(f"{table}: {seen['foreign_rows']} чужих строк")
        if int(seen["total"]) == 0:
            empty.append(table)
    assert leaks == [], "\n".join(leaks)
    # Таблицы, которые симуляция не наполняет, ожидаемо пусты — на них тест
    # ничего не доказывает, но и не должен падать. Вход симуляция не проходит:
    # она работает через код приложения напрямую, а не через HTTP, поэтому
    # ни кодов, ни попыток входа в её истории нет.
    unexpected_empty = set(empty) - {"study_group", "lesson_series", "task", "lesson_note",
                                     "auth_code", "auth_attempt"}
    assert unexpected_empty == set(), f"под тенантом не видно и своих данных: {unexpected_empty}"


def test_joins_do_not_leak_across_tenants(sql) -> None:
    """Связующие таблицы без tenant_id закрыты подзапросом к родителю."""
    for table, parent, key in [
        ("family_member", "family", "family_id"),
        ("group_member", "study_group", "group_id"),
        ("staff_discipline", "staff", "staff_id"),
        ("staff_branch", "staff", "staff_id"),
    ]:
        visible = app_rows(
            TENANT_A,
            f"SELECT count(*) AS n FROM {table} l "
            f"WHERE NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.id = l.{key})",
        )[0]
        assert int(visible["n"]) == 0, f"{table}: видны строки без своего родителя"


def _session_headers(tenant_id: str, user_id: str) -> dict[str, str]:
    """Настоящая сессия, выданная напрямую в базу.

    Заголовков-заглушек больше нет: школу API узнаёт из `user_session`.
    Служебного входа «для тестов» в приложении не заводилось — здесь просто
    кладётся такая же строка, какую положил бы вход по коду, и приложение
    проверяет её ровно теми же двумя запросами.
    """
    from app import auth

    token = auth.new_session_token()
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            """INSERT INTO user_session (tenant_id, user_id, token_hash, expires_at)
               VALUES (%s, %s, %s, now() + interval '1 hour')""",
            (tenant_id, user_id, auth.hash_token(token)),
        )
        conn.commit()
    return {"Authorization": f"Bearer {token}"}


def test_api_isolates_schools() -> None:
    """То же самое через HTTP: чужие идентификаторы дают 404, а не данные."""
    from fastapi.testclient import TestClient

    from app.main import app

    headers_a = _session_headers(TENANT_A, ADMIN_A)
    with psycopg.connect(ADMIN_URL, row_factory=dict_row) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM student WHERE tenant_id = %s LIMIT 1", (TENANT_B,))
        foreign_student = str(cur.fetchone()["id"])
        cur.execute("SELECT id FROM lesson WHERE tenant_id = %s LIMIT 1", (TENANT_B,))
        foreign_lesson = str(cur.fetchone()["id"])
        cur.execute("SELECT id FROM lead WHERE tenant_id = %s LIMIT 1", (TENANT_B,))
        foreign_lead = str(cur.fetchone()["id"])
        cur.execute(
            "SELECT id FROM subscription WHERE tenant_id = %s LIMIT 1", (TENANT_B,)
        )
        foreign_subscription = str(cur.fetchone()["id"])

    with TestClient(app) as client:
        for path in (
            f"/api/v1/students/{foreign_student}",
            f"/api/v1/lessons/{foreign_lesson}",
            f"/api/v1/leads/{foreign_lead}",
            f"/api/v1/schedule?branch_id={sim.SIM_BRANCH_B1}&date=2026-08-11",
        ):
            response = client.get(path, headers=headers_a)
            assert response.status_code == 404, f"{path} -> {response.status_code}"

        response = client.post(
            f"/api/v1/subscriptions/{foreign_subscription}/holds",
            headers=headers_a,
            json={"from": "2099-01-01", "to": "2099-01-05"},
        )
        assert response.status_code == 404

        # Поиск не должен находить учеников соседней школы даже по точному имени.
        with psycopg.connect(ADMIN_URL, row_factory=dict_row) as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT p.first_name, p.last_name FROM student s
                     JOIN person p ON p.id = s.person_id
                    WHERE s.tenant_id = %s LIMIT 1""",
                (TENANT_B,),
            )
            person = cur.fetchone()
        found = client.get(
            f"/api/v1/students?query={person['first_name']} {person['last_name']}",
            headers=headers_a,
        ).json()
        assert all(item["id"] != foreign_student for item in found)


# ---------------------------------------------------------------------------
# 6. Сводка — цифры, на которые можно сослаться в отчёте
# ---------------------------------------------------------------------------


def test_history_is_actually_large(sql) -> None:
    """История должна быть той величины, ради которой всё затевалось."""
    numbers = one(
        sql,
        """
        SELECT (SELECT count(*) FROM student WHERE tenant_id = %(t)s) AS students,
               (SELECT count(*) FROM lesson WHERE tenant_id = %(t)s) AS lessons,
               (SELECT count(*) FROM attendance WHERE tenant_id = %(t)s) AS marks,
               (SELECT count(*) FROM subscription_entry WHERE tenant_id = %(t)s) AS entries,
               (SELECT max(starts_at)::date - min(starts_at)::date FROM lesson
                 WHERE tenant_id = %(t)s) AS span_days
        """,
        {"t": TENANT_A},
    )
    assert int(numbers["students"]) >= 100, numbers
    assert int(numbers["lessons"]) >= 2500, numbers
    assert int(numbers["span_days"]) >= 150, numbers


def test_print_convergence_summary(sql) -> None:
    """Не проверка, а отчёт: цифры сходимости, на которые можно сослаться.

    Утверждение «всё сошлось» без чисел ничего не стоит — здесь видно,
    на каком объёме оно сошлось.
    """
    for tenant, title in ((TENANT_A, "школа 1"), (TENANT_B, "школа 2")):
        numbers = one(
            sql,
            """
            SELECT (SELECT count(*) FROM student WHERE tenant_id = %(t)s) AS students,
                   (SELECT count(*) FROM student
                     WHERE tenant_id = %(t)s AND archived_at IS NULL) AS active_students,
                   (SELECT count(*) FROM lesson WHERE tenant_id = %(t)s) AS lessons,
                   (SELECT count(*) FROM lesson
                     WHERE tenant_id = %(t)s AND status = 'held') AS held,
                   (SELECT count(*) FROM attendance WHERE tenant_id = %(t)s) AS marks,
                   (SELECT count(*) FROM attendance
                     WHERE tenant_id = %(t)s AND revoked_at IS NOT NULL) AS revoked,
                   (SELECT count(*) FROM subscription WHERE tenant_id = %(t)s) AS subs,
                   (SELECT count(*) FROM subscription_entry WHERE tenant_id = %(t)s) AS entries,
                   (SELECT count(*) FROM lead WHERE tenant_id = %(t)s) AS leads,
                   (SELECT count(*) FROM payment WHERE tenant_id = %(t)s) AS payments,
                   (SELECT coalesce(sum(amount), 0) FROM payment
                     WHERE tenant_id = %(t)s) AS money,
                   (SELECT count(*) FROM payroll_entry WHERE tenant_id = %(t)s) AS payroll,
                   (SELECT count(*) FROM makeup_credit WHERE tenant_id = %(t)s) AS makeups,
                   (SELECT count(*) FROM subscription_hold WHERE tenant_id = %(t)s) AS holds,
                   (SELECT count(*) FROM audit_log WHERE tenant_id = %(t)s) AS audit
            """,
            {"t": tenant},
        )
        print(f"\n{title}: " + ", ".join(f"{k}={v}" for k, v in numbers.items()))
