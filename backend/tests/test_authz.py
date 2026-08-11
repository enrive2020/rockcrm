"""Роли: каждый видит своё и не видит чужого (spec.md §2).

Два самых дорогих места в этом файле:

* преподаватель не может отметить чужое занятие — чужая отметка двигает
  чужой абонемент и чужую зарплату, и объяснить родителю списанное занятие,
  которого не было, потом нечем;
* родитель не видит чужого ребёнка — это прямая утечка персональных данных
  и единственная причина, по которой кабинет родителя нельзя было открывать
  до этой задачи.
"""
from __future__ import annotations

import pytest
from conftest import (
    BRANCH_AB,
    BRANCH_AF,
    DAY,
    HEADERS,
    HEADERS_ADMIN,
    HEADERS_BRANCH_ADMIN,
    HEADERS_GUARDIAN,
    HEADERS_STUDENT,
    HEADERS_TEACHER,
    HEADERS_TEACHER2,
    lesson,
    student,
    subscription,
)

from scripts import seed_demo

# Занятия демо-дня: у Шарапова барабаны, у Федько гитара.
SHARAPOV_LESSON = "les02"       # 11:00, Амина Сагындык
FEDKO_LESSON = "les01"          # 10:00, Тимур Ахметов (уже отмечен «пришёл»)
ENSEMBLE_LESSON = "les09"       # 17:30, ансамбль Меренкова

AMINA = "sagyndyk"
AHMETOV = "ahmetov"


def mark(client, headers, lesson_key, student_key, value="came"):
    return client.post(
        f"/api/v1/lessons/{lesson(lesson_key)}/attendance",
        json={"student_id": student(student_key), "mark": value},
        headers=headers,
    )


def card(client, headers, lesson_key):
    return client.get(f"/api/v1/lessons/{lesson(lesson_key)}", headers=headers)


def student_card(client, headers, student_key):
    return client.get(f"/api/v1/students/{student(student_key)}", headers=headers)


# ---------------------------------------------------------------------------
# Преподаватель
# ---------------------------------------------------------------------------


def test_teacher_marks_his_own_lesson(client):
    """Своё занятие преподаватель отмечает — это его основная работа."""
    response = mark(client, HEADERS_TEACHER, SHARAPOV_LESSON, AMINA)
    assert response.status_code == 201, response.text


def test_teacher_cannot_mark_someone_elses_lesson(client, sql):
    """САМОЕ ВАЖНОЕ. Барабанщик не отмечает урок гитариста.

    Проверяется не только код ответа, но и то, что в базе ничего не осталось:
    403 при записанной отметке был бы хуже, чем её отсутствие.
    """
    response = mark(client, HEADERS_TEACHER, FEDKO_LESSON, AHMETOV, "no_show")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_your_lesson"

    marks = sql.execute(
        "SELECT mark FROM attendance WHERE lesson_id = %s AND revoked_at IS NULL",
        (lesson(FEDKO_LESSON),),
    ).fetchall()
    # Отметка на этом занятии одна и та, что была посеяна.
    assert [row["mark"] for row in marks] == ["came"]


