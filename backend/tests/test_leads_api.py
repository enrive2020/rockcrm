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
