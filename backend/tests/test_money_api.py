"""Экран «Деньги и ЗП»: ведомость, закрытие периода и отчёты владельца.

Главное, что здесь проверяется, — закрытие периода. Всё остальное на этом
экране можно пересчитать заново и сверить глазами, а закрытый период сверить
уже нечем: деньги отданы людям на руки. Поэтому тестов на него больше, чем
на любой отчёт, и самый важный из них — что начисление, появившееся после
закрытия, в закрытую ведомость не попадает и уходит в следующую (spec.md §6.2).
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import (
    BRANCH_AF,
    HEADERS,
    HEADERS_OTHER,
    TENANT,
    lesson,
    student,
    subscription,
)
from scripts import seed_demo

# Демо-данные: абонементы всех учеников действуют с 1 по 31 августа 2026,
# отметки стоят на истории Амины (4–7 августа) и на демо-дне 12 августа.
AUGUST = {"from": "2026-08-01", "to": "2026-08-31"}
# Период, который заведомо закончился: сегодня в демо — 11 августа 2026.
EARLY_AUGUST = {"from": "2026-08-01", "to": "2026-08-10"}
REST_OF_AUGUST = {"from": "2026-08-11", "to": "2026-08-31"}

BRANCH_AB = seed_demo.BRANCH_AB

SHARAPOV = seed_demo.teacher_id("sharapov")
FEDKO = seed_demo.teacher_id("fedko")
ISENOVA = seed_demo.teacher_id("isenova")

# Занятия истории Амины: 2 августа отмена заранее (без начисления),
# 4 и 7 — «пришла», 5 — прогул. Ставка Шарапова 4500 ₸, прогул оплачивается.
AMINA_CAME = seed_demo._id("4a1")   # 4 августа
AMINA_NO_SHOW = seed_demo._id("4a2")  # 5 августа


def get(client, path, params=None, headers=None):
    response = client.get(path, params=params, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def sheet(client, period=AUGUST, **params):
    return get(client, "/api/v1/payroll", {**period, **params})


def row_of(data, teacher_id):
    for row in data["teachers"]:
        if row["teacher"]["id"] == teacher_id:
            return row
    raise AssertionError(f"преподаватель {teacher_id} не найден в ведомости")


def close(client, period, headers=None):
    return client.post(
        "/api/v1/payroll/periods", json=period, headers=headers or HEADERS
    )


def attendance_id_of(client, lesson_id, student_key):
    card = get(client, f"/api/v1/lessons/{lesson_id}")
    for person in card["participants"]:
        if person["student_id"] == student(student_key):
            return person["attendance_id"]
    raise AssertionError("отметка не найдена")


# ---------------------------------------------------------------------------
# Ведомость
# ---------------------------------------------------------------------------


def test_sheet_shows_what_attendance_accrued(client, sql):
    """Ведомость не считает зарплату заново — она показывает начисленное.

    Сверяем с суммой в payroll_entry напрямую: второй расчёт разошёлся бы
    с первым при первой же смене ставки, и объяснить преподавателю, какая
    из двух цифр верна, стало бы нечем.
    """
    data = sheet(client)
    expected = sql.execute(
        """SELECT coalesce(sum(amount), 0) AS total, count(*) AS n
           FROM payroll_entry WHERE tenant_id = %s""",
        (TENANT,),
    ).fetchone()

    assert data["totals"]["total"] == int(expected["total"])
    assert data["totals"]["entries"] == int(expected["n"])
    assert row_of(data, SHARAPOV)["total"] == 13500   # три занятия по 4500
    assert row_of(data, FEDKO)["total"] == 8400       # два занятия по 4200
    assert row_of(data, ISENOVA)["total"] == 4000


def test_sheet_counts_no_shows_in_their_own_column(client):
    """Прогул оплачен, но в ведомости стоит отдельно — спор всегда о нём."""
    data = sheet(client)
    sharapov = row_of(data, SHARAPOV)
    assert sharapov["lessons"] == 3
    assert sharapov["no_shows"] == 1
    assert row_of(data, FEDKO)["no_shows"] == 1
    assert row_of(data, ISENOVA)["no_shows"] == 0


def test_sheet_rate_comes_from_the_accruals_not_from_the_price_list(client):
    """Ставка в ведомости — та, по которой посчитали, а не та, что сегодня."""
    data = sheet(client)
    assert row_of(data, SHARAPOV)["rate"] == 4500
    assert row_of(data, SHARAPOV)["rate_varies"] is False
    assert row_of(data, FEDKO)["rate"] == 4200


def test_sheet_period_defaults_to_the_current_month(client):
    data = get(client, "/api/v1/payroll")
    today = dt.date.today()
    first = today.replace(day=1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    assert data["period"] == {"from": first.isoformat(), "to": last.isoformat()}


def test_sheet_filters_by_branch(client):
    """В Абая 150 из отмеченного только урок Исеновой."""
    data = sheet(client, branch_id=BRANCH_AB)
    assert [r["teacher"]["id"] for r in data["teachers"]] == [ISENOVA]
    assert data["totals"]["total"] == 4000


def test_sheet_of_untouched_period_is_empty_but_valid(client):
    data = sheet(client, {"from": "2026-01-01", "to": "2026-01-31"})
    assert data["teachers"] == []
    assert data["totals"]["total"] == 0
    assert data["closed"] is False


def test_teacher_detail_explains_every_line(client):
    """Расшифровка — то, чем администратор отвечает на «откуда сумма»."""
    data = get(client, f"/api/v1/payroll/teachers/{SHARAPOV}", AUGUST)
    assert data["teacher"]["name"] == "Дмитрий Шарапов"
    assert data["totals"]["total"] == 13500
    assert len(data["entries"]) == 3

    dates = [e["date"] for e in data["entries"]]
    assert dates == ["2026-08-04", "2026-08-05", "2026-08-07"]
    assert [e["mark"] for e in data["entries"]] == ["came", "no_show", "came"]
    assert all(e["student"] == "Амина Сагындык" for e in data["entries"])
    assert all(e["amount"] == 4500 for e in data["entries"])
    # Снимок расчёта обязан ехать целиком: через полгода объяснить сумму
    # больше нечем — ставки к тому времени поменяются.
    assert all(e["calc"] for e in data["entries"])
    assert all(e["carried_over"] is False for e in data["entries"])


def test_teacher_detail_totals_repeat_the_sheet_row(client):
    detail = get(client, f"/api/v1/payroll/teachers/{FEDKO}", AUGUST)
    assert detail["totals"] == row_of(sheet(client), FEDKO)


def test_teacher_detail_of_unknown_teacher_is_404(client):
    response = client.get(
        f"/api/v1/payroll/teachers/{seed_demo.OTHER_USER}", params=AUGUST, headers=HEADERS
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Закрытие периода
# ---------------------------------------------------------------------------


def test_close_period_stamps_its_accruals(client, sql):
    response = close(client, EARLY_AUGUST)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["period"] == EARLY_AUGUST
    assert body["closed"] is True
    # 4, 5 и 7 августа — вся история Амины; демо-день 12 августа за границей.
    assert body["entries"] == 3
    assert body["teachers"] == 1
    assert body["total"] == 13500
    assert "закрыт" in body["message"]

    stamped = sql.execute(
        "SELECT count(*) AS n FROM payroll_entry WHERE period_id = %s", (body["id"],)
    ).fetchone()
    assert int(stamped["n"]) == 3


def test_closed_sheet_reads_by_stamp_and_reports_who_closed_it(client):
    # Имя сверяется с тем, кто РЕАЛЬНО закрыл период, а не с литералом
    # из посева: «кто закрыл» — свойство сессии, и тест, знающий имя фикстуры
    # наизусть, ломается от любой правки демо-данных, ничего не проверив.
    closer = get(client, "/api/v1/auth/me")["name"]
    close(client, EARLY_AUGUST)
    data = sheet(client, EARLY_AUGUST)
    assert data["closed"] is True
    assert data["closed_by"] == closer
    assert data["closed_at"].endswith("+05:00")
    assert data["totals"]["total"] == 13500
    assert "Период закрыт" in data["note"]


def test_accrual_after_close_does_not_get_into_the_closed_period(client, sql):
    """Главное правило §6.2: закрытый период не пересчитывается.

    Закрываем начало августа, потом отменяем отметку внутри него. Отмена
    пишет корректирующее начисление датой того же занятия — и оно обязано
    остаться за пределами закрытой ведомости.
    """
    closed = close(client, EARLY_AUGUST).json()
    before = sheet(client, EARLY_AUGUST)["totals"]["total"]

    attendance_id = attendance_id_of(client, AMINA_CAME, "sagyndyk")
    revoked = client.delete(f"/api/v1/attendance/{attendance_id}", headers=HEADERS)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["reverted"]["teacher_amount"] == -4500

    # Корректировка легла в базу...
    correction = sql.execute(
        """SELECT id, amount, period_id FROM payroll_entry
           WHERE kind = 'correction' AND tenant_id = %s""",
        (TENANT,),
    ).fetchall()
    assert len(correction) == 1
    assert int(correction[0]["amount"]) == -4500
    # ...но штампа закрытого периода не получила.
    assert correction[0]["period_id"] is None

    # ...и закрытая ведомость не изменилась.
    after = sheet(client, EARLY_AUGUST)
    assert after["totals"]["total"] == before == 13500
    assert after["period_id"] == closed["id"]


def test_correction_for_a_closed_month_shows_up_in_the_next_period(client):
    """Правка уходит корректировкой в следующий период — и объясняет себя."""
    close(client, EARLY_AUGUST)
    attendance_id = attendance_id_of(client, AMINA_CAME, "sagyndyk")
    client.delete(f"/api/v1/attendance/{attendance_id}", headers=HEADERS)

    following = sheet(client, REST_OF_AUGUST)
    sharapov = row_of(following, SHARAPOV)

    assert sharapov["corrections"] == -4500
    assert sharapov["total"] == -4500
    # Начисление за уже закрытый месяц помечено, иначе ведомость не сходится
    # с числом занятий и преподаватель считает, что ему приписали лишнее.
    assert sharapov["carried_over"] == -4500
    assert sharapov["carried_over_entries"] == 1
    assert following["totals"]["carried_over_entries"] == 1
    assert "закрытые месяцы" in following["note"]

    detail = get(client, f"/api/v1/payroll/teachers/{SHARAPOV}", REST_OF_AUGUST)
    carried = [e for e in detail["entries"] if e["carried_over"]]
    assert len(carried) == 1
    assert carried[0]["kind"] == "correction"
    assert carried[0]["date"] == "2026-08-04"


def test_new_mark_inside_a_closed_month_also_lands_in_the_next_period(client, sql):
    """Не только отмена: любое начисление после закрытия минует закрытый период.

    Отмечаем занятие 5 августа заново (внутри уже закрытого периода) —
    начисление появляется, но штампа не получает.
    """
    close(client, EARLY_AUGUST)
    closed_total = sheet(client, EARLY_AUGUST)["totals"]["total"]

    attendance_id = attendance_id_of(client, AMINA_NO_SHOW, "sagyndyk")
    client.delete(f"/api/v1/attendance/{attendance_id}", headers=HEADERS)
    again = client.post(
        f"/api/v1/lessons/{AMINA_NO_SHOW}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS,
    )
    if again.status_code == 409:
        pytest.skip("переотметка требует миграции 008 (attendance_active_uniq)")
    assert again.status_code == 201, again.text

    unstamped = sql.execute(
        """SELECT count(*) AS n FROM payroll_entry
           WHERE tenant_id = %s AND period_id IS NULL
             AND lesson_id = %s""",
        (TENANT, AMINA_NO_SHOW),
    ).fetchone()
    # Корректировка отмены и новое начисление — обе строки мимо закрытого.
    assert int(unstamped["n"]) == 2
    assert sheet(client, EARLY_AUGUST)["totals"]["total"] == closed_total


def test_closing_the_same_period_twice_is_rejected(client):
    assert close(client, EARLY_AUGUST).status_code == 201
    response = close(client, EARLY_AUGUST)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "period_overlap"


def test_overlapping_period_is_rejected(client):
    assert close(client, EARLY_AUGUST).status_code == 201
    response = close(client, {"from": "2026-08-05", "to": "2026-08-09"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "period_overlap"


def test_period_that_has_not_ended_cannot_be_closed(client):
    """Закрывать идущий месяц нельзя: занятия в нём ещё будут."""
    response = close(client, AUGUST)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "period_not_over"


def test_reversed_period_is_rejected(client):
    response = close(client, {"from": "2026-08-10", "to": "2026-08-01"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_period"


def test_closed_period_appears_in_the_list_with_its_totals(client):
    close(client, EARLY_AUGUST)
    periods = get(client, "/api/v1/payroll/periods")
    assert len(periods) == 1
    assert periods[0]["period"] == EARLY_AUGUST
    assert periods[0]["closed"] is True
    assert periods[0]["entries"] == 3
    assert periods[0]["teachers"] == 1
    assert periods[0]["total"] == 13500


def test_closing_writes_audit(client, sql):
    body = close(client, EARLY_AUGUST).json()
    row = sql.execute(
        """SELECT payload FROM audit_log
           WHERE action = 'payroll.period.close' AND entity_id = %s""",
        (body["id"],),
    ).fetchone()
    assert row is not None
    assert row["payload"]["entries"] == 3
    assert row["payload"]["total"] == 13500


def test_foreign_tenant_cannot_close_our_period(client):
    """Периоды соседней школы — её дело, и наши строки они не штампуют."""
    assert close(client, EARLY_AUGUST, headers=HEADERS_OTHER).status_code == 201
    assert get(client, "/api/v1/payroll/periods", headers=HEADERS) == []
    assert sheet(client, EARLY_AUGUST)["closed"] is False
    assert sheet(client, EARLY_AUGUST)["totals"]["total"] == 13500


# ---------------------------------------------------------------------------
# Выручка
# ---------------------------------------------------------------------------


def test_revenue_counts_money_received(client, sql):
    """Выручка = поступившие деньги, а не выставленные счета."""
    data = get(client, "/api/v1/reports/revenue", AUGUST)
    expected = sql.execute(
        """SELECT coalesce(sum(amount), 0) AS total, count(*) AS n
           FROM payment WHERE tenant_id = %s AND status = 'succeeded'""",
        (TENANT,),
    ).fetchone()

    assert data["total"] == int(expected["total"]) == 97200
    assert data["payments"] == int(expected["n"]) == 2
    assert data["by_method"] == [
        {"method": "kaspi", "amount": 97200, "payments": 2, "share_pct": 100}
    ]


def test_revenue_splits_by_discipline_and_branch(client):
    data = get(client, "/api/v1/reports/revenue", AUGUST)
    by_discipline = {row["name"]: row["amount"] for row in data["by_discipline"]}
    assert by_discipline == {"Барабаны": 48600, "Гитара": 48600}
    assert all(row["share_pct"] == 50 for row in data["by_discipline"])

    assert [row["name"] for row in data["by_branch"]] == ["Аль-Фараби 53В"]
    assert data["by_branch"][0]["amount"] == 97200


def test_revenue_by_month_covers_the_whole_period(client):
    data = get(client, "/api/v1/reports/revenue", {"from": "2026-06-01", "to": "2026-08-31"})
    assert data["by_month"] == [{"month": "2026-08", "amount": 97200, "payments": 2}]


def test_revenue_of_a_sale_without_payment_stays_zero(client):
    """Продажа в долг выручкой не является — деньги ещё не пришли."""
    before = get(client, "/api/v1/reports/revenue", AUGUST)["total"]
    sold = client.post(
        f"/api/v1/students/{student('so')}/subscriptions",
        json={"plan_id": seed_demo.plan_id("vocal8"), "starts_on": "2026-09-01"},
        headers=HEADERS,
    )
    assert sold.status_code == 201, sold.text
    assert get(client, "/api/v1/reports/revenue", AUGUST)["total"] == before


# ---------------------------------------------------------------------------
# Загрузка кабинетов
# ---------------------------------------------------------------------------


def test_room_utilization_is_busy_minutes_over_capacity(client):
    """Считается честно: занятые минуты делятся на часы работы × дни × кабинеты."""
    data = get(client, "/api/v1/reports/rooms", {"from": "2026-08-12", "to": "2026-08-12"})
    rooms = {row["room"]: row for row in data["rooms"]}

    # Барабанная A 12 августа: 55 + 55 + 45 + 85 + 55 = 295 минут
    # при рабочем дне филиала 10:00–21:00 = 660 минут.
    drum_a = rooms["Барабанная A"]
    assert drum_a["busy_minutes"] == 295
    assert drum_a["capacity_minutes"] == 660
    assert drum_a["utilization_pct"] == round(295 / 660 * 100)

    assert data["utilization_pct"] == round(
        data["busy_minutes"] / data["capacity_minutes"] * 100
    )


def test_rooms_are_sorted_by_load(client):
    """Отчёт отвечает на «где кончилось место» — занятый кабинет идёт первым."""
    data = get(client, "/api/v1/reports/rooms", {"from": "2026-08-12", "to": "2026-08-12"})
    loads = [row["utilization_pct"] for row in data["rooms"]]
    assert loads == sorted(loads, reverse=True)
    assert data["rooms"][0]["room"] == "Барабанная A"


def test_rooms_report_shows_how_capacity_was_counted(client):
    data = get(client, "/api/v1/reports/rooms", {"from": "2026-08-12", "to": "2026-08-12"})
    branches = {row["branch"]: row for row in data["branches"]}
    assert branches["Аль-Фараби 53В"]["open_days"] == 1
    assert branches["Аль-Фараби 53В"]["open_minutes_per_day"] == 660
    assert branches["Аль-Фараби 53В"]["rooms"] == 3
    assert "дни с занятиями" in data["capacity_note"]


def test_rooms_filter_by_branch(client):
    data = get(
        client,
        "/api/v1/reports/rooms",
        {"from": "2026-08-12", "to": "2026-08-12", "branch_id": BRANCH_AF},
    )
    assert {row["branch_id"] for row in data["rooms"]} == {BRANCH_AF}


# ---------------------------------------------------------------------------
# Отток (issue #25)
#
# Отчёт считает ЛЮДЕЙ, а не абонементы, и не выносит вердикт раньше, чем
# истекла отсрочка продления. Прежняя версия делала обе ошибки сразу
# и показывала 76% там, где школу покинули 16%: абонемент, кончающийся
# 31 августа, попадал в «не продлили» уже одиннадцатого числа, а на демо-данных
# отчёт бодро отвечал «отток 100%» про восемнадцать учеников, которые
# в этот момент ходили на занятия.
#
# Все проверки ниже опираются на то же «сегодня», что и остальной файл:
# 11 августа 2026, абонементы демо действуют с 1 по 31 августа.
# ---------------------------------------------------------------------------

# Ученик без абонемента в демо: на нём удобно строить историю ухода,
# не задевая восемнадцать остальных.
ALONE = "zhanat"


def churn(client, period, **params):
    return get(client, "/api/v1/reports/churn", {**period, **params})


def sell(client, student_key, plan_key, starts_on):
    response = client.post(
        f"/api/v1/students/{student(student_key)}/subscriptions",
        json={"plan_id": seed_demo.plan_id(plan_key), "starts_on": starts_on},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_running_subscription_is_not_a_departure(client):
    """Ученик, чей абонемент действует сегодня, ушедшим быть не может.

    Именно этого не хватало отчёту: он объявлял непродлёнными абонементы,
    которые кончаются в конце месяца, — то есть выносил вердикт о днях,
    которые ещё не наступили.
    """
    data = churn(client, AUGUST)
    assert data["as_of"] == "2026-08-11"
    assert data["students_total"] == 18
    assert data["retained"] == 18
    assert data["churned"] == 0
    assert data["churn_pct"] == 0
    # Документы при этом посчитаны честно: восемнадцать абонементов
    # действительно кончаются в августе, и ни один пока не продлён.
    assert data["ended"] == 18
    assert data["renewed"] == 0
    assert data["grace_days"] == 14


def test_a_pause_between_subscriptions_is_not_a_departure(client):
    """Ученик, вернувшийся после паузы длиннее отсрочки, не ушёл.

    Пауза в два месяца — это лето, а не уход, и человек, который сегодня
    снова ходит на занятия, не может числиться в оттоке за май.
    """
    sell(client, ALONE, "drums4", "2026-05-01")   # по 31 мая
    sell(client, ALONE, "drums4", "2026-08-01")   # вернулся в августе

    data = churn(client, {"from": "2026-05-01", "to": "2026-05-31"})
    assert data["students_total"] == 1
    assert data["retained"] == 1
    assert data["churned"] == 0
    assert data["students"] == []


def test_one_person_is_one_departure_however_many_subscriptions(client):
    """Два абонемента подряд — два документа, но один человек.

    Отток — про людей: владелец смотрит на это число, чтобы понять,
    теряет ли он клиентов, а не сколько бумаг закрылось.
    """
    sell(client, ALONE, "drums4", "2026-04-01")    # по 1 мая
    sell(client, ALONE, "guitar4", "2026-05-02")   # по 1 июня, больше ничего

    data = churn(client, {"from": "2026-04-01", "to": "2026-06-30"})
    assert data["ended"] == 2       # документов закончилось два
    assert data["renewed"] == 1     # второй документ продлевает первый
    assert data["students_total"] == 1
    assert data["churned"] == 1     # а ушёл один человек
    assert data["churn_pct"] == 100
    assert [row["student_id"] for row in data["students"]] == [student(ALONE)]


def test_verdict_waits_until_the_grace_window_closes(client):
    """Пока окно продления открыто, ученик в отток не попадает.

    Продление покупают и через неделю после окончания. Отчёт обязан
    отличать «не продлил» от «ещё не успел»: по первому звонят с удержанием,
    по второму — нет.
    """
    sell(client, ALONE, "drums4", "2026-07-05")   # по 4 августа, отсрочка до 18-го
    period = {"from": "2026-07-01", "to": "2026-08-31"}

    patient = churn(client, period)
    assert patient["pending"] == 1
    assert patient["churned"] == 0
    assert "в отсрочке" in patient["note"]

    # Та же история при нулевой отсрочке: окно закрыто в день окончания.
    strict = churn(client, period, grace_days=0)
    assert strict["pending"] == 0
    assert strict["churned"] == 1


def test_frozen_student_is_on_holidays_not_gone(client, sql):
    """Заморозка — каникулы, а не уход.

    Школа, которая знает про каникулы ученика, не имеет права показывать
    его в оттоке: удержание такому ученику не нужно, а число портится.
    """
    sold = sell(client, ALONE, "drums4", "2026-06-01")   # по 1 июля
    period = {"from": "2026-06-01", "to": "2026-07-31"}
    assert churn(client, period)["churned"] == 1

    frozen = client.post(
        f"/api/v1/subscriptions/{sold['subscription_id']}/holds",
        json={"from": "2026-08-11", "to": "2026-08-18", "reason": "каникулы"},
        headers=HEADERS,
    )
    assert frozen.status_code == 201, frozen.text

    data = churn(client, period)
    assert data["frozen"] == 1
    assert data["churned"] == 0


def test_churn_is_shown_next_to_the_archive(client, sql):
    """Два числа вместо одного: уход по абонементам и перевод в архив.

    Это две разные оценки одного события, и они расходятся всегда:
    администратор архивирует ученика тогда, когда узнал об уходе, а не когда
    кончился абонемент. Показывать одно число значит выдать оценку за факт.
    """
    sell(client, ALONE, "drums4", "2026-06-01")   # по 1 июля — ушёл по абонементам
    sql.execute(
        "UPDATE student SET archived_at = now() WHERE id IN (%s, %s)",
        (student(ALONE), student("so")),          # «Со» продолжает ходить
    )
    sql.commit()

    data = churn(client, {"from": "2026-06-01", "to": "2026-08-31"})
    assert data["churned"] == 1
    assert data["archived"] == 2
    assert data["archived_pct"] == round(2 / data["students_total"] * 100)
    gone = next(row for row in data["students"] if row["student_id"] == student(ALONE))
    assert gone["archived_on"] == "2026-08-11"


def test_churn_by_teacher_reports_the_size_of_its_base(client):
    """Процент без базы нечитаем: 100% у преподавателя с одним учеником.

    Прежний отчёт называл «худшим» преподавателя с 82% оттока, и по такому
    числу увольняют. Рядом с процентом обязано стоять, из скольких человек
    он посчитан.
    """
    sell(client, ALONE, "drums4", "2026-06-01")   # Амир Жанат ходит к Шарапову

    data = churn(client, {"from": "2026-06-01", "to": "2026-08-31"})
    by_teacher = {row["teacher_id"]: row for row in data["by_teacher"]}
    assert by_teacher[SHARAPOV]["churned"] == 1
    assert by_teacher[SHARAPOV]["students"] == 5     # четверо барабанщиков и Амир
    assert by_teacher[SHARAPOV]["churn_pct"] == 20
    assert by_teacher[FEDKO]["churned"] == 0
    assert by_teacher[ISENOVA]["churned"] == 0
    # Отсортировано по числу ушедших: первым идёт тот, к кому идти разбираться.
    assert data["by_teacher"][0]["teacher_id"] == SHARAPOV


def test_churn_names_the_students_with_their_last_lesson(client):
    sell(client, ALONE, "drums4", "2026-06-01")
    data = churn(client, {"from": "2026-06-01", "to": "2026-07-31"})
    amir = next(row for row in data["students"] if row["student_id"] == student(ALONE))
    assert amir["name"] == "Амир Жанат"
    assert amir["teacher"] == "Дмитрий Шарапов"
    assert amir["ended_on"] == "2026-07-01"
    assert amir["archived_on"] is None


def test_churn_grace_period_is_honest_about_its_length(client):
    """Продление через месяц при grace_days = 0 продлением уже не считается.

    Проверка про документы: `renewed` считает абонементы, и отсрочка здесь
    ровно та, которую попросили.
    """
    sell(client, "sagyndyk", "drums8", "2026-10-01")
    lenient = churn(client, AUGUST, grace_days=45)
    strict = churn(client, AUGUST, grace_days=0)
    assert lenient["renewed"] == 1
    assert strict["renewed"] == 0


# ---------------------------------------------------------------------------
# Долги
# ---------------------------------------------------------------------------


def test_no_debts_when_everything_is_paid(client):
    """Семья Сагындык оплатила оба абонемента полностью."""
    data = get(client, "/api/v1/reports/debts")
    assert data["families"] == 0
    assert data["total"] == 0
    assert data["items"] == []


def test_sale_without_payment_creates_a_debt_the_card_agrees_with(client):
    """Долг в отчёте и долг в карточке ученика считаются одной формулой."""
    sold = client.post(
        f"/api/v1/students/{student('sagyndyk')}/subscriptions",
        json={"plan_id": seed_demo.plan_id("drums8"), "starts_on": "2026-09-01"},
        headers=HEADERS,
    )
    assert sold.status_code == 201, sold.text

    data = get(client, "/api/v1/reports/debts")
    assert data["families"] == 1
    item = data["items"][0]
    assert item["payer"] == "Гульнара Сагындык"
    assert sorted(item["students"]) == ["Амина Сагындык", "Тимур Сагындык"]
    assert item["debt"] == item["charged"] - item["paid"] == 48600
    assert item["last_paid_on"] == "2026-08-01"

    card = get(client, f"/api/v1/students/{student('sagyndyk')}")
    assert card["family"]["debt"] == item["debt"]
    assert data["total"] == item["debt"]


# ---------------------------------------------------------------------------
# Шапка экрана
# ---------------------------------------------------------------------------


def test_summary_gathers_the_four_numbers_of_the_screen(client):
    data = get(client, "/api/v1/reports/summary", AUGUST)

    assert data["revenue"]["amount"] == 97200
    # Прошлого месяца в демо нет — процента роста не существует, и «+100%»
    # от нуля было бы враньём.
    assert data["revenue"]["previous"] == 0
    assert data["revenue"]["change_pct"] is None
    assert data["revenue"]["previous_period"] == {"from": "2026-07-01", "to": "2026-07-31"}

    assert 0 <= data["rooms"]["utilization_pct"] <= 100
    # Абонементы всех восемнадцати действуют по 31 августа: ушедших нет
    # и «худшего преподавателя» нет тоже. Пустая карточка честнее, чем
    # виноватый, назначенный из чисел, которых ещё не существует.
    assert data["churn"]["students_total"] == 18
    assert data["churn"]["churned"] == 0
    assert data["churn"]["archived"] == 0
    assert data["churn"]["worst_teacher"] is None
    assert data["payroll"]["total"] == 25900
    assert data["payroll"]["closed"] is False


def test_summary_attention_block_counts_facts(client):
    data = get(client, "/api/v1/reports/summary", AUGUST)["attention"]
    # Три ученика, как в прототипе: двое с остатком 2 и Дмитрий Со с нулём —
    # срок его абонемента ещё идёт, значит звонить надо и ему.
    assert data["subscriptions_running_low"] == 3
    assert data["makeups_open"] == 1
    assert data["frozen_now"] == 0
    assert data["debt_families"] == 0


# ---------------------------------------------------------------------------
# Изоляция и заголовки
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/payroll",
        "/api/v1/payroll/periods",
        "/api/v1/reports/revenue",
        "/api/v1/reports/rooms",
        "/api/v1/reports/churn",
        "/api/v1/reports/debts",
        "/api/v1/reports/summary",
    ],
)
def test_money_screens_require_session(client, path):
    assert client.get(path).status_code == 401


def test_foreign_tenant_gets_its_own_zeros(client):
    """У соседней школы ни отметок, ни платежей — и наши она не видит."""
    assert get(client, "/api/v1/payroll", AUGUST, HEADERS_OTHER)["totals"]["total"] == 0
    assert get(client, "/api/v1/reports/revenue", AUGUST, HEADERS_OTHER)["total"] == 0
    assert get(client, "/api/v1/reports/debts", None, HEADERS_OTHER)["families"] == 0


def test_foreign_tenant_cannot_read_our_teacher_sheet(client):
    response = client.get(
        f"/api/v1/payroll/teachers/{SHARAPOV}", params=AUGUST, headers=HEADERS_OTHER
    )
    assert response.status_code == 404


def test_bad_period_is_rejected(client):
    response = client.get(
        "/api/v1/payroll", params={"from": "2026-08-31", "to": "2026-08-01"}, headers=HEADERS
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_period"


# ---------------------------------------------------------------------------
# Применённый эффект отметки (issue #22)
#
# Эти проверки живут здесь, а не в тестах посещаемости, потому что речь
# о деньгах и остатке уже применённой отметки: `mark_effects` отвечает
# на «что будет», `applied_effect` — на «что уже случилось».
# ---------------------------------------------------------------------------


def _person(client, lesson_id, student_key):
    card = get(client, f"/api/v1/lessons/{lesson_id}")
    for person in card["participants"]:
        if person["student_id"] == student(student_key):
            return person
    raise AssertionError("участник не найден")


def test_unmarked_participant_has_no_applied_effect(client):
    assert _person(client, lesson("les02"), "sagyndyk")["applied_effect"] is None


def test_applied_effect_reports_the_facts_of_the_mark(client, sql):
    """Применённый эффект собирается из журнала, а не пересчётом правил."""
    response = client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text

    person = _person(client, lesson("les02"), "sagyndyk")
    applied = person["applied_effect"]
    assert applied["mark"] == "came"
    assert applied["attendance_id"] == person["attendance_id"]
    assert applied["lessons_delta"] == -1
    assert applied["teacher_amount"] == 4500
    # Остаток «после» — настоящий текущий остаток абонемента.
    assert applied["lessons_after"] == person["subscription"]["lessons_balance"] == 4
    assert "списано" in applied["summary"]

    balance = sql.execute(
        "SELECT lessons_balance FROM subscription WHERE id = %s",
        (subscription("sagyndyk"),),
    ).fetchone()
    assert applied["lessons_after"] == int(balance["lessons_balance"])


def test_preview_of_a_marked_participant_counts_from_the_balance_before(client):
    """issue #22: у отмеченного участника предпросмотр вычитал занятие дважды.

    Остаток 5 → отметка «пришёл» → 4. Открываем карточку заново: обещание
    той же отметки обязано быть 4, а не 3 — переотметка начинается с отмены
    прежней, и она вернёт занятие назад.
    """
    before = _person(client, lesson("les02"), "sagyndyk")
    assert before["subscription"]["lessons_balance"] == 5
    assert before["mark_effects"]["came"]["lessons_after"] == 4

    client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS,
    )

    after = _person(client, lesson("les02"), "sagyndyk")
    assert after["subscription"]["lessons_balance"] == 4
    assert after["mark_effects"]["came"]["lessons_delta"] == -1
    assert after["mark_effects"]["came"]["lessons_after"] == 4, (
        "предпросмотр посчитан от уже уменьшенного остатка"
    )
    # Отмена заранее не списывает и даёт отработку — остаток вернулся бы к 5.
    assert after["mark_effects"]["cancelled_early"]["lessons_after"] == 5
    assert after["mark_effects"]["cancelled_early"]["makeups_after"] == 2


def test_applied_effect_of_a_mark_that_granted_a_makeup(client):
    """Отработка в применённом эффекте — тоже факт из журнала."""
    client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "cancelled_early"},
        headers=HEADERS,
    )
    applied = _person(client, lesson("les02"), "sagyndyk")["applied_effect"]
    assert applied["lessons_delta"] == 0
    assert applied["makeups_delta"] == 1
    assert applied["makeups_after"] == 2
    assert applied["teacher_amount"] == 0
    assert "отработка" in applied["summary"]


def test_applied_effect_survives_a_change_of_school_rules(client, sql):
    """Правила школы сменились — применённая отметка не пересчиталась.

    Ровно ради этого применённый эффект читается из журнала: пересчёт
    правилами показал бы не ту цифру, которая ушла в остаток и в ведомость.
    """
    client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "no_show"},
        headers=HEADERS,
    )
    sql.execute(
        """UPDATE tenant
           SET default_rules = jsonb_set(default_rules, '{no_show_burns}', 'false')
           WHERE id = %s""",
        (TENANT,),
    )
    sql.execute(
        """UPDATE subscription
           SET rules = jsonb_set(rules, '{no_show_burns}', 'false')
           WHERE id = %s""",
        (subscription("sagyndyk"),),
    )
    sql.commit()

    applied = _person(client, lesson("les02"), "sagyndyk")["applied_effect"]
    assert applied["lessons_delta"] == -1, "применённый эффект пересчитали правилами"
    assert applied["lessons_after"] == 4


def test_marked_participant_without_subscription_has_no_balance(client):
    """У ученика без абонемента остатка не существует — и null честнее нуля."""
    client.post(
        f"/api/v1/lessons/{lesson('les10')}/attendance",
        json={"student_id": student("zhanat"), "mark": "came"},
        headers=HEADERS,
    )
    applied = _person(client, lesson("les10"), "zhanat")["applied_effect"]
    assert applied["lessons_delta"] == 0
    assert applied["lessons_after"] is None
    assert applied["makeups_after"] is None
    assert applied["teacher_amount"] == 4500


def test_revoked_mark_leaves_no_applied_effect(client):
    response = client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS,
    )
    attendance_id = response.json()["attendance_id"]
    client.delete(f"/api/v1/attendance/{attendance_id}", headers=HEADERS)

    person = _person(client, lesson("les02"), "sagyndyk")
    assert person["applied_effect"] is None
    assert person["subscription"]["lessons_balance"] == 5
    assert person["mark_effects"]["came"]["lessons_after"] == 4