def test_teacher_cannot_revoke_someone_elses_attendance(client):
    """Отмена — та же запись в чужой урок, только с другой стороны."""
    card_body = card(client, HEADERS, FEDKO_LESSON).json()
    attendance_id = card_body["participants"][0]["attendance_id"]

    response = client.delete(
        f"/api/v1/attendance/{attendance_id}", headers=HEADERS_TEACHER
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_your_lesson"

    # А своей отметкой тот же преподаватель распоряжается свободно.
    mine = mark(client, HEADERS_TEACHER, SHARAPOV_LESSON, AMINA).json()
    assert client.delete(
        f"/api/v1/attendance/{mine['attendance_id']}", headers=HEADERS_TEACHER
    ).status_code == 200


def test_teacher_sees_only_his_own_schedule(client):
    """Дорожки чужих преподавателей в расписании не появляются."""
    day = client.get(
        "/api/v1/schedule",
        params={"branch_id": BRANCH_AF, "date": DAY},
        headers=HEADERS_TEACHER,
    ).json()
    names = {track["teacher"]["name"] for track in day["tracks"]}
    assert names == {"Дмитрий Шарапов"}

    all_names = {
        track["teacher"]["name"]
        for track in client.get(
            "/api/v1/schedule",
            params={"branch_id": BRANCH_AF, "date": DAY},
            headers=HEADERS,
        ).json()["tracks"]
    }
    assert len(all_names) > 1


def test_teacher_does_not_open_someone_elses_lesson(client):
    """Чужое занятие — 404, а не 403: «есть, но не ваше» уже подсказка."""
    assert card(client, HEADERS_TEACHER, FEDKO_LESSON).status_code == 404
    assert card(client, HEADERS_TEACHER, SHARAPOV_LESSON).status_code == 200


def test_teacher_sees_his_students_and_not_the_others(client):
    """«Свои ученики» — те, кого он ведёт, а не вся школа."""
    found = client.get("/api/v1/students", headers=HEADERS_TEACHER).json()
    ids = {row["id"] for row in found}
    assert student(AMINA) in ids
    assert student(AHMETOV) not in ids

    assert student_card(client, HEADERS_TEACHER, AMINA).status_code == 200
    assert student_card(client, HEADERS_TEACHER, AHMETOV).status_code == 404
    # А гитарист — наоборот.
    assert student_card(client, HEADERS_TEACHER2, AHMETOV).status_code == 200


def test_teacher_does_not_sell_or_run_the_funnel(client):
    """§2: преподаватель отмечает и пишет заметки. Деньги — не его работа."""
    sale = client.post(
        f"/api/v1/students/{student(AMINA)}/subscriptions",
        json={"plan_id": seed_demo.plan_id("drums8"), "starts_on": "2026-09-01"},
        headers=HEADERS_TEACHER,
    )
    assert sale.status_code == 403
    assert client.get("/api/v1/leads", headers=HEADERS_TEACHER).status_code == 403
    assert client.get("/api/v1/reports/debts", headers=HEADERS_TEACHER).status_code == 403


# ---------------------------------------------------------------------------
# Родитель и взрослый ученик
# ---------------------------------------------------------------------------


def test_guardian_sees_only_her_children(client):
    """САМОЕ ВАЖНОЕ. Чужой ребёнок для родителя не существует."""
    found = client.get("/api/v1/students", headers=HEADERS_GUARDIAN).json()
    assert {row["name"] for row in found} == {"Амина Сагындык", "Тимур Сагындык"}

    assert student_card(client, HEADERS_GUARDIAN, AMINA).status_code == 200
    assert student_card(client, HEADERS_GUARDIAN, AHMETOV).status_code == 404


def test_guardian_search_cannot_be_widened_by_the_query(client):
    """Поиск по чужому имени и чужому телефону не находит ничего.

    Фильтр стоит после выборки, и именно это надо проверить: подобрать
    запрос, обходящий фильтр, не должно получаться.
    """
    for query in ("Ахметов", "Ким", "555", "+7", ""):
        found = client.get(
            "/api/v1/students", params={"query": query}, headers=HEADERS_GUARDIAN
        ).json()
        assert all(row["name"].endswith("Сагындык") for row in found), query


def test_guardian_sees_the_lesson_of_her_child_only(client):
    assert card(client, HEADERS_GUARDIAN, SHARAPOV_LESSON).status_code == 200
    assert card(client, HEADERS_GUARDIAN, FEDKO_LESSON).status_code == 404


def test_guardian_does_not_see_other_children_in_a_group_lesson(client):
    """Ансамбль — четверо чужих детей в одном ответе. Ровно то место,
    где утечка происходит сама собой, если не смотреть."""
    # Тимур Сагындык в ансамбле не состоит, поэтому родителю занятия не видно.
    assert card(client, HEADERS_GUARDIAN, ENSEMBLE_LESSON).status_code == 404

    whole = card(client, HEADERS, ENSEMBLE_LESSON).json()
    assert len(whole["participants"]) == 4


def test_adult_student_sees_himself_only(client):
    """Взрослый ученик платит за себя сам и видит себя (§2)."""
    found = client.get("/api/v1/students", headers=HEADERS_STUDENT).json()
    assert {row["name"] for row in found} == {"Дмитрий Со"}
    assert student_card(client, HEADERS_STUDENT, AMINA).status_code == 404
    assert student_card(client, HEADERS_STUDENT, "so").status_code == 200


def test_guardian_cannot_mark_or_sell(client):
    """Родитель смотрит и платит, а не ведёт учёт (§2)."""
    assert mark(client, HEADERS_GUARDIAN, SHARAPOV_LESSON, AMINA).status_code == 403
    freeze = client.post(
        f"/api/v1/subscriptions/{subscription(AMINA)}/holds",
        json={"from": "2026-08-20", "to": "2026-08-25"},
        headers=HEADERS_GUARDIAN,
    )
    assert freeze.status_code == 403


def test_guardian_does_not_get_the_school_screens(client):
    """Расписание филиала, справочники и воронка — экраны школы, не кабинета."""
    for path, params in (
        ("/api/v1/schedule", {"branch_id": BRANCH_AF, "date": DAY}),
        ("/api/v1/branches", None),
        ("/api/v1/teachers", None),
        ("/api/v1/plans", None),
        ("/api/v1/leads", None),
        ("/api/v1/reports/summary", None),
    ):
        response = client.get(path, params=params, headers=HEADERS_GUARDIAN)
        assert response.status_code == 403, path


# ---------------------------------------------------------------------------
# Филиалы
# ---------------------------------------------------------------------------


def test_branch_admin_does_not_touch_another_branch(client, sql):
    """§2 дословно: администратор филиала Абая не правит Аль-Фараби."""
    theirs = mark(client, HEADERS_BRANCH_ADMIN, SHARAPOV_LESSON, AMINA)  # Аль-Фараби
    assert theirs.status_code == 403
    assert theirs.json()["error"]["code"] == "other_branch"

    mine = mark(client, HEADERS_BRANCH_ADMIN, "les13", "toktar")         # Абая
    assert mine.status_code == 201, mine.text


def test_branch_admin_cannot_book_a_trial_in_another_branch(client):
    """Пробный — это запись в расписание, и филиал у неё чужой."""
    room_af = seed_demo.ROOMS["drum_a"]
    response = client.post(
        f"/api/v1/leads/{seed_demo.lead_id('sanzhar')}/trial",
        json={
            "teacher_id": seed_demo.teacher_id("sharapov"),
            "room_id": room_af,
            "starts_at": "2026-08-13T13:00:00+05:00",
            "duration_min": 45,
        },
        headers=HEADERS_BRANCH_ADMIN,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "other_branch"


def test_admin_without_branches_is_not_limited(client):
    """Школа, не заполнявшая staff_branch, ограничения не получает.

    Пустой staff_branch означает «связь не заведена», а не «доступа никуда
    нет»: иначе первое же обновление выключило бы вход администраторам
    у всех, кто эту таблицу не трогал.
    """
    assert mark(client, HEADERS_ADMIN, SHARAPOV_LESSON, AMINA).status_code == 201
    assert mark(client, HEADERS_ADMIN, "les13", "toktar").status_code == 201


# ---------------------------------------------------------------------------
# Ставки и ведомость
# ---------------------------------------------------------------------------

SHARAPOV = seed_demo.teacher_id("sharapov")
AUGUST = {"from": "2026-08-01", "to": "2026-08-31"}


def test_payroll_is_for_the_owner_only(client):
    """§2: администратор видит всё, КРОМЕ ставок ЗП других сотрудников."""
    assert client.get("/api/v1/payroll", params=AUGUST, headers=HEADERS).status_code == 200
    for headers in (HEADERS_ADMIN, HEADERS_TEACHER, HEADERS_GUARDIAN):
        assert client.get(
            "/api/v1/payroll", params=AUGUST, headers=headers
        ).status_code == 403


def test_teacher_sees_his_own_sheet_and_not_the_neighbours(client):
    """«Своя ЗП» преподавателю по §2 положена — и только своя."""
    mine = client.get(
        f"/api/v1/payroll/teachers/{SHARAPOV}", params=AUGUST, headers=HEADERS_TEACHER
    )
    assert mine.status_code == 200
    assert mine.json()["totals"]["total"] >= 0

    theirs = client.get(
        f"/api/v1/payroll/teachers/{seed_demo.teacher_id('fedko')}",
        params=AUGUST,
        headers=HEADERS_TEACHER,
    )
    assert theirs.status_code == 403


def test_admin_does_not_see_a_teachers_rate_on_the_lesson_card(client):
    """Ставка в карточке занятия — та же чужая зарплата, только россыпью."""
    assert card(client, HEADERS, SHARAPOV_LESSON).json()["teacher"]["rate"] == 4500
    assert card(client, HEADERS_ADMIN, SHARAPOV_LESSON).json()["teacher"]["rate"] is None
    # Свою ставку преподаватель видит.
    assert card(client, HEADERS_TEACHER, SHARAPOV_LESSON).json()["teacher"]["rate"] == 4500


def test_admin_summary_hides_the_payroll_total(client):
    """Фонд оплаты труда — те же деньги людей, просто одной строкой."""
    owner = client.get("/api/v1/reports/summary", params=AUGUST, headers=HEADERS).json()
    admin = client.get(
        "/api/v1/reports/summary", params=AUGUST, headers=HEADERS_ADMIN
    ).json()
    assert owner["payroll"]["total"] > 0
    assert admin["payroll"]["total"] is None
    # Всё остальное администратору видно: касса школы — его работа.
    assert admin["revenue"] == owner["revenue"]


def test_period_is_closed_by_the_owner(client):
    """Закрытие периода — подпись под тем, что деньги отданы. Обратно не открыть."""
    body = {"from": "2026-08-01", "to": "2026-08-10"}
    assert client.post(
        "/api/v1/payroll/periods", json=body, headers=HEADERS_ADMIN
    ).status_code == 403
    assert client.post(
        "/api/v1/payroll/periods", json=body, headers=HEADERS
    ).status_code == 201


# ---------------------------------------------------------------------------
# Изоляция школ поверх ролей
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [HEADERS_TEACHER, HEADERS_GUARDIAN, HEADERS_BRANCH_ADMIN, HEADERS_ADMIN],
    ids=["teacher", "guardian", "branch_admin", "admin"],
)
def test_nobody_reaches_the_neighbouring_school(client, headers):
    """Роль решает, что видно внутри школы. Границу школы держит RLS."""
    from conftest import OTHER_LESSON

    assert client.get(
        f"/api/v1/lessons/{OTHER_LESSON}", headers=headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/students/{seed_demo.OTHER_STUDENT}", headers=headers
    ).status_code == 404


def test_me_answers_who_and_what(client):
    """Интерфейс рисует разные экраны разным ролям и спрашивает об этом сервер:
    роль, вычисленная на клиенте, — это роль, которую клиент себе назначил."""
    teacher = client.get("/api/v1/auth/me", headers=HEADERS_TEACHER).json()
    assert teacher["role"] == "teacher"
    assert teacher["staff_id"] == SHARAPOV
    assert set(teacher["branch_ids"]) == {BRANCH_AF, BRANCH_AB}

    branch_admin = client.get("/api/v1/auth/me", headers=HEADERS_BRANCH_ADMIN).json()
    assert branch_admin["branch_ids"] == [BRANCH_AB]

    guardian = client.get("/api/v1/auth/me", headers=HEADERS_GUARDIAN).json()
    assert guardian["role"] == "guardian"
    assert sorted(guardian["student_ids"]) == sorted(
        [student(AMINA), student("sagyndyk_t")]
    )
