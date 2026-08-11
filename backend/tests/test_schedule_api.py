"""Расписание и карточка занятия: форма ответа, время, изоляция."""
from __future__ import annotations

from conftest import (
    BRANCH_AF,
    DAY,
    HEADERS,
    HEADERS_OTHER,
    OTHER_LESSON,
    get_card,
    lesson,
    student,
)


def schedule(client, headers=None, branch_id=BRANCH_AF, date=DAY):
    return client.get(
        "/api/v1/schedule",
        params={"branch_id": branch_id, "date": date},
        headers=headers or HEADERS,
    )


def test_branches_listed(client):
    response = client.get("/api/v1/branches", headers=HEADERS)
    assert response.status_code == 200
    names = {b["name"] for b in response.json()}
    assert names == {"Аль-Фараби 53В", "Абая 150"}
    assert all(b["timezone"] == "Asia/Almaty" for b in response.json())


def test_schedule_day_shape(client):
    body = schedule(client).json()
    assert body["date"] == DAY
    assert body["branch"]["name"] == "Аль-Фараби 53В"
    assert len(body["tracks"]) == 5          # пять преподавателей с занятиями
    assert body["summary"]["lessons"] == 12
    assert body["summary"]["trials"] == 1
    assert body["summary"]["conflicts"] == 1  # подтверждённый овербукинг в 13:00


def test_lesson_time_is_local_to_branch(client):
    """Время в ответе — местное время филиала, а не UTC и не смещение из кода.

    Ошибка здесь тихая и разрушительная: расписание уезжает на час, и заметно
    это становится только по жалобам родителей.
    """
    body = schedule(client).json()
    times = {
        les["id"]: (les["starts_at"], les["ends_at"])
        for track in body["tracks"]
        for les in track["lessons"]
    }
    # Занятие Амины Сагындык поставлено на 11:00 по Алматы.
    assert times[lesson("les02")] == (
        f"{DAY}T11:00:00+05:00",
        f"{DAY}T11:55:00+05:00",
    )
    # Первое занятие дня — 10:00, последнее начинается в 20:00: ни одно
    # не должно выпасть за границы рабочего дня из-за пересчёта пояса.
    starts = sorted(t[0] for t in times.values())
    assert starts[0].endswith("T10:00:00+05:00")
    assert starts[-1].endswith("T20:00:00+05:00")


def test_conflict_is_visible_on_both_lessons(client):
    body = schedule(client).json()
    conflicts = {
        les["id"]: les["conflicts"] for track in body["tracks"] for les in track["lessons"]
    }
    assert conflicts[lesson("les04")], "занятие в 12:30 обязано видеть конфликт кабинета"
    assert conflicts[lesson("les05")], "пробный в 13:00 обязан видеть конфликт кабинета"
    assert conflicts[lesson("les04")][0]["with_lesson_id"] == lesson("les05")
    assert conflicts[lesson("les04")][0]["kind"] == "room"


def test_cancelled_lessons_are_hidden(client, sql):
    sql.execute("UPDATE lesson SET status = 'cancelled' WHERE id = %s", (lesson("les02"),))
    sql.commit()
    body = schedule(client).json()
    ids = {les["id"] for track in body["tracks"] for les in track["lessons"]}
    assert lesson("les02") not in ids
    assert body["summary"]["lessons"] == 11


def test_trial_by_lead_shows_lead_name(client):
    """У пробного нет ученика, но заголовок обязан быть — имя из заявки."""
    body = schedule(client).json()
    trial = next(
        les
        for track in body["tracks"]
        for les in track["lessons"]
        if les["kind"] == "trial"
    )
    assert trial["title"] == "Алиса Ким"
    assert trial["student_id"] is None

    card = get_card(client, trial["id"])
    assert card["title"] == "Алиса Ким"
    # Отметить такое занятие нельзя: attendance требует ученика, а его ещё нет.
    assert card["participants"] == []


