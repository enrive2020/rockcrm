"""Доска воронки, карточка заявки, заведение и смена стадии.

Смысл этапа — отчёт, а отчёт считается из lead_stage_history. Поэтому
тесты смотрят не только в ответ API, но и в саму историю: стадия без
записи в ней ломает конверсию молча, и обнаружилось бы это через месяц
на разговоре с владельцем школы.
"""
from __future__ import annotations

from conftest import HEADERS, HEADERS_OTHER, lead
from scripts import seed_demo

DISC_DRUMS = seed_demo.DISC["drums"]
BRANCH_AF = seed_demo.BRANCH_AF


def board(client, headers=None, **params):
    response = client.get("/api/v1/leads", params=params, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def column(body: dict, stage: str) -> dict:
    return next(c for c in body["columns"] if c["stage"] == stage)


def names(body: dict, stage: str) -> list[str]:
    return [c["name"] for c in column(body, stage)["leads"]]


def card(client, key: str, headers=None):
    return client.get(f"/api/v1/leads/{lead(key)}", headers=headers or HEADERS)


def ok_card(client, key: str) -> dict:
    response = card(client, key)
    assert response.status_code == 200, response.text
    return response.json()


def create(client, headers=None, **body):
    body.setdefault("name", "Тест Тестов")
    return client.post("/api/v1/leads", json=body, headers=headers or HEADERS)


def patch(client, lead_id: str, headers=None, **body):
    return client.patch(f"/api/v1/leads/{lead_id}", json=body, headers=headers or HEADERS)


def history(sql, lead_id: str):
    return sql.execute(
        """SELECT from_stage, to_stage FROM lead_stage_history
           WHERE lead_id = %s ORDER BY id""",
        (lead_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Доска
# ---------------------------------------------------------------------------


def test_board_has_all_stages_in_funnel_order(client):
    body = board(client)
    assert [c["stage"] for c in body["columns"]] == [
        "new", "contacting", "trial_booked", "trial_held", "won", "lost"
    ]
    assert column(body, "new")["title"] == "Новая"
    assert column(body, "new")["count"] == len(column(body, "new")["leads"])


def test_board_matches_the_prototype_counts(client):
    body = board(client)
    assert [column(body, s)["count"] for s in
            ("new", "contacting", "trial_booked", "trial_held", "won")] == [3, 2, 2, 2, 3]
    assert "Алиса Ким" in names(body, "trial_booked")


def test_board_summary_is_counted_from_history(client):
    """Конверсию нельзя считать по колонкам: купившие в «пробном» уже не лежат."""
    summary = board(client)["summary"]
    assert summary["total"] == 16
    assert summary["overdue"] == 1
    # Пробный провели пятеро, купили трое.
    assert summary["conversion_trial_to_won_pct"] == 60
    assert summary["avg_days_to_won"] == 4.0


def test_board_card_shows_waiting_time_and_source(client):
    card_ = next(c for c in column(board(client), "new")["leads"] if c["name"] == "Ержан Тулеу")
    assert card_["source"] == "instagram"
    assert card_["student_age"] == 28
    assert card_["waiting_for"].endswith(("часа", "часов", "час"))


def test_board_flags_are_actionable_facts(client):
    body = board(client)
    flags = {c["name"]: set(c["flags"]) for col in body["columns"] for c in col["leads"]}
    # Две неудачных попытки дозвона.
    assert "no_answer" in flags["Мадина Абишева"]
    # Напоминание «перезвонить» в прошлом.
    assert "overdue" in flags["Ольга Ким"]
    # Пробный Алисы стоит в занятой Барабанной A — тот самый конфликт прототипа.
    assert "trial_conflict" in flags["Алиса Ким"]
    assert not flags["Аружан Сапар"]


def test_board_filters_narrow_to_one_column(client):
    body = board(client, stage="new")
    assert [c["stage"] for c in body["columns"]] == ["new"]
    assert len(body["columns"][0]["leads"]) == 3


def test_board_filter_by_source(client):
    body = board(client, source="whatsapp")
    found = [c["name"] for col in body["columns"] for c in col["leads"]]
    assert set(found) == {"Санжар Тлеу", "Руслан Ким"}


def test_board_of_other_tenant_is_empty(client):
    body = board(client, headers=HEADERS_OTHER)
    assert sum(c["count"] for c in body["columns"]) == 0


# ---------------------------------------------------------------------------
# Размер выборки и число запросов
#
# На демонстрационных данных доска отвечает мгновенно и оба дефекта не видны:
# заявок четырнадцать. На полугодовой истории их 688, и доска делала 395
# запросов за 1,3 секунды, отдавая целиком колонку «Отказ» в 509 карточек.
# Поэтому здесь проверяется не время, а то, что от него зависит: размер
# выборки и постоянство числа запросов.
# ---------------------------------------------------------------------------


def test_board_page_is_limited_and_count_stays_honest(client):
    """Колонка отдаёт страницу, но счётчик показывает, сколько их всего.

    Если `count` начнёт равняться размеру страницы, администратор решит,
    что отказов пятьдесят, и перестанет искать причины в остальных
    четырёхстах — вранья такого рода интерфейс не заметит.
    """
    body = board(client, limit=1)
    new = column(body, "new")

    assert len(new["leads"]) == 1, "страница обязана быть ограничена"
    assert new["count"] == 3, "счётчик колонки — это вся стадия, а не страница"
    assert new["has_more"] is True
    assert new["next_offset"] == 1
    # Шапка тоже считает всю воронку, а не загруженное.
    assert body["summary"]["total"] == 16
    assert body["summary"]["overdue"] == 1


def test_board_pages_do_not_lose_or_double_leads(client):
    """Три страницы по одной дают ровно те же три заявки, что и одна на три."""
    whole = names(board(client), "new")
    assert len(whole) == 3

    paged = []
    offset = 0
    while True:
        col = column(board(client, limit=1, offset=offset), "new")
        paged += [c["name"] for c in col["leads"]]
        if not col["has_more"]:
            break
        offset = col["next_offset"]

    assert paged == whole
    assert len(set(paged)) == 3, "заявка не должна попасть на две страницы сразу"

    # Последняя страница честно говорит, что дальше ничего нет.
    last = column(board(client, limit=1, offset=2), "new")
    assert last["has_more"] is False and last["next_offset"] is None


def test_board_limit_applies_to_every_column_separately(client):
    """Ограничение постадийное: одна большая колонка не съедает остальные."""
    body = board(client, limit=1)
    filled = [c["stage"] for c in body["columns"] if c["leads"]]
    assert len(filled) >= 4, "каждая непустая колонка обязана отдать свою карточку"
    assert all(len(c["leads"]) <= 1 for c in body["columns"])


def test_board_query_count_does_not_grow_with_the_number_of_leads(client, monkeypatch):
    """N+1: занятость слотов берётся одним запросом на доску, а не на карточку.

    Каждый пробный на доске стоил отдельного `slot_occupants`, и на живой
    школе это давало 395 запросов вместо десятка. Считаем запросы до и после
    добавления трёх заявок с пробными: число обязано остаться тем же.
    """
    import psycopg

    from scripts import seed_demo

    counter = {"n": 0}
    original = psycopg.Cursor.execute

    def counting(self, *args, **kwargs):
        counter["n"] += 1
        return original(self, *args, **kwargs)

    def queries_for_board() -> int:
        monkeypatch.setattr(psycopg.Cursor, "execute", counting)
        counter["n"] = 0
        try:
            assert client.get("/api/v1/leads", headers=HEADERS).status_code == 200
            return counter["n"]
        finally:
            monkeypatch.undo()

    before = queries_for_board()

    # Три новых заявки, каждая с назначенным пробным: именно пробный тянул
    # за собой лишний запрос за занятостью кабинета и преподавателя.
    for index in range(3):
        lead_id = create(
            client, name=f"Пробный {index}", phone=f"+7701555010{index}",
            discipline_id=DISC_DRUMS, branch_id=BRANCH_AF,
        ).json()["id"]
        booked = client.post(
            f"/api/v1/leads/{lead_id}/trial",
            json={
                "teacher_id": seed_demo.teacher_id("madratov"),
                "room_id": seed_demo.ROOMS["drum_a"],
                "starts_at": f"2026-08-1{3 + index}T09:00:00+05:00",
                "duration_min": 45,
            },
            headers=HEADERS,
        )
        assert booked.status_code == 201, booked.text

    after = queries_for_board()
    assert sum(c["count"] for c in board(client)["columns"]) == 19
    assert after == before, (
        f"на трёх новых заявках доска сделала {after} запросов вместо {before} — "
        "занятость слотов снова спрашивается по карточке"
    )


# ---------------------------------------------------------------------------
# Карточка
# ---------------------------------------------------------------------------


def test_card_shows_trial_with_conflicts(client):
    body = ok_card(client, "alisa")
    assert body["stage"] == "trial_booked"
    assert body["discipline"]["name"] == "Барабаны"
    assert body["assigned_to"]["name"] == "Айгерим Дюсенова"
    trial = body["trial"]
    assert trial["teacher"] == "Егор Мадратов"
    assert trial["room"] == "Барабанная A"
    assert [c["kind"] for c in trial["conflicts"]] == ["room"]
    assert "занят" in trial["conflicts"][0]["message"]


def test_card_history_is_the_whole_path(client):
    body = ok_card(client, "alisa")
    assert [(h["from"], h["to"]) for h in body["history"]] == [
        (None, "new"), ("new", "contacting"), ("contacting", "trial_booked")
    ]


def test_card_warns_about_age_below_minimum(client):
    """Барабаны берут с 5 лет, Аружан 4 — это предупреждение, а не запрет."""
    body = ok_card(client, "aruzhan")
    assert body["age_warning"] is not None
    assert "с 5 лет" in body["age_warning"]
    assert ok_card(client, "alisa")["age_warning"] is None


def test_card_discipline_carries_min_age(client):
    """Порог возраста едет вместе с направлением, а не зашит в интерфейс.

    Готовой фразы мало: интерфейсу нужно и само число — подсветить поле
    возраста и сравнить его прямо при вводе, не дожидаясь ответа сервера.
    Захардкоженная таблица порогов сломалась бы на первой же школе,
    которая берёт на барабаны с четырёх: min_age — настройка школы.
    """
    body = ok_card(client, "aruzhan")
    assert body["discipline"]["id"] == DISC_DRUMS
    assert body["discipline"]["min_age"] == 5
    # Порог и предупреждение считаются из одного поля и разъехаться не могут:
    # иначе на экране было бы «берут с 5 лет», а подсветка срабатывала бы с 7.
    assert f"с {body['discipline']['min_age']} лет" in body["age_warning"]


def test_card_of_other_tenant_is_404(client):
    assert card(client, "alisa", headers=HEADERS_OTHER).status_code == 404


# ---------------------------------------------------------------------------
# Заведение вручную
# ---------------------------------------------------------------------------


def test_manual_lead_normalizes_phone_and_writes_history(client, sql):
    response = create(
        client, name="Ольга Ким", phone="8 (701) 555-98-76", student_name="Ольга",
        student_age=34, discipline_id=DISC_DRUMS, branch_id=BRANCH_AF,
        source="phone", comment="хочет вечером после 19",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["phone"] == "+77015559876", "номер приводится к E.164 из любой записи"
    assert body["stage"] == "new"
    assert body["utm"]["comment"] == "хочет вечером после 19"
    # Первая строка истории обязательна: без неё у стадии new нет момента
    # входа, и отчёт не сможет посчитать ни её конверсию, ни время простоя.
    assert [(h["from_stage"], h["to_stage"]) for h in history(sql, body["id"])] == [(None, "new")]


def test_duplicate_phone_in_open_stage_is_refused(client):
    """Второй лид на того же человека — это враньё в воронке."""
    response = create(client, name="Санжар ещё раз", phone="+77013330006", source="phone")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "duplicate_lead"
    assert error["details"]["lead_id"] == lead("sanzhar")


def test_duplicate_phone_of_closed_lead_is_allowed(client):
    """Тот же человек через полгода — это новая заявка, а не дубль."""
    assert create(client, name="Нурбек снова", phone="+77013330013", source="phone").status_code == 201


def test_bad_phone_is_refused_with_explanation(client):
    response = create(client, name="Кривой номер", phone="123", source="phone")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_phone"


# ---------------------------------------------------------------------------
# Смена стадии
# ---------------------------------------------------------------------------


def test_stage_change_writes_history(client, sql):
    lead_id = lead("madina")
    assert patch(client, lead_id, stage="contacting", contact_attempts=3).status_code == 200
    rows = history(sql, lead_id)
    assert (rows[-1]["from_stage"], rows[-1]["to_stage"]) == ("new", "contacting")


def test_same_stage_does_not_duplicate_history(client, sql):
    """Повторное сохранение той же стадии не должно раздувать отчёт."""
    lead_id = lead("madina")
    before = len(history(sql, lead_id))
    assert patch(client, lead_id, stage="new", contact_attempts=1).status_code == 200
    assert len(history(sql, lead_id)) == before


def test_won_cannot_be_set_by_hand(client, sql):
    """Иначе появятся выигранные заявки без ученика, и отчёт начнёт врать."""
    lead_id = lead("damir")
    response = patch(client, lead_id, stage="won")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "won_requires_conversion"
    assert all(h["to_stage"] != "won" for h in history(sql, lead_id))


def test_lost_without_reason_is_refused(client):
    response = patch(client, lead("damir"), stage="lost")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "lost_reason_required"


def test_lost_with_reason_is_recorded(client, sql):
    lead_id = lead("damir")
    body = patch(client, lead_id, stage="lost", lost_reason="price").json()
    assert body["stage"] == "lost" and body["lost_reason"] == "price"
    assert history(sql, lead_id)[-1]["to_stage"] == "lost"


def test_returning_from_lost_clears_the_reason(client):
    """«Дорого» на заявке, вернувшейся в дозвон, читалось бы как решение клиента."""
    lead_id = lead("nurbek")
    body = patch(client, lead_id, stage="contacting").json()
    assert body["stage"] == "contacting"
    assert body["lost_reason"] is None


def test_assignment_and_reminder(client):
    body = patch(
        client, lead("aruzhan"),
        assigned_to=seed_demo.MANAGER_USER,
        next_action_at="2026-08-12T18:00:00+05:00",
        contact_attempts=1,
    ).json()
    assert body["assigned_to"]["name"] == "Айгерим Дюсенова"
    assert body["next_action_at"] == "2026-08-12T18:00:00+05:00"
    assert body["contact_attempts"] == 1


def test_assignment_to_unknown_user_is_404(client):
    response = patch(
        client, lead("aruzhan"), assigned_to="0189b0de-0000-7000-8000-0000000000ff"
    )
    assert response.status_code == 404


def test_patch_of_other_tenant_is_404(client):
    assert patch(client, lead("alisa"), headers=HEADERS_OTHER, stage="contacting").status_code == 404
