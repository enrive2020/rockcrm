"""Кабинет родителя: ресурсы `/me/*` и заявки в школу (issue #5).

Главная проверка этого файла не «форма ответа совпала с контрактом», а
«наружу не уехало лишнее». Кабинет собирается сложением, и проверять его
надо тем же способом, каким он ломается: не перечислением полей, которые
должны быть, а обходом всего, что реально приехало.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterator

import pytest

from conftest import (
    HEADERS,
    HEADERS_ADMIN,
    HEADERS_GUARDIAN,
    HEADERS_OTHER,
    HEADERS_STUDENT,
    HEADERS_TEACHER,
    student,
)
from scripts import seed_demo

AMINA = student("sagyndyk")
TIMUR = student("sagyndyk_t")        # брат Амины, второй ребёнок в семье
AHMETOV = student("ahmetov")         # чужой ребёнок
SO = student("so")                   # взрослый ученик, платит за себя сам

# Занятия Амины из демо-данных.
LESSON_TODAY = seed_demo.lesson_id("les02")   # 12 августа, 11:00
LESSON_FUTURE = seed_demo._id("4b0")          # 14 августа — заведомо дальше суток
LESSON_HELD = seed_demo._id("4a3")            # 7 августа, отмечено «пришёл»
LESSON_CANCELLED = seed_demo._id("4a0")       # 2 августа, отменено заранее
LESSON_FOREIGN = seed_demo.lesson_id("les01")  # Тимур Ахметов, чужой ребёнок

ME_CHILDREN = "/api/v1/me/children"
ME_SCHEDULE = "/api/v1/me/schedule"


def get(client, path: str, headers=None, **params) -> Any:
    response = client.get(path, params=params or None, headers=headers or HEADERS_GUARDIAN)
    assert response.status_code == 200, response.text
    return response.json()


def child_of(children: list[dict[str, Any]], student_id: str) -> dict[str, Any]:
    for row in children:
        if row["student_id"] == student_id:
            return row
    raise AssertionError(f"ребёнка {student_id} нет в ответе кабинета")


def walk(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Все пары «путь → значение» в ответе, включая ключи вложенных объектов.

    Проверять состав ответа перечислением полей нельзя: перечисление устареет
    при первом же добавлении, причём молча и именно в ту сторону, в которую
    ломаться нельзя. Обход видит всё, что реально приехало, — на любой глубине
    и в любом списке.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            yield here, value
            yield from walk(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}[{i}]")


def every_me_response(client) -> dict[str, Any]:
    """Все ответы кабинета одним словарём — материал для обхода."""
    return {
        "children": get(client, ME_CHILDREN),
        "schedule": get(client, ME_SCHEDULE, **{"from": "2026-08-01", "to": "2026-08-31"}),
        "child": get(client, f"{ME_CHILDREN}/{AMINA}"),
        "child2": get(client, f"{ME_CHILDREN}/{TIMUR}"),
        "reschedule": ask_reschedule(client, LESSON_FUTURE).json(),
        "renew": client.post(
            f"{ME_CHILDREN}/{TIMUR}/renew-request",
            json={"comment": "хотим 2 раза в неделю"},
            headers=HEADERS_GUARDIAN,
        ).json(),
    }


def ask_reschedule(client, lesson_id: str, headers=None, **body):
    return client.post(
        f"/api/v1/me/lessons/{lesson_id}/reschedule-request",
        json=body,
        headers=headers or HEADERS_GUARDIAN,
    )


@pytest.fixture
def lesson_in_two_hours(sql) -> str:
    """Занятие Амины через два часа — меньше порога отмены (24 ч).

    Заводится тестом, а не посевом, потому что «меньше суток до занятия» —
    это про часы от текущего момента, и демо-занятие с фиксированной датой
    отвечало бы на этот вопрос по-разному в зависимости от того, когда
    прогнали тесты.
    """
    row = sql.execute(
        """
        INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, discipline_id,
                            student_id, kind, starts_at, ends_at, status, overbook_ack)
        VALUES (%s, %s, %s, %s, %s, %s, 'regular',
                now() + interval '2 hours', now() + interval '2 hours 55 minutes',
                'planned', true)
        RETURNING id
        """,
        (
            seed_demo.TENANT,
            seed_demo.BRANCH_AF,
            seed_demo.teacher_id("sharapov"),
            seed_demo.ROOMS["drum_a"],
            seed_demo.DISC["drums"],
            AMINA,
        ),
    ).fetchone()
    sql.commit()
    return str(row["id"])


# ---------------------------------------------------------------------------
# GET /me/children
# ---------------------------------------------------------------------------


def test_children_are_own_kids_with_balance_and_next_lesson(client):
    children = get(client, ME_CHILDREN)
    assert {row["full_name"] for row in children} == {"Амина Сагындык", "Тимур Сагындык"}

    amina = child_of(children, AMINA)
    assert amina["name"] == "Амина"           # как зовут дома
    assert amina["age"] == 9
    assert amina["discipline"] == "Барабаны"
    assert amina["teacher"] == {"name": "Дмитрий Шарапов"}
    assert amina["branch"]["name"] == "Аль-Фараби 53В"
    assert amina["subscription"]["lessons_balance"] == 5
    assert amina["subscription"]["lessons_total"] == 8
    assert amina["subscription"]["makeups_balance"] == 1
    # Ближайшее занятие — главный вопрос кабинета, и он закрыт первым запросом.
    assert amina["next_lesson"]["lesson_id"] == LESSON_TODAY
    assert amina["next_lesson"]["starts_at"] == "2026-08-12T11:00:00+05:00"
    assert amina["next_lesson"]["room"] == "Барабанная A"


def test_ends_soon_is_computed_by_the_server(client, sql):
    """Порог «мало» — правило школы, а не число в интерфейсе."""
    assert child_of(get(client, ME_CHILDREN), AMINA)["subscription"]["ends_soon"] is False

    sql.execute(
        """INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta,
                                           reason, created_by)
           VALUES (%s, %s, 'adjust', -3, 'тест', NULL)""",
        (seed_demo.TENANT, seed_demo.subscription_id("sagyndyk")),
    )
    sql.commit()

    sub = child_of(get(client, ME_CHILDREN), AMINA)["subscription"]
    assert sub["lessons_balance"] == 2
    assert sub["ends_soon"] is True


def test_no_subscription_is_null_and_not_an_error(client, sql):
    """`null` — не ошибка, а повод показать «нужно продление»."""
    sql.execute(
        "UPDATE subscription SET status = 'cancelled' WHERE student_id = %s", (TIMUR,)
    )
    sql.commit()

    timur = child_of(get(client, ME_CHILDREN), TIMUR)
    assert timur["subscription"] is None


# ---------------------------------------------------------------------------
# GET /me/schedule
# ---------------------------------------------------------------------------


def test_schedule_holds_all_children_at_once(client):
    """Родителю нужно знать, когда вести кого, а не листать детей по одному."""
    schedule = get(client, ME_SCHEDULE, **{"from": "2026-08-10", "to": "2026-08-16"})
    assert schedule["period"] == {"from": "2026-08-10", "to": "2026-08-16"}

    lesson = next(row for row in schedule["lessons"] if row["lesson_id"] == LESSON_TODAY)
    assert lesson["student_name"] == "Амина Сагындык"
    assert lesson["starts_at"] == "2026-08-12T11:00:00+05:00"
    assert lesson["ends_at"] == "2026-08-12T11:55:00+05:00"
    assert lesson["duration_min"] == 55
    assert lesson["teacher"] == "Дмитрий Шарапов"
    assert lesson["branch"] == "Аль-Фараби 53В"
    assert lesson["room"] == "Барабанная A"
    assert lesson["kind"] == "regular"
    assert lesson["status"] == "planned"
    assert lesson["attendance"] is None

    # Ни одного чужого ребёнка: расписание филиала на 12 августа — семнадцать
    # занятий, и родителю из них видны только свои.
    assert {row["student_id"] for row in schedule["lessons"]} <= {AMINA, TIMUR}


def test_schedule_defaults_to_a_week_ahead(client):
    schedule = get(client, ME_SCHEDULE)
    since = dt.date.fromisoformat(schedule["period"]["from"])
    until = dt.date.fromisoformat(schedule["period"]["to"])
    assert (until - since).days == 6      # семь дней включительно


def test_schedule_shows_cancelled_lessons(client):
    """Родитель должен видеть, что урок отменён, а не находить пустоту."""
    schedule = get(client, ME_SCHEDULE, **{"from": "2026-08-01", "to": "2026-08-08"})
    cancelled = next(
        row for row in schedule["lessons"] if row["lesson_id"] == LESSON_CANCELLED
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["can_request_reschedule"] is False


def test_can_request_reschedule_follows_the_school_rules(client, lesson_in_two_hours):
    schedule = get(client, ME_SCHEDULE, **{"from": "2026-08-01", "to": "2026-08-31"})
    flags = {row["lesson_id"]: row["can_request_reschedule"] for row in schedule["lessons"]}

    assert flags[LESSON_FUTURE] is True          # 14 августа, до него больше суток
    assert flags[LESSON_HELD] is False           # уже проведено
    assert flags[lesson_in_two_hours] is False   # меньше порога отмены


def test_pending_request_is_visible_in_the_schedule(client):
    """Без этого кабинет предложил бы подать заявку второй раз и получил бы 409."""
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем к бабушке")
    assert created.status_code == 201

    schedule = get(client, ME_SCHEDULE, **{"from": "2026-08-01", "to": "2026-08-31"})
    lesson = next(row for row in schedule["lessons"] if row["lesson_id"] == LESSON_FUTURE)
    assert lesson["reschedule_request"] == {
        "request_id": created.json()["request_id"],
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# GET /me/children/{id}
# ---------------------------------------------------------------------------


def test_child_card_carries_history_notes_and_repertoire(client):
    card = get(client, f"{ME_CHILDREN}/{AMINA}")
    assert card["name"] == "Амина Сагындык"
    assert card["age"] == 9
    assert card["teacher"] == "Дмитрий Шарапов"
    assert card["started_on"] == "2026-02-04"
    assert card["makeups"] == [{"expires_on": "2026-09-01", "days_left": 21}]

    # История — движения абонемента вместе с занятием: родитель видит,
    # ЗА ЧТО списано, а не только сколько осталось.
    days = [row["date"] for row in card["history"]]
    assert days == ["2026-08-07", "2026-08-05", "2026-08-04", "2026-08-02", "2026-08-01"]

    lesson = card["history"][0]
    assert lesson["attendance"] == "came"
    assert lesson["lessons_delta"] == -1
    assert lesson["note"]["homework"].startswith("Метроном 80 bpm")
    assert "Nirvana — Smells Like Teen Spirit" in lesson["note"]["tags"]

    purchase = card["history"][-1]
    assert purchase["lessons_delta"] == 8
    assert purchase["starts_at"] is None          # покупка ни к какому уроку не привязана

    assert card["progress"]["lessons_attended"] == 2
    assert card["progress"]["months"] == 6
    assert "Nirvana — Smells Like Teen Spirit" in card["progress"]["repertoire"]


def test_internal_note_never_reaches_the_family(client, sql):
    """САМОЕ ВАЖНОЕ. Внутренняя пометка преподавателя наружу не уходит.

    Проверяется не «поле note пустое у нужной строки», а отсутствие самого
    текста в ЛЮБОМ ответе кабинета: заметка могла бы просочиться и заголовком
    строки журнала, и тегом в репертуаре, и текстом в расписании.
    """
    internal = sql.execute(
        "SELECT body, tags FROM lesson_note WHERE NOT visible_to_family AND student_id = %s",
        (AMINA,),
    ).fetchone()
    assert internal is not None, "в демо-данных нет внутренней заметки — проверять нечего"

    haystack = repr(every_me_response(client))
    assert internal["body"] not in haystack
    for tag in internal["tags"]:
        assert tag not in haystack, tag

    # И для полноты: у занятия, к которому она привязана, заметки нет вовсе.
    card = get(client, f"{ME_CHILDREN}/{AMINA}")
    no_show = next(row for row in card["history"] if row["attendance"] == "no_show")
    assert no_show["note"] is None


# ---------------------------------------------------------------------------
# Чего в кабинете быть не должно
# ---------------------------------------------------------------------------

# Что нельзя показывать родителю: долг семьи, риск оттока, ставка
# преподавателя, цены и скидки. Список — по подстроке в ИМЕНИ поля, на любой
# глубине: поле `churn_risk` в корне и `family.churn_risk.level` внутри
# третьего вложения одинаково недопустимы, а перечислять их поимённо значило
# бы завести список, который устареет при первом же добавлении.
FORBIDDEN_IN_KEYS = (
    "debt", "churn", "risk", "rate", "salary", "payroll", "payer",
    "price", "discount", "amount", "revenue", "paid", "family_id", "internal",
)


def test_no_debt_no_churn_risk_no_teacher_rate_anywhere(client):
    """Обходим весь JSON, а не сверяем известные поля.

    Утечка в кабинете выглядит не как «в ответе появилось поле debt», а как
    «кто-то добавил поле в общий сборщик, и оно доехало сюда». Найти такое
    можно только обходом.
    """
    for name, payload in every_me_response(client).items():
        for path, _ in walk(payload):
            field = path.rsplit(".", 1)[-1].split("[")[0]
            for forbidden in FORBIDDEN_IN_KEYS:
                assert forbidden not in field.lower(), f"{name}: {path}"


def test_me_responses_have_a_closed_set_of_fields(client):
    """Состав ответа перечислен ЦЕЛИКОМ, и добавить в него что-то можно только
    руками, поправив этот тест.

    Это дубль предыдущей проверки с другой стороны: та ловит известное плохое,
    эта — всё неизвестное. Падение здесь не означает ошибку, оно означает
    «в кабинет приехало новое поле — посмотрите, можно ли его родителю».
    """
    allowed = {
        # общее
        "student_id", "name", "full_name", "age", "discipline", "teacher", "branch",
        "address", "started_on", "subscription", "next_lesson", "room",
        # абонемент
        "lessons_balance", "lessons_total", "makeups_balance", "valid_until",
        "status", "ends_soon",
        # расписание
        "period", "from", "to", "lessons", "lesson_id", "student_name", "starts_at",
        "ends_at", "duration_min", "kind", "attendance", "can_request_reschedule",
        "reschedule_request", "request_id",
        # карточка ребёнка
        "makeups", "expires_on", "days_left", "history", "date", "title",
        "lessons_delta", "makeups_delta", "note", "body", "homework", "tags",
        "progress", "lessons_attended", "months", "repertoire",
        # заявки
        "lesson", "message", "student", "reason",
    }
    for name, payload in every_me_response(client).items():
        seen = {path.rsplit(".", 1)[-1].split("[")[0] for path, _ in walk(payload)}
        assert seen <= allowed, f"{name}: новые поля {sorted(seen - allowed)}"


def test_foreign_child_is_404_on_every_me_resource(client):
    """Чужой ребёнок отвечает «не найдено», а не «нельзя»: 403 подтверждал бы,
    что такой ученик в школе есть."""
    assert client.get(f"{ME_CHILDREN}/{AHMETOV}", headers=HEADERS_GUARDIAN).status_code == 404
    assert client.get(f"{ME_CHILDREN}/{SO}", headers=HEADERS_GUARDIAN).status_code == 404
    assert ask_reschedule(client, LESSON_FOREIGN).status_code == 404

    renew = client.post(
        f"{ME_CHILDREN}/{AHMETOV}/renew-request", json={}, headers=HEADERS_GUARDIAN
    )
    assert renew.status_code == 404

    # И чужого ребёнка нет ни в списке, ни в расписании — не «скрыт», а не существует.
    assert AHMETOV not in repr(every_me_response(client))


def test_adult_student_sees_himself(client):
    """Взрослый ученик платит за себя сам и видит про себя то же, что родитель
    видит про ребёнка (§2)."""
    children = get(client, ME_CHILDREN, headers=HEADERS_STUDENT)
    assert [row["full_name"] for row in children] == ["Дмитрий Со"]
    assert children[0]["subscription"]["lessons_balance"] == 0
    assert children[0]["subscription"]["ends_soon"] is True

    card = client.get(f"{ME_CHILDREN}/{SO}", headers=HEADERS_STUDENT)
    assert card.status_code == 200
    assert client.get(f"{ME_CHILDREN}/{AMINA}", headers=HEADERS_STUDENT).status_code == 404


@pytest.mark.parametrize("headers", [HEADERS, HEADERS_ADMIN, HEADERS_TEACHER])
def test_staff_does_not_get_the_cabinet(client, headers):
    """«Мои дети» у сотрудника не определены: видимость учеников у владельца
    и администратора не ограничена вовсе, и кабинет показал бы им всю школу."""
    for path in (ME_CHILDREN, ME_SCHEDULE, f"{ME_CHILDREN}/{AMINA}"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, path
        assert response.json()["error"]["code"] == "family_only"


def test_cabinet_needs_a_session(client):
    assert client.get(ME_CHILDREN).status_code == 401


# ---------------------------------------------------------------------------
# Заявка на перенос
# ---------------------------------------------------------------------------


def test_reschedule_request_is_a_request_not_a_move(client, sql):
    created = ask_reschedule(
        client,
        LESSON_FUTURE,
        reason="уезжаем к бабушке",
        preferred=["2026-08-16T15:00:00+05:00"],
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["lesson"] == {
        "starts_at": "2026-08-14T11:00:00+05:00",
        "student_name": "Амина Сагындык",
    }
    assert body["message"]

    # Занятие осталось на месте: двигает расписание администратор, не родитель.
    lesson = sql.execute(
        "SELECT starts_at, status FROM lesson WHERE id = %s", (LESSON_FUTURE,)
    ).fetchone()
    assert lesson["status"] == "planned"
    assert lesson["starts_at"].astimezone().strftime("%d") == "14"

    row = sql.execute(
        "SELECT * FROM family_request WHERE id = %s", (body["request_id"],)
    ).fetchone()
    assert row["kind"] == "reschedule"
    assert row["reason"] == "уезжаем к бабушке"
    assert len(row["preferred"]) == 1


def test_reschedule_request_creates_a_task_and_a_notification(client, sql):
    """Заявка, которую некому увидеть, ничем не отличается от несделанной."""
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем")
    request_id = created.json()["request_id"]

    task = sql.execute(
        "SELECT kind, title, student_id, done_at FROM task WHERE dedup_key = %s",
        (f"family_request:{request_id}",),
    ).fetchone()
    assert task is not None
    assert str(task["student_id"]) == AMINA
    assert task["done_at"] is None

    note = sql.execute(
        "SELECT template, status, to_address FROM notification WHERE dedup_key = %s",
        (f"family_request_received:{request_id}",),
    ).fetchone()
    assert note["status"] == "queued"
    assert note["to_address"] == "+77015552418"

    audit = sql.execute(
        "SELECT action FROM audit_log WHERE entity_id = %s", (request_id,)
    ).fetchall()
    assert [row["action"] for row in audit] == ["family_request.create"]


def test_reschedule_of_a_held_lesson_is_rejected(client):
    """Проведённое занятие перенести нечем: оно уже списано."""
    response = ask_reschedule(client, LESSON_HELD)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "reschedule_not_allowed"
    assert "проведено" in response.json()["error"]["message"]


def test_reschedule_below_the_notice_threshold_is_rejected(client, lesson_in_two_hours):
    """Порог назван в тексте: отказ без числа заставляет звонить, чтобы его узнать."""
    response = ask_reschedule(client, lesson_in_two_hours)
    assert response.status_code == 422
    assert "24" in response.json()["error"]["message"]
    assert "ресепшен" in response.json()["error"]["message"]


def test_second_request_for_the_same_lesson_is_409(client, sql):
    """Повторная заявка почти всегда двойное нажатие, а не второе намерение."""
    assert ask_reschedule(client, LESSON_FUTURE, reason="раз").status_code == 201

    again = ask_reschedule(client, LESSON_FUTURE, reason="два")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "request_exists"

    count = sql.execute(
        "SELECT count(*) AS n FROM family_request WHERE lesson_id = %s", (LESSON_FUTURE,)
    ).fetchone()
    assert count["n"] == 1


def test_answered_request_does_not_block_a_new_one(client):
    """Запрет держится на ОТКРЫТОЙ заявке: рассмотренная не должна запирать
    занятие навсегда."""
    first = ask_reschedule(client, LESSON_FUTURE, reason="раз").json()
    answer = client.patch(
        f"/api/v1/requests/{first['request_id']}",
        json={"status": "declined", "answer": "В четверг всё занято"},
        headers=HEADERS_ADMIN,
    )
    assert answer.status_code == 200

    assert ask_reschedule(client, LESSON_FUTURE, reason="два").status_code == 201


# ---------------------------------------------------------------------------
# Заявка на продление
# ---------------------------------------------------------------------------


def test_renew_request_creates_a_task(client, sql):
    before = sql.execute("SELECT count(*) AS n FROM payment").fetchone()["n"]
    response = client.post(
        f"{ME_CHILDREN}/{AMINA}/renew-request",
        json={"comment": "хотим 2 раза в неделю с сентября"},
        headers=HEADERS_GUARDIAN,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["student"]["name"] == "Амина Сагындык"

    task = sql.execute(
        "SELECT kind, title FROM task WHERE dedup_key = %s",
        (f"family_request:{body['request_id']}",),
    ).fetchone()
    assert task["kind"] == "renew_subscription"

    # Денег кабинет не берёт: приём платежей через провайдера — отдельная
    # интеграция, а обещать оплату, которой нет, хуже, чем её отсутствие.
    assert sql.execute("SELECT count(*) AS n FROM payment").fetchone()["n"] == before
    assert sql.execute(
        "SELECT count(*) AS n FROM subscription WHERE student_id = %s", (AMINA,)
    ).fetchone()["n"] == 1


def test_second_renew_request_is_409(client):
    body = {"comment": "ещё раз"}
    assert client.post(
        f"{ME_CHILDREN}/{AMINA}/renew-request", json=body, headers=HEADERS_GUARDIAN
    ).status_code == 201
    again = client.post(
        f"{ME_CHILDREN}/{AMINA}/renew-request", json=body, headers=HEADERS_GUARDIAN
    )
    assert again.status_code == 409


# ---------------------------------------------------------------------------
# Очередь администратора
# ---------------------------------------------------------------------------


def test_queue_shows_pending_requests_oldest_first(client):
    reschedule = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    renew = client.post(
        f"{ME_CHILDREN}/{TIMUR}/renew-request", json={"comment": "продлить"},
        headers=HEADERS_GUARDIAN,
    ).json()

    queue = get(client, "/api/v1/requests", headers=HEADERS_ADMIN)
    assert queue["counts"] == {"pending": 2, "reschedule": 1, "renew": 1}
    assert [row["request_id"] for row in queue["requests"]] == [
        reschedule["request_id"],
        renew["request_id"],
    ]

    first = queue["requests"][0]
    assert first["kind"] == "reschedule"
    assert first["student"] == {"student_id": AMINA, "name": "Амина Сагындык"}
    assert first["requested_by"] == {"name": "Гульнара Сагындык", "phone": "+77015552418"}
    assert first["lesson"]["starts_at"] == "2026-08-14T11:00:00+05:00"
    assert first["lesson"]["teacher"] == "Дмитрий Шарапов"
    assert first["answered_by"] is None

    only_renew = get(client, "/api/v1/requests", headers=HEADERS_ADMIN, kind="renew")
    assert [row["kind"] for row in only_renew["requests"]] == ["renew"]


def test_accepting_a_request_closes_the_task_and_answers_the_parent(client, sql):
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    request_id = created["request_id"]

    answered = client.patch(
        f"/api/v1/requests/{request_id}",
        json={"status": "accepted", "answer": "Перенесли на 16 августа, 15:00"},
        headers=HEADERS_ADMIN,
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["status"] == "accepted"
    assert body["answered_by"] == "Асель Нурланова"
    assert body["answered_at"]
    assert body["message"]

    task = sql.execute(
        "SELECT done_at, done_by FROM task WHERE dedup_key = %s",
        (f"family_request:{request_id}",),
    ).fetchone()
    assert task["done_at"] is not None

    note = sql.execute(
        "SELECT payload FROM notification WHERE dedup_key = %s",
        (f"family_request_answered:{request_id}",),
    ).fetchone()
    assert note["payload"]["status"] == "accepted"

    actions = sql.execute(
        "SELECT action FROM audit_log WHERE entity_id = %s ORDER BY id", (request_id,)
    ).fetchall()
    assert [row["action"] for row in actions] == [
        "family_request.create",
        "family_request.answer",
    ]


def test_declining_without_an_answer_is_422(client):
    """Отказ без объяснения хуже отказа: родитель всё равно позвонит,
    только уже раздражённым."""
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    response = client.patch(
        f"/api/v1/requests/{created['request_id']}",
        json={"status": "declined"},
        headers=HEADERS_ADMIN,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "answer_required"


def test_answering_twice_is_409(client):
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    decision = {"status": "accepted", "answer": "ок"}
    assert client.patch(
        f"/api/v1/requests/{created['request_id']}", json=decision, headers=HEADERS_ADMIN
    ).status_code == 200
    again = client.patch(
        f"/api/v1/requests/{created['request_id']}", json=decision, headers=HEADERS_ADMIN
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_answered"


def test_parent_answer_is_visible_in_her_own_cabinet(client):
    """Ответ администратора обязан вернуться туда, откуда пришла заявка."""
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    client.patch(
        f"/api/v1/requests/{created['request_id']}",
        json={"status": "declined", "answer": "В субботу всё занято, предлагаем вторник"},
        headers=HEADERS_ADMIN,
    )
    schedule = get(client, ME_SCHEDULE, **{"from": "2026-08-01", "to": "2026-08-31"})
    lesson = next(row for row in schedule["lessons"] if row["lesson_id"] == LESSON_FUTURE)
    # Рассмотренная заявка больше не держит занятие: попросить можно заново.
    assert lesson["reschedule_request"] is None
    assert lesson["can_request_reschedule"] is True


def test_the_queue_is_a_school_screen(client):
    """Родитель подаёт заявки, но чужие не читает и своих не рассматривает."""
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()
    assert client.get("/api/v1/requests", headers=HEADERS_GUARDIAN).status_code == 403
    assert client.patch(
        f"/api/v1/requests/{created['request_id']}",
        json={"status": "accepted"},
        headers=HEADERS_GUARDIAN,
    ).status_code == 403
    assert client.get("/api/v1/requests", headers=HEADERS_TEACHER).status_code == 403


def test_another_school_sees_no_requests(client):
    created = ask_reschedule(client, LESSON_FUTURE, reason="уезжаем").json()

    queue = get(client, "/api/v1/requests", headers=HEADERS_OTHER)
    assert queue["requests"] == []
    assert queue["counts"]["pending"] == 0

    # Чужая заявка отвечает «не найдено» — так же, как несуществующая.
    answered = client.patch(
        f"/api/v1/requests/{created['request_id']}",
        json={"status": "accepted", "answer": "ок"},
        headers=HEADERS_OTHER,
    )
    assert answered.status_code == 404