def test_lesson_card_has_all_six_effects_and_subscription(client):
    card = get_card(client, lesson("les02"))
    assert card["teacher"]["name"] == "Дмитрий Шарапов"
    assert card["teacher"]["rate"] == 4500
    person = card["participants"][0]
    assert person["name"] == "Амина Сагындык"
    assert person["subscription"]["lessons_balance"] == 5
    assert person["subscription"]["makeups_balance"] == 1
    assert set(person["mark_effects"]) == {
        "came",
        "late",
        "no_show",
        "cancelled_early",
        "cancelled_late",
        "cancelled_teacher",
    }
    assert person["attendance"] is None
    assert person["attendance_id"] is None


def test_group_lesson_gives_every_participant_own_effects(client):
    card = get_card(client, lesson("les09"))
    assert len(card["participants"]) == 4
    assert all(p["mark_effects"]["came"]["lessons_delta"] == -1 for p in card["participants"])


def test_student_without_subscription_pays_per_lesson(client):
    card = get_card(client, lesson("les10"))   # Амир Жанат, абонемента нет
    person = card["participants"][0]
    assert person["subscription"] is None
    assert person["mark_effects"]["came"]["lessons_delta"] == 0
    assert "разовой оплатой" in person["mark_effects"]["came"]["summary"]


def test_effects_come_from_subscription_rules_not_school_settings(client, sql):
    """Проданный абонемент живёт по правилам момента покупки.

    Меняем настройки школы на противоположные и проверяем, что уже проданный
    абонемент их не заметил.
    """
    before = get_card(client, lesson("les02"))["participants"][0]["mark_effects"]["no_show"]
    assert before["lessons_delta"] == -1

    sql.execute(
        """UPDATE tenant
           SET default_rules = jsonb_set(default_rules, '{no_show_burns}', 'false')
           WHERE id = (SELECT tenant_id FROM branch WHERE id = %s)""",
        (BRANCH_AF,),
    )
    sql.commit()

    after = get_card(client, lesson("les02"))["participants"][0]["mark_effects"]["no_show"]
    assert after["lessons_delta"] == -1, "смена настроек школы пересчитала проданный абонемент"


# ---------------------------------------------------------------------------
# Изоляция и заголовки
# ---------------------------------------------------------------------------


def test_foreign_tenant_gets_404_not_someone_elses_lesson(client):
    """Занятие соседней школы для нас не существует."""
    response = client.get(f"/api/v1/lessons/{lesson('les02')}", headers=HEADERS_OTHER)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    # А своё занятие соседняя школа видит — значит, дело именно в изоляции,
    # а не в том, что эндпоинт сломан целиком.
    assert get_card(client, OTHER_LESSON, headers=HEADERS_OTHER)["id"] == OTHER_LESSON


def test_foreign_tenant_cannot_mark_our_lesson(client):
    response = client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers=HEADERS_OTHER,
    )
    assert response.status_code == 404


def test_foreign_tenant_schedule_is_empty(client):
    """Чужой филиал не находится даже по прямому идентификатору."""
    response = schedule(client, headers=HEADERS_OTHER)
    assert response.status_code == 404


def test_missing_headers_give_401(client):
    assert client.get("/api/v1/branches").status_code == 401
    assert client.get("/api/v1/branches", headers={"X-Tenant-Id": HEADERS["X-Tenant-Id"]}).status_code == 401
    body = client.get("/api/v1/branches").json()
    assert body["error"]["code"] == "no_tenant"


def test_unknown_user_cannot_write(client):
    response = client.post(
        f"/api/v1/lessons/{lesson('les02')}/attendance",
        json={"student_id": student("sagyndyk"), "mark": "came"},
        headers={"X-Tenant-Id": HEADERS["X-Tenant-Id"],
                 "X-User-Id": "0189b0de-0000-7000-8000-0000000000ff"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unknown_user"
