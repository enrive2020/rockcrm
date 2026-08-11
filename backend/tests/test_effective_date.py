"""Дата операции отделена от системных часов (ADR-001, issue #15).

Проверяется то, что раньше было невозможно: внести отметку, продажу
и заморозку задним числом — и то, что этой возможностью нельзя
злоупотребить. Границ две: окно правки школы `backdating_days` и закрытый
зарплатный период, который не открывает никакое окно.

Демо-день — 12 августа 2026, сегодня по системным часам — 11 августа, как
и в остальных тестах этого набора. Абонементы демо действуют с 1 по 31
августа, поэтому вся история «уже истекло» строится на отдельном ученике,
у которого абонемента нет вовсе, — Амире Жанате.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import (
    HEADERS,
    TENANT,
    has_backdating_columns,
    recalc_trigger_ignores_the_clock,
    student,
)
from scripts import seed_demo

TODAY = dt.date(2026, 8, 11)
TZ = seed_demo.TZ

# Ученик без абонемента и без занятий: на нём удобно строить историю
# «продали в июне, отметили в июле», не задевая остальных.
ALONE = "zhanat"

# Направление и площадка для занятия, которое тест ставит в прошлое.
DRUMS = seed_demo.DISC["drums"]
DRUM_ROOM = seed_demo.ROOMS["drum_a"]
SHARAPOV = seed_demo.teacher_id("sharapov")
BRANCH = seed_demo.BRANCH_AF


def day(offset: int) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def sell(client, plan_key="drums4", student_key=ALONE, **body):
    return client.post(
        f"/api/v1/students/{student(student_key)}/subscriptions",
        json={"plan_id": seed_demo.plan_id(plan_key), **body},
        headers=HEADERS,
    )


def sold(client, **body) -> dict:
    response = sell(client, **body)
    assert response.status_code == 201, response.text
    return response.json()


def past_lesson(sql, on: str, at: str = "11:00", student_key: str = ALONE) -> str:
    """Занятие в прошлом. Расписание в API не редактируется — пишем напрямую."""
    row = sql.execute(
        """
        INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, discipline_id,
                            student_id, kind, starts_at, ends_at, status)
        VALUES (%(t)s, %(b)s, %(tc)s, %(rm)s, %(d)s, %(st)s, 'regular',
                %(start)s::timestamp AT TIME ZONE %(tz)s,
                (%(start)s::timestamp AT TIME ZONE %(tz)s) + make_interval(mins => 55),
                'planned')
        RETURNING id
        """,
        {
            "t": TENANT, "b": BRANCH, "tc": SHARAPOV, "rm": DRUM_ROOM, "d": DRUMS,
            "st": student(student_key), "start": f"{on} {at}", "tz": TZ,
        },
    ).fetchone()
    sql.commit()
    return str(row["id"])


def mark(client, lesson_id, student_key=ALONE, mark_value="came", **body):
    return client.post(
        f"/api/v1/lessons/{lesson_id}/attendance",
        json={"student_id": student(student_key), "mark": mark_value, **body},
        headers=HEADERS,
    )


def set_rule(sql, name: str, value) -> None:
    sql.execute(
        "UPDATE tenant SET default_rules = default_rules || %s::jsonb WHERE id = %s",
        (f'{{"{name}": {value}}}', TENANT),
    )
    sql.commit()


# ---------------------------------------------------------------------------
# 1. Задним числом наконец можно
# ---------------------------------------------------------------------------


def test_mark_finds_the_subscription_that_was_valid_that_day(client, sql):
    """Отметка от 15 июня списывается с июньского абонемента, истёкшего в июле.

    Это и есть issue #15 целиком: раньше `active_subscription` не отдавала
    абонемент со статусом `expired`, отметка проходила мимо журнала, и остаток
    оставался прежним. Из 2 900 отметок так потерялись 2 500 — и проверки
    сходимости при этом были зелёными.
    """
    subscription = sold(client, starts_on="2026-06-01")   # действует по 1 июля
    lesson_id = past_lesson(sql, "2026-06-15")

    applied = mark(client, lesson_id, effective_date=day(-20))
    assert applied.status_code == 201, applied.text
    body = applied.json()["applied"]
    assert body["subscription_id"] == subscription["subscription_id"]
    assert body["lessons_delta"] == -1
    assert body["lessons_after"] == 3

    # Журнал — источник правды об остатке, и он обязан объяснять списание.
    entries = sql.execute(
        "SELECT kind, lessons_delta FROM subscription_entry "
        "WHERE subscription_id = %s ORDER BY id",
        (subscription["subscription_id"],),
    ).fetchall()
    assert [(e["kind"], e["lessons_delta"]) for e in entries] == [
        ("purchase", 4),
        ("charge", -1),
    ]


def test_sale_backdated_starts_from_the_operation_date(client):
    """Продажа без `starts_on` начинается с даты операции, а не с сегодня.

    Абонемент, проданный за прошлый понедельник, с понедельника и действует:
    иначе неделя занятий осталась бы непокрытой, и списывать за неё было бы
    не с чего — то есть задача решалась бы наполовину.
    """
    body = sold(client, effective_date=day(-7))
    assert body["valid_from"] == day(-7)
    assert body["effective_date"] == day(-7)
    assert body["backdated"] is True

    # Явный starts_on по-прежнему сильнее: дата операции и первый день
    # действия — разные вопросы, и путать их нельзя. Продлеваем другому
    # ученику, чтобы не столкнуться с запретом двух абонементов на одни дни.
    other = sold(client, student_key="sagyndyk", plan_key="drums8",
                 effective_date=day(-7), starts_on="2026-09-04")
    assert other["valid_from"] == "2026-09-04"
    assert other["backdated"] is True


def test_freeze_can_start_on_the_operation_date(client):
    """Заморозку можно внести задним числом — в пределах окна школы.

    Раньше `create_hold` отказывал по системным часам, и внести каникулы,
    начавшиеся в понедельник, во вторник было нельзя вовсе.
    """
    subscription = sold(client, plan_key="drums8", starts_on=day(-5))

    refused = client.post(
        f"/api/v1/subscriptions/{subscription['subscription_id']}/holds",
        json={"from": day(-3), "to": day(2)},
        headers=HEADERS,
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "hold_in_past"

    accepted = client.post(
        f"/api/v1/subscriptions/{subscription['subscription_id']}/holds",
        json={"from": day(-3), "to": day(2), "effective_date": day(-3)},
        headers=HEADERS,
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["days"] == 5
    assert accepted.json()["backdated"] is True


def test_default_is_today_so_old_calls_do_not_change(client):
    """Без параметра — сегодня в поясе филиала. Ровно прежнее поведение."""
    body = sold(client)
    assert body["valid_from"] == TODAY.isoformat()
    assert body["effective_date"] == TODAY.isoformat()
    assert body["backdated"] is False


# ---------------------------------------------------------------------------
# 2. Окно правки
# ---------------------------------------------------------------------------


def test_operation_older_than_the_window_is_refused(client, sql):
    """Дата глубже окна — 422, а не молчаливая запись.

    Разрешить любую дату значило бы обменять один класс ошибок на другой,
    более тихий: заморозка «с прошлого месяца» пересчитала бы уже проведённые
    занятия, а истёкший абонемент, с которого снова можно списывать, уводит
    остаток в минус.
    """
    lesson_id = past_lesson(sql, "2026-06-15")
    sold(client, starts_on="2026-06-01")

    refused = mark(client, lesson_id, effective_date=day(-45))
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "effective_date_too_old"
    assert error["details"]["backdating_days"] == 30
    assert error["details"]["earliest"] == day(-30)


def test_school_can_switch_backdating_off(client, sql):
    """Ноль означает «только сегодня» — школа отключает возможность целиком."""
    set_rule(sql, "backdating_days", 0)
    lesson_id = past_lesson(sql, day(-1))
    sold(client, starts_on=day(-1))

    refused = mark(client, lesson_id, effective_date=day(-1))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "effective_date_too_old"

    # Сегодняшним числом — можно: запрещено прошлое, а не работа.
    assert mark(client, lesson_id).status_code == 201


def test_school_can_widen_the_window(client, sql):
    """Окно настраивается: школе с переездом нужно больше тридцати дней."""
    lesson_id = past_lesson(sql, "2026-06-15")
    sold(client, starts_on="2026-06-01")
    assert mark(client, lesson_id, effective_date=day(-45)).status_code == 422

    set_rule(sql, "backdating_days", 90)
    assert mark(client, lesson_id, effective_date=day(-45)).status_code == 201


def test_operation_dated_in_the_future_is_refused(client, sql):
    """Дата операции в будущем — ошибка ввода, а не «заранее».

    Отметить занятие, которого ещё не было, нельзя; абонемент, проданный
    завтрашним числом, выпал бы из сегодняшней кассы.
    """
    refused = sell(client, effective_date=day(1))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "effective_date_future"


# ---------------------------------------------------------------------------
# 3. Закрытый зарплатный период — жёсткая граница
# ---------------------------------------------------------------------------


def close_july(client):
    response = client.post(
        "/api/v1/payroll/periods",
        json={"from": "2026-07-01", "to": "2026-07-31"},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_closed_payroll_period_refuses_the_mark(client, sql):
    """В закрытый месяц отметку не внести: зарплата за него уже выдана.

    Правка идёт корректировкой в текущий период — механизм для этого уже
    есть (spec.md §6.2), и сообщение обязано на него указать.
    """
    close_july(client)
    lesson_id = past_lesson(sql, "2026-07-20")
    sold(client, starts_on="2026-07-15")

    refused = mark(client, lesson_id, effective_date="2026-07-20")
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "payroll_period_closed"
    assert error["details"]["from"] == "2026-07-01"
    assert error["details"]["to"] == "2026-07-31"
    assert "корректировкой" in error["message"]


def test_no_window_opens_a_closed_period(client, sql):
    """Жёсткая граница: даже окно в девяносто дней её не открывает."""
    close_july(client)
    set_rule(sql, "backdating_days", 90)
    lesson_id = past_lesson(sql, "2026-07-20")
    sold(client, starts_on="2026-07-15")

    refused = mark(client, lesson_id, effective_date="2026-07-20")
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "payroll_period_closed"

    # А сегодняшним числом та же отметка проходит и уходит корректировкой
    # в текущую ведомость — ровно то, что предлагает сообщение об отказе.
    assert mark(client, lesson_id).status_code == 201


def test_closed_period_also_guards_the_revocation(client, sql):
    """Отмена отметки пишет корректировку в зарплату — граница та же."""
    lesson_id = past_lesson(sql, "2026-07-20")
    sold(client, starts_on="2026-07-15")
    applied = mark(client, lesson_id, effective_date="2026-07-20")
    assert applied.status_code == 201, applied.text
    close_july(client)

    refused = client.delete(
        f"/api/v1/attendance/{applied.json()['attendance_id']}",
        params={"effective_date": "2026-07-20"},
        headers=HEADERS,
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "payroll_period_closed"

    allowed = client.delete(
        f"/api/v1/attendance/{applied.json()['attendance_id']}", headers=HEADERS
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["backdated"] is False


# ---------------------------------------------------------------------------
# 4. Пометка и статус — после миграции 009
#
# Обе проверки спрашивают базу, а не файлы в db/: до накатки миграции
# проверять нечего, и падать на этом тест не должен — он проверял бы
# отсутствующую схему, а не ошибку в коде.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not has_backdating_columns(), reason="нет миграции 009: колонок backdated нет"
)
def test_backdated_operations_are_marked_in_the_journals(client, sql):
    """Внесённое задним числом видно в журнале и в отметке.

    Без пометки восстановить картину при разборе спора нельзя: `created_at`
    отвечает, когда появилась строка, но не отвечает, за какой день она.
    """
    subscription = sold(client, starts_on="2026-06-01", effective_date=day(-20))
    lesson_id = past_lesson(sql, "2026-06-15")
    applied = mark(client, lesson_id, effective_date=day(-20))
    assert applied.status_code == 201, applied.text
    assert applied.json()["backdated"] is True

    flags = sql.execute(
        "SELECT kind, backdated FROM subscription_entry "
        "WHERE subscription_id = %s ORDER BY id",
        (subscription["subscription_id"],),
    ).fetchall()
    assert [row["backdated"] for row in flags] == [True, True]

    marked = sql.execute(
        "SELECT backdated FROM attendance WHERE id = %s",
        (applied.json()["attendance_id"],),
    ).fetchone()
    assert marked["backdated"] is True


@pytest.mark.skipif(
    not has_backdating_columns(), reason="нет миграции 009: колонок backdated нет"
)
def test_operations_dated_today_are_not_marked(client, sql):
    """Обычная работа пометки не получает — иначе флаг перестаёт что-то значить."""
    subscription = sold(client)
    flags = sql.execute(
        "SELECT backdated FROM subscription_entry WHERE subscription_id = %s",
        (subscription["subscription_id"],),
    ).fetchall()
    assert [row["backdated"] for row in flags] == [False]


@pytest.mark.skipif(
    not recalc_trigger_ignores_the_clock(),
    reason="нет миграции 009: триггер всё ещё ставит expired по current_date",
)
def test_inserting_a_journal_row_does_not_expire_the_subscription(client, sql):
    """Вставка строки в журнал больше не объявляет абонемент истёкшим.

    Прежде это делал триггер по `current_date`: абонемент, проданный
    «в марте», становился `expired` в ту же секунду, и все последующие
    отметки проходили мимо журнала. Перевод в `expired` — работа ночной
    сверки `sync_statuses`, и это осознанный размен: сверку видно
    и можно проверить, а неявный пересчёт при вставке — нет.
    """
    subscription = sold(client, starts_on="2026-06-01")   # истёк 1 июля
    status = sql.execute(
        "SELECT status FROM subscription WHERE id = %s",
        (subscription["subscription_id"],),
    ).fetchone()
    assert status["status"] == "active"

    from app import billing

    billing.sync_statuses(sql.cursor(), TODAY)
    sql.commit()
    after = sql.execute(
        "SELECT status FROM subscription WHERE id = %s",
        (subscription["subscription_id"],),
    ).fetchone()
    assert after["status"] == "expired"
