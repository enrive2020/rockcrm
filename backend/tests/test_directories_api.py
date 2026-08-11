"""Справочники преподавателей, кабинетов и направлений.

Главный тест файла — test_teacher_without_lessons_is_still_offered. До этих
трёх ресурсов интерфейс собирал преподавателей и кабинеты из расписания дня,
и это работало случайно: расписание — срез дня, а не справочник. Совпадали
они ровно до первого выходного, а направление в форме заявки выбрать было
нельзя вовсе, и заявка уходила в воронку без него.
"""
from __future__ import annotations

import pytest

from conftest import HEADERS, HEADERS_OTHER
from scripts import seed_demo

BRANCH_AF = seed_demo.BRANCH_AF
BRANCH_AB = seed_demo.BRANCH_AB
DAY = seed_demo.DAY

DRUMS = seed_demo.DISC["drums"]
GUITAR = seed_demo.DISC["guitar"]
SHARAPOV = seed_demo.teacher_id("sharapov")   # ведёт только барабаны
FEDKO = seed_demo.teacher_id("fedko")         # гитара и укулеле
DRUM_A = seed_demo.ROOMS["drum_a"]            # Аль-Фараби, с установкой
CLASS3 = seed_demo.ROOMS["class3"]            # Абая, без установки


def get(client, path: str, headers=None, **params) -> list[dict]:
    response = client.get(f"/api/v1/{path}", params=params, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def by_id(items: list[dict], item_id: str) -> dict:
    return next(item for item in items if item["id"] == item_id)


def ids(items: list[dict]) -> set[str]:
    return {item["id"] for item in items}


# ---------------------------------------------------------------------------
# Преподаватели
# ---------------------------------------------------------------------------


def test_teacher_without_lessons_is_still_offered(client, sql):
    """Справочник — это все преподаватели школы, а не те, кто работает сегодня.

    Отменяем Шарапову весь день и смотрим на оба ответа: из расписания
    он исчезает (там дорожки только у занятых, и это правильно), а назначить
    ему пробный на завтра по-прежнему нужно уметь. Ровно на этом ломался
    прежний обход через GET /schedule.
    """
    sql.execute(
        """UPDATE lesson SET status = 'cancelled'
            WHERE teacher_id = %s AND starts_at >= %s::date AND starts_at < %s::date + 1""",
        (SHARAPOV, DAY, DAY),
    )
    sql.commit()

    schedule = client.get(
        "/api/v1/schedule", params={"branch_id": BRANCH_AF, "date": DAY}, headers=HEADERS
    ).json()
    tracks = {track["teacher"]["id"] for track in schedule["tracks"]}
    assert SHARAPOV not in tracks, "занятий в этот день нет — дорожки быть не должно"

    assert SHARAPOV in ids(get(client, "teachers"))


def test_teacher_carries_disciplines_and_branches(client):
    """Диалогу назначения пробного нужны и предмет, и филиал — объектами.

    Названием тут не обойтись: по нему нельзя ни отфильтровать список,
    ни подставить discipline_id в запрос, а сверять строки — значит
    сломаться на первом переименовании направления.
    """
    teacher = by_id(get(client, "teachers"), SHARAPOV)

    assert teacher["name"] == "Дмитрий Шарапов"
    assert [d["name"] for d in teacher["disciplines"]] == ["Барабаны"]
    assert [d["id"] for d in teacher["disciplines"]] == [DRUMS]
    assert {b["id"] for b in teacher["branches"]} == {BRANCH_AF, BRANCH_AB}


def test_teachers_filtered_by_discipline(client):
    """«Кто ведёт гитару» — вопрос формы, а не повод фильтровать на клиенте."""
    guitarists = get(client, "teachers", discipline_id=GUITAR)

    assert FEDKO in ids(guitarists)
    assert SHARAPOV not in ids(guitarists), "барабанщик гитару не ведёт"
    # Ни одного задвоения: у Федько два направления, а EXISTS в запросе стоит
    # именно затем, чтобы JOIN не вернул его дважды.
    assert len(ids(guitarists)) == len(guitarists)
    for teacher in guitarists:
        assert GUITAR in {d["id"] for d in teacher["disciplines"]}


def test_teachers_filtered_by_branch(client, sql):
    """Филиал ограничивает список так же, как ограничивает саму работу."""
    sql.execute("DELETE FROM staff_branch WHERE staff_id = %s AND branch_id = %s",
                (SHARAPOV, BRANCH_AB))
    sql.commit()

    assert SHARAPOV in ids(get(client, "teachers", branch_id=BRANCH_AF))
    assert SHARAPOV not in ids(get(client, "teachers", branch_id=BRANCH_AB))


def test_archived_teacher_is_not_offered(client, sql):
    """Уволенному пробный не назначают — его не должно быть в списке."""
    sql.execute("UPDATE staff SET archived_at = now() WHERE id = %s", (SHARAPOV,))
    sql.commit()

    assert SHARAPOV not in ids(get(client, "teachers"))


def test_admins_are_not_offered_as_teachers(client, sql):
    """В справочнике только kind = 'teacher'.

    Администратор — тоже строка в staff, но занятий он не ведёт, и его
    появление в списке преподавателей означало бы пробный урок, который
    некому провести.
    """
    assert len(get(client, "teachers")) == len(seed_demo.TEACHERS)

    sql.execute(
        """INSERT INTO staff (tenant_id, person_id, kind)
           SELECT tenant_id, person_id, 'admin' FROM app_user WHERE id = %s""",
        (seed_demo.ADMIN_USER,),
    )
    sql.commit()

    assert len(get(client, "teachers")) == len(seed_demo.TEACHERS)


# ---------------------------------------------------------------------------
# Кабинеты
# ---------------------------------------------------------------------------


def test_room_carries_branch_and_features(client):
    """features нужны, чтобы погасить неподходящий кабинет до отправки формы.

    Правило «барабаны требуют установки» остаётся на сервере (422 при
    попытке), но администратору незачем узнавать о нём после нажатия:
    сверить room.features с discipline.room_reqs интерфейс может сам.
    """
    room = by_id(get(client, "rooms"), DRUM_A)

    assert room["name"] == "Барабанная A"
    assert room["branch"] == {"id": BRANCH_AF, "name": "Аль-Фараби 53В"}
    assert room["features"]["drum_kit"] is True
    assert room["capacity"] == 1


def test_rooms_filtered_by_branch(client):
    rooms = get(client, "rooms", branch_id=BRANCH_AF)

    assert DRUM_A in ids(rooms)
    assert CLASS3 not in ids(rooms), "Класс 3 стоит в другом филиале"
    assert all(room["branch"]["id"] == BRANCH_AF for room in rooms)


def test_archived_room_is_not_offered(client, sql):
    sql.execute("UPDATE room SET archived_at = now() WHERE id = %s", (DRUM_A,))
    sql.commit()

    assert DRUM_A not in ids(get(client, "rooms"))


# ---------------------------------------------------------------------------
# Направления
# ---------------------------------------------------------------------------


def test_discipline_carries_min_age_and_room_reqs(client):
    """Оба поля — настройки школы, и зашивать их в клиент нельзя.

    min_age кормит предупреждение «берут с 5 лет», room_reqs — проверку
    кабинета. У каждой школы свои, и первая же школа, берущая на барабаны
    с четырёх, сломала бы захардкоженную таблицу.
    """
    drums = by_id(get(client, "disciplines"), DRUMS)

    assert drums["name"] == "Барабаны"
    assert drums["min_age"] == 5
    assert drums["room_reqs"] == {"drum_kit": True}

    guitar = by_id(get(client, "disciplines"), GUITAR)
    assert guitar["min_age"] == 6
    assert guitar["room_reqs"] == {}, "гитаре особый кабинет не нужен"


def test_disciplines_keep_the_order_of_the_school(client):
    """Порядок задан школой (sort_order), а не алфавитом: он же в интерфейсе."""
    names = [d["name"] for d in get(client, "disciplines")]
    assert names[0] == "Барабаны"
    assert names == [
        "Барабаны", "Гитара", "Вокал", "Фортепиано", "Укулеле", "Бас-гитара", "Перкуссия"
    ]


def test_archived_discipline_is_not_offered(client, sql):
    sql.execute("UPDATE discipline SET archived_at = now() WHERE id = %s", (GUITAR,))
    sql.commit()

    assert GUITAR not in ids(get(client, "disciplines"))
    # И из карточки преподавателя тоже: предлагать закрытое направление
    # значит дать администратору выбрать то, чего школа больше не продаёт.
    fedko = by_id(get(client, "teachers"), FEDKO)
    assert GUITAR not in {d["id"] for d in fedko["disciplines"]}


# ---------------------------------------------------------------------------
# Общее
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["teachers", "rooms", "disciplines"])
def test_directories_are_isolated_by_tenant(client, path):
    """Справочник — такие же данные школы, как ученики: чужие не видны.

    Условия `WHERE tenant_id = ...` в запросах нет — его ставит RLS,
    и проверить это можно только настоящим вторым тенантом. Соседняя школа
    направлений не завела вовсе, поэтому её список сравнивается на пересечение,
    а не на непустоту: пустой ответ здесь такой же верный, как и любой другой.
    """
    ours = ids(get(client, path))
    theirs = ids(get(client, path, headers=HEADERS_OTHER))

    assert ours
    assert ours & theirs == set()


@pytest.mark.parametrize("path", ["/api/v1/teachers", "/api/v1/rooms", "/api/v1/disciplines"])
def test_directories_require_tenant_header(client, path):
    assert client.get(path).status_code == 401
