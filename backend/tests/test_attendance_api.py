"""Отметка посещаемости: предпросмотр против применения, конфликты, отмена.

Главный тест файла — test_preview_matches_apply. Если предпросмотр обещает
одно, а применение делает другое, администратор показывает родителю неверные
цифры и узнаёт об этом от родителя, а не от системы.
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from conftest import (
    HEADERS,
    TENANT,
    get_card,
    has_partial_attendance_index,
    lesson,
    participant,
    student,
    subscription,
)
from scripts import seed_demo

ALL_MARKS = ["came", "late", "no_show", "cancelled_early", "cancelled_late", "cancelled_teacher"]

# Занятие Амины Сагындык: абонемент на 8, остаток 5, одна отработка.
LESSON = "les02"
STUDENT = "sagyndyk"


def mark(client, lesson_key, student_key, value, headers=None):
    return client.post(
        f"/api/v1/lessons/{lesson(lesson_key)}/attendance",
        json={"student_id": student(student_key), "mark": value},
        headers=headers or HEADERS,
    )


@pytest.mark.parametrize("value", ALL_MARKS)
def test_preview_matches_apply(client, sql, value):
    """Предпросмотр и применение считаются одной функцией — проверяем сходимость.

    Сравниваем не «примерно то же», а все поля последствий, и отдельно —
    остаток, который реально оказался в базе после транзакции.
    """
    preview = participant(get_card(client, lesson(LESSON)), student(STUDENT))
    expected = preview["mark_effects"][value]

    response = mark(client, LESSON, STUDENT, value)
    assert response.status_code == 201, response.text
    applied = response.json()["applied"]

    for field in ("lessons_delta", "makeups_delta", "teacher_amount", "lessons_after", "makeups_after"):
        assert applied[field] == expected[field], (
            f"отметка «{value}»: предпросмотр обещал {field}={expected[field]}, "
            f"применение дало {applied[field]}"
        )

    row = sql.execute(
        "SELECT lessons_balance, makeups_balance FROM subscription WHERE id = %s",
        (subscription(STUDENT),),
    ).fetchone()
    assert row["lessons_balance"] == expected["lessons_after"]
    assert row["makeups_balance"] == expected["makeups_after"]


def test_apply_writes_all_four_records_in_one_transaction(client, sql):
    response = mark(client, LESSON, STUDENT, "came")
    attendance_id = response.json()["attendance_id"]

    entries = sql.execute(
        "SELECT kind, lessons_delta FROM subscription_entry WHERE attendance_id = %s",
        (attendance_id,),
    ).fetchall()
    assert [(e["kind"], e["lessons_delta"]) for e in entries] == [("charge", -1)]

    payroll = sql.execute(
        "SELECT amount, kind FROM payroll_entry WHERE attendance_id = %s", (attendance_id,)
    ).fetchone()
    assert int(payroll["amount"]) == 4500 and payroll["kind"] == "lesson"

    audit = sql.execute(
        "SELECT action, payload FROM audit_log WHERE entity_id = %s", (attendance_id,)
    ).fetchone()
    assert audit["action"] == "attendance.mark"
    assert audit["payload"]["teacher_amount"] == 4500

    assert response.json()["lesson_status"] == "held"
    status = sql.execute("SELECT status FROM lesson WHERE id = %s", (lesson(LESSON),)).fetchone()
    assert status["status"] == "held"


def balance(sql, student_key: str) -> int:
    row = sql.execute(
        "SELECT lessons_balance FROM subscription WHERE id = %s",
        (subscription(student_key),),
    ).fetchone()
    return int(row["lessons_balance"])


def charges(sql, student_key: str) -> int:
    row = sql.execute(
        "SELECT count(*) AS n FROM subscription_entry WHERE subscription_id = %s AND kind = 'charge'",
        (subscription(student_key),),
    ).fetchone()
    return int(row["n"])


def test_second_mark_of_same_student_is_rejected(client, sql):
    # Считаем списания до и после, а не сравниваем с числом из демо-данных:
    # тест обязан проверять «второго списания не появилось», а не количество
    # строк, которое меняется при каждом пополнении демо-данных.
    before = charges(sql, STUDENT)
    assert mark(client, LESSON, STUDENT, "came").status_code == 201
    assert charges(sql, STUDENT) == before + 1

    again = mark(client, LESSON, STUDENT, "no_show")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_marked"

    # Отказ обязан быть полным: второго списания в журнале быть не должно.
    assert charges(sql, STUDENT) == before + 1


def test_zero_balance_gives_422_and_changes_nothing(client, sql):
    """Дмитрий Со выбрал абонемент до нуля: списывать нечего."""
    card = get_card(client, lesson("les12"))
    person = card["participants"][0]
    assert person["subscription"]["lessons_balance"] == 0
    # Предпросмотр обязан предупредить до нажатия, а не после.
    assert person["mark_effects"]["came"]["blocked_reason"] is not None

    response = mark(client, "les12", "so", "came")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_lessons_left"

    balance = sql.execute(
        "SELECT lessons_balance FROM subscription WHERE id = %s", (subscription("so"),)
    ).fetchone()
    assert balance["lessons_balance"] == 0
    marks = sql.execute(
        "SELECT count(*) AS n FROM attendance WHERE lesson_id = %s", (lesson("les12"),)
    ).fetchone()
    assert marks["n"] == 0, "отметка не должна была записаться"


def test_cancellation_works_even_on_empty_subscription(client):
    """Нулевой остаток запрещает списание, но не отмену: она ничего не списывает."""
    assert mark(client, "les12", "so", "cancelled_teacher").status_code == 201


def test_low_balance_alert(client):
    """Остаток 2 и ниже — повод продать продление, и система об этом говорит."""
    response = mark(client, "les11", "kim_o", "came")   # было 2, станет 1
    alerts = response.json()["alerts"]
    assert alerts and alerts[0]["kind"] == "subscription_low"
    assert "1 занятие" in alerts[0]["message"]


def test_no_alert_while_balance_is_comfortable(client):
    assert mark(client, LESSON, STUDENT, "came").json()["alerts"] == []


def test_unknown_mark_is_400(client):
    response = client.post(
        f"/api/v1/lessons/{lesson(LESSON)}/attendance",
        json={"student_id": student(STUDENT), "mark": "хорошо_позанимались"},
        headers=HEADERS,
    )
    assert response.status_code == 400


def test_student_from_another_lesson_is_404(client):
    response = client.post(
        f"/api/v1/lessons/{lesson(LESSON)}/attendance",
        json={"student_id": student("kim_o"), "mark": "came"},
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_a_participant"


def test_trial_by_lead_cannot_be_marked(client):
    response = client.post(
        f"/api/v1/lessons/{lesson('les05')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "lead_lesson"


# ---------------------------------------------------------------------------
# Отмена отметки
# ---------------------------------------------------------------------------


def test_attendance_id_is_reachable_from_lesson_card(client):
    """Фронтенду нужен идентификатор отметки, иначе отменить её не из чего."""
    mark(client, LESSON, STUDENT, "came")
    person = participant(get_card(client, lesson(LESSON)), student(STUDENT))
    assert person["attendance"] == "came"
    assert person["attendance_id"]

    revoke = client.delete(f"/api/v1/attendance/{person['attendance_id']}", headers=HEADERS)
    assert revoke.status_code == 200


def test_revoke_returns_balance_exactly_back(client, sql):
    before = sql.execute(
        "SELECT lessons_balance, makeups_balance FROM subscription WHERE id = %s",
        (subscription(STUDENT),),
    ).fetchone()

    applied = mark(client, LESSON, STUDENT, "came").json()
    revoke = client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS)
    assert revoke.status_code == 200
    body = revoke.json()

    assert body["reverted"]["lessons_delta"] == 1
    assert body["reverted"]["teacher_amount"] == -4500
    assert body["reverted"]["lessons_after"] == before["lessons_balance"]
    assert body["lesson_status"] == "planned", "занятие без отметок снова запланировано"

    after = sql.execute(
        "SELECT lessons_balance, makeups_balance FROM subscription WHERE id = %s",
        (subscription(STUDENT),),
    ).fetchone()
    assert (after["lessons_balance"], after["makeups_balance"]) == (
        before["lessons_balance"],
        before["makeups_balance"],
    )


def test_revoke_compensates_and_does_not_touch_old_records(client, sql):
    """Журнал не правится и не чистится — гасится встречной записью."""
    applied = mark(client, LESSON, STUDENT, "came").json()
    attendance_id = applied["attendance_id"]

    charge = sql.execute(
        "SELECT id, lessons_delta FROM subscription_entry WHERE attendance_id = %s",
        (attendance_id,),
    ).fetchone()

    client.delete(f"/api/v1/attendance/{attendance_id}", headers=HEADERS)

    still_there = sql.execute(
        "SELECT lessons_delta FROM subscription_entry WHERE id = %s", (charge["id"],)
    ).fetchone()
    assert still_there["lessons_delta"] == -1, "исходное списание переписали"

    refund = sql.execute(
        "SELECT kind, lessons_delta, reverses_id FROM subscription_entry WHERE reverses_id = %s",
        (charge["id"],),
    ).fetchone()
    assert (refund["kind"], refund["lessons_delta"]) == ("refund", 1)

    correction = sql.execute(
        "SELECT kind, amount FROM payroll_entry WHERE kind = 'correction' AND lesson_id = %s",
        (lesson(LESSON),),
    ).fetchone()
    assert int(correction["amount"]) == -4500

    attendance = sql.execute(
        "SELECT revoked_at, revoked_by FROM attendance WHERE id = %s", (attendance_id,)
    ).fetchone()
    assert attendance["revoked_at"] is not None
    assert attendance["revoked_by"] is not None

    audit = sql.execute(
        "SELECT count(*) AS n FROM audit_log WHERE entity_id = %s AND action = 'attendance.revoke'",
        (attendance_id,),
    ).fetchone()
    assert audit["n"] == 1


def test_revoke_takes_makeup_back(client, sql):
    applied = mark(client, LESSON, STUDENT, "cancelled_early").json()
    assert applied["applied"]["makeups_delta"] == 1
    granted = sql.execute(
        "SELECT count(*) AS n FROM makeup_credit WHERE granted_for = %s", (lesson(LESSON),)
    ).fetchone()
    assert granted["n"] == 1

    client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS)

    left = sql.execute(
        "SELECT count(*) AS n FROM makeup_credit WHERE granted_for = %s", (lesson(LESSON),)
    ).fetchone()
    assert left["n"] == 0, "ошибочно выданная отработка осталась доступной"
    balance = sql.execute(
        "SELECT makeups_balance FROM subscription WHERE id = %s", (subscription(STUDENT),)
    ).fetchone()
    assert balance["makeups_balance"] == 1  # столько же, сколько было до отметки


def test_makeup_expires_counting_from_the_lesson_not_from_the_mark(client, sql):
    """«Сгорает через 30 дней» — это 30 дней от занятия, а не от ввода отметки.

    Родитель читает правило по дате пропущенного урока: она есть в договоре
    и он может её проверить. Если считать от current_date, срок отработки
    начинает зависеть от того, когда администратор дошёл до журнала, —
    отметка через неделю после урока даёт лишнюю неделю жизни.
    """
    lesson_id = seed_demo._id("4b1")  # занятие Амины 19 августа, ещё не отмечено
    row = sql.execute(
        """SELECT (l.starts_at AT TIME ZONE 'Asia/Almaty')::date AS day,
                  current_date AS today,
                  (s.rules ->> 'makeup_ttl_days')::int AS ttl
             FROM lesson l, subscription s
            WHERE l.id = %s AND s.id = %s""",
        (lesson_id, subscription(STUDENT)),
    ).fetchone()
    assert row["day"] != row["today"], (
        "тест бессмыслен, если занятие приходится на сегодня: "
        "дата занятия и дата ввода отметки обязаны различаться"
    )

    response = client.post(
        f"/api/v1/lessons/{lesson_id}/attendance",
        json={"student_id": student(STUDENT), "mark": "cancelled_early"},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text

    credit = sql.execute(
        "SELECT expires_on FROM makeup_credit WHERE granted_for = %s", (lesson_id,)
    ).fetchone()
    assert credit["expires_on"] == row["day"] + dt.timedelta(days=row["ttl"])
    assert credit["expires_on"] != row["today"] + dt.timedelta(days=row["ttl"])


@pytest.mark.parametrize("outcome", ["used", "expired"])
def test_revoke_does_not_take_back_a_makeup_that_is_already_gone(client, sql, outcome):
    """Компенсируется ровно то, что удалось отозвать.

    Отработку, потраченную на занятие или сгоревшую по сроку, вернуть нечем:
    из баланса она уже ушла. Компенсирующая запись «−1 отработка» вычла бы её
    второй раз, и родитель увидел бы в журнале минус, которого не было.
    """
    applied = mark(client, LESSON, STUDENT, "cancelled_early").json()
    assert applied["applied"]["makeups_delta"] == 1
    balance_with_makeup = applied["applied"]["makeups_after"]

    # Расхода отработок в приложении ещё нет — воспроизводим его в журнале
    # ровно так, как его сделает будущая операция: запись kind и метка
    # на самой отработке. Дефект живёт именно на этом стыке.
    kind, column = ("makeup_use", "used_at") if outcome == "used" else ("makeup_expire", "expired_at")
    sql.execute(
        f"UPDATE makeup_credit SET {column} = now() WHERE granted_for = %s",
        (lesson(LESSON),),
    )
    sql.execute(
        """INSERT INTO subscription_entry
             (tenant_id, subscription_id, kind, makeups_delta, lesson_id, reason)
           VALUES (%s, %s, %s, -1, %s, 'Отработка израсходована')""",
        (TENANT, subscription(STUDENT), kind, lesson(LESSON)),
    )
    sql.commit()
    spent = sql.execute(
        "SELECT makeups_balance FROM subscription WHERE id = %s", (subscription(STUDENT),)
    ).fetchone()
    assert spent["makeups_balance"] == balance_with_makeup - 1

    response = client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS)
    assert response.status_code == 200, response.text

    after = sql.execute(
        "SELECT makeups_balance FROM subscription WHERE id = %s", (subscription(STUDENT),)
    ).fetchone()
    assert after["makeups_balance"] == spent["makeups_balance"], (
        "отзывать было нечего — баланс отработок обязан остаться прежним"
    )
    assert response.json()["reverted"]["makeups_delta"] == 0
    assert response.json()["reverted"]["makeups_after"] == spent["makeups_balance"]

    # И в журнале нет записи о том, чего не произошло.
    revoked = sql.execute(
        """SELECT count(*) AS n FROM subscription_entry
            WHERE subscription_id = %s AND kind = 'adjust' AND makeups_delta < 0""",
        (subscription(STUDENT),),
    ).fetchone()
    assert revoked["n"] == 0
    # Сама отработка остаётся на месте: она была потрачена или сгорела,
    # и удалять её задним числом значило бы стирать историю.
    left = sql.execute(
        "SELECT count(*) AS n FROM makeup_credit WHERE granted_for = %s", (lesson(LESSON),)
    ).fetchone()
    assert left["n"] == 1


def test_double_revoke_is_409(client):
    applied = mark(client, LESSON, STUDENT, "came").json()
    assert client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS).status_code == 200
    second = client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_revoked"


def test_revoke_of_foreign_tenant_is_404(client):
    from conftest import HEADERS_OTHER

    applied = mark(client, LESSON, STUDENT, "came").json()
    response = client.delete(f"/api/v1/attendance/{applied['attendance_id']}", headers=HEADERS_OTHER)
    assert response.status_code == 404


@pytest.mark.skipif(
    not has_partial_attendance_index(),
    reason="нужна миграция 008: частичный индекс attendance_active_uniq",
)
def test_remark_after_revoke_is_allowed(client, sql):
    """Ошибся, отменил, отметил заново — сценарий обязан замыкаться.

    Пока уникальный индекс (lesson_id, student_id) был сплошным, отменённая
    строка продолжала занимать ключ, и занятие оставалось неотмеченным
    навсегда: единственным выходом было бросить его как есть. Частичный
    индекс `WHERE revoked_at IS NULL` снимает тупик, не трогая принцип —
    отмена по-прежнему компенсация, а не удаление.
    """
    before = balance(sql, STUDENT)

    wrong = mark(client, LESSON, STUDENT, "came").json()
    assert balance(sql, STUDENT) == before - 1
    assert client.delete(
        f"/api/v1/attendance/{wrong['attendance_id']}", headers=HEADERS
    ).status_code == 200
    assert balance(sql, STUDENT) == before

    right = mark(client, LESSON, STUDENT, "late")
    assert right.status_code == 201, right.text
    assert right.json()["attendance_id"] != wrong["attendance_id"]
    # Списание пошло заново и ровно одно: остаток не должен ни застрять
    # на возвращённом, ни уехать на два.
    assert balance(sql, STUDENT) == before - 1

    # Ошибочная отметка остаётся в базе отменённой: отмена — компенсация,
    # а не стирание истории, и обе строки обязаны быть видны.
    rows = sql.execute(
        """SELECT mark, revoked_at FROM attendance
            WHERE lesson_id = %s AND student_id = %s ORDER BY marked_at""",
        (lesson(LESSON), student(STUDENT)),
    ).fetchall()
    assert [(r["mark"], r["revoked_at"] is None) for r in rows] == [
        ("came", False),
        ("late", True),
    ]


def test_second_active_mark_is_still_refused_by_the_index(client, sql):
    """Частичный индекс ослабил ключ ровно на отменённые строки — и только.

    Проверяем не через API (там раньше сработает предварительная проверка),
    а вставкой напрямую: гарантию даёт база, а не порядок операторов
    в приложении, и два администратора в гонке упрутся именно в неё.

    Тест не привязан к миграции 008 намеренно: он обязан выполняться
    и до неё, и после — в этом и смысл, что послабление коснулось только
    отменённых отметок.
    """
    mark(client, LESSON, STUDENT, "came")

    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute(
            """INSERT INTO attendance (tenant_id, lesson_id, student_id, mark)
               VALUES (%s, %s, %s, 'no_show')""",
            (TENANT, lesson(LESSON), student(STUDENT)),
        )
    sql.rollback()
