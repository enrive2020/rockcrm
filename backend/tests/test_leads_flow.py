"""Пробный урок, конверсия в ученика, приём по вебхуку и отчёт по воронке.

Три места, где этап ломается тише всего, и поэтому проверяются придирчиво:
идемпотентность вебхука (без неё ретраи LeadHub превращаются в дубли),
переиспользование персоны при конверсии (иначе теряется семейная скидка)
и расчёт отчёта из истории, а не из текущих стадий.
"""
from __future__ import annotations

import datetime as dt

from conftest import HEADERS, HEADERS_OTHER, lead
from scripts import seed_demo

DISC_DRUMS = seed_demo.DISC["drums"]
BRANCH_AF = seed_demo.BRANCH_AF
ROOM_DRUM_A = seed_demo.ROOMS["drum_a"]
ROOM_CLASS1 = seed_demo.ROOMS["class1"]
TEACHER = seed_demo.teacher_id("madratov")
PLAN_DRUMS_8 = seed_demo.plan_id("drums8")

KEY = {"X-Api-Key": "rck_demo_rockschool_leads_key"}
KEY_OTHER = {"X-Api-Key": "rck_demo_other_school_key"}
KEY_REVOKED = {"X-Api-Key": "rck_demo_revoked_key"}
KEY_READONLY = {"X-Api-Key": "rck_demo_readonly_key"}

# Свободный слот: демо-день занят, следующий — нет.
FREE_SLOT = "2026-08-13T13:00:00+05:00"
BUSY_SLOT = "2026-08-12T13:00:00+05:00"  # Барабанная A занята Ержаном Оспановым


def book(client, key: str, headers=None, **body):
    body.setdefault("teacher_id", TEACHER)
    body.setdefault("room_id", ROOM_DRUM_A)
    body.setdefault("starts_at", FREE_SLOT)
    body.setdefault("duration_min", 45)
    return client.post(
        f"/api/v1/leads/{lead(key)}/trial", json=body, headers=headers or HEADERS
    )


def convert(client, key: str, headers=None, **body):
    body.setdefault("student", {"first_name": "Санжар", "last_name": "Тлеу",
                                "discipline_id": DISC_DRUMS, "branch_id": BRANCH_AF})
    return client.post(
        f"/api/v1/leads/{lead(key)}/convert", json=body, headers=headers or HEADERS
    )


def hook(client, headers=None, **body):
    body.setdefault("name", "Санжар Тлеу")
    return client.post("/api/v1/hooks/leads", json=body, headers=headers or KEY)


def stages(sql, lead_id: str) -> list[str]:
    return [
        row["to_stage"]
        for row in sql.execute(
            "SELECT to_stage FROM lead_stage_history WHERE lead_id = %s ORDER BY id",
            (lead_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------------------------
# Пробный урок
# ---------------------------------------------------------------------------


def test_trial_creates_lesson_and_moves_the_lead(client, sql):
    response = book(client, "sanzhar", price=2000)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stage"] == "trial_booked"
    assert body["teacher"] == "Егор Мадратов"
    assert body["room"] == "Барабанная A"
    assert body["starts_at"] == FREE_SLOT

    lesson = sql.execute(
        "SELECT lead_id, student_id, kind, status FROM lesson WHERE id = %s",
        (body["lesson_id"],),
    ).fetchone()
    assert str(lesson["lead_id"]) == lead("sanzhar")
    assert lesson["student_id"] is None, "у пробного нет ученика — есть заявка"
    assert (lesson["kind"], lesson["status"]) == ("trial", "planned")

    assert stages(sql, lead("sanzhar"))[-1] == "trial_booked"


def test_trial_appears_in_the_schedule(client):
    """Пробный — обычное занятие: расписание этапа 1 обязано его показать."""
    lesson_id = book(client, "sanzhar").json()["lesson_id"]
    body = client.get(
        "/api/v1/schedule", params={"branch_id": BRANCH_AF, "date": "2026-08-13"},
        headers=HEADERS,
    ).json()
    found = [
        les for track in body["tracks"] for les in track["lessons"] if les["id"] == lesson_id
    ]
    assert found and found[0]["kind"] == "trial"
    assert found[0]["title"] == "Санжар"


def test_trial_queues_one_reminder_however_many_times_it_is_booked(client, sql):
    """Повторное назначение не должно слать второе сообщение."""
    first = book(client, "sanzhar").json()
    assert first["notification_queued"] is True

    queued = sql.execute(
        """SELECT template, to_address, dedup_key, send_after, status
           FROM notification WHERE dedup_key = %s""",
        (f"trial_reminder:{first['lesson_id']}",),
    ).fetchall()
    assert len(queued) == 1
    assert queued[0]["template"] == "trial_reminder"
    assert queued[0]["to_address"] == "+77013330006"
    assert queued[0]["status"] == "queued"
    # За сутки до занятия: раньше забудут, позже уже не переставят день.
    assert queued[0]["send_after"] == dt.datetime.fromisoformat(FREE_SLOT) - dt.timedelta(days=1)


def test_busy_room_is_409_with_the_name_of_who_is_there(client):
    response = book(client, "sanzhar", starts_at=BUSY_SLOT)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "slot_busy"
    assert "Ержан Оспанов" in error["message"]
    assert "овербукинг" in error["message"]


def test_busy_room_is_allowed_with_overbook_ack(client):
    """Осознанный овербукинг проходит — конфликт остаётся видимым в интерфейсе."""
    response = book(client, "sanzhar", starts_at=BUSY_SLOT, overbook_ack=True)
    assert response.status_code == 201, response.text
    card = client.get(f"/api/v1/leads/{lead('sanzhar')}", headers=HEADERS).json()
    assert card["trial"]["conflicts"], "конфликт не исчезает от подтверждения"
    assert "trial_conflict" in card["flags"]


def test_room_without_drum_kit_is_refused(client):
    """Барабаны без установки — это не занятие, а зря пришедший родитель."""
    response = book(client, "sanzhar", room_id=ROOM_CLASS1)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "room_unsuitable"
    assert error["details"]["missing"] == ["drum_kit"]


def test_trial_for_closed_lead_is_refused(client):
    response = book(client, "nurbek")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "lead_closed"


def test_trial_of_other_tenant_is_404(client):
    assert book(client, "sanzhar", headers=HEADERS_OTHER).status_code == 404


# ---------------------------------------------------------------------------
# Конверсия
# ---------------------------------------------------------------------------


def test_conversion_creates_student_with_subscription(client, sql):
    response = convert(
        client, "sanzhar",
        payer={"first_name": "Асем", "last_name": "Тлеу", "phone": "+77016660001"},
        subscription={"plan_id": PLAN_DRUMS_8, "starts_on": "2026-09-01",
                      "payment": {"amount": 54000, "method": "kaspi"}},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stage"] == "won"

    student = sql.execute(
        "SELECT family_id, discipline_id, branch_id FROM student WHERE id = %s",
        (body["student_id"],),
    ).fetchone()
    assert str(student["family_id"]) == body["family_id"]

    # Абонемент продан тем же кодом, что и в этапе 2: журнал наполнен,
    # остаток посчитан триггером, платёж лежит на семье.
    subscription = sql.execute(
        "SELECT lessons_balance, student_id FROM subscription WHERE id = %s",
        (body["subscription_id"],),
    ).fetchone()
    assert subscription["lessons_balance"] == 8
    entry = sql.execute(
        "SELECT kind, lessons_delta FROM subscription_entry WHERE subscription_id = %s",
        (body["subscription_id"],),
    ).fetchall()
    assert [(e["kind"], e["lessons_delta"]) for e in entry] == [("purchase", 8)]

    assert stages(sql, lead("sanzhar"))[-1] == "won"


def test_conversion_reuses_person_with_the_same_phone(client, sql):
    """Тот же родитель со вторым ребёнком — одна персона и одна семья.

    Две персоны на один телефон означают потерянную семейную скидку
    и разъехавшуюся историю платежей.
    """
    gulnara = sql.execute(
        "SELECT id FROM person WHERE phone = '+77015552418'"
    ).fetchone()["id"]
    family_before = sql.execute(
        "SELECT id FROM family WHERE payer_id = %s", (gulnara,)
    ).fetchone()["id"]

    body = convert(
        client, "sanzhar",
        payer={"first_name": "Гульнара", "last_name": "Сагындык", "phone": "+77015552418"},
    ).json()

    assert str(family_before) == body["family_id"], "ребёнок попал в существующую семью"
    same_phone = sql.execute(
        "SELECT count(*) AS n FROM person WHERE phone = '+77015552418'"
    ).fetchone()
    assert same_phone["n"] == 1, "второй персоны на тот же телефон не появилось"

    # Скидка семьи никуда не делась и применится к следующей продаже.
    discount = sql.execute(
        "SELECT discount_pct FROM family WHERE id = %s", (body["family_id"],)
    ).fetchone()
    assert int(discount["discount_pct"]) == 10


def test_conversion_without_payer_makes_the_student_the_payer(client, sql):
    """Взрослый ученик платит за себя — семья из одного человека."""
    body = convert(
        client, "olga",
        student={"first_name": "Ольга", "last_name": "Ким", "birth_date": "1992-04-01",
                 "discipline_id": DISC_DRUMS, "branch_id": BRANCH_AF},
    ).json()
    family = sql.execute(
        "SELECT payer_id FROM family WHERE id = %s", (body["family_id"],)
    ).fetchone()
    assert str(family["payer_id"]) == body["person_id"]
    person = sql.execute(
        "SELECT phone FROM person WHERE id = %s", (body["person_id"],)
    ).fetchone()
    assert person["phone"] == "+77013330007", "телефон взят из заявки"


def test_conversion_without_subscription_still_wins(client, sql):
    """Ученика заводят сейчас, абонемент продают позже — заявка выиграна."""
    body = convert(client, "sanzhar").json()
    assert body["subscription_id"] is None
    assert body["stage"] == "won"
    assert stages(sql, lead("sanzhar"))[-1] == "won"


def test_conversion_links_the_lead_to_what_it_became(client):
    body = convert(client, "sanzhar").json()
    card = client.get(f"/api/v1/leads/{lead('sanzhar')}", headers=HEADERS).json()
    assert card["converted"] == {
        "student_id": body["student_id"], "person_id": body["person_id"]
    }


def test_second_conversion_is_refused(client):
    assert convert(client, "sanzhar").status_code == 201
    response = convert(client, "sanzhar")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_converted"


def test_conversion_of_other_tenant_is_404(client):
    assert convert(client, "sanzhar", headers=HEADERS_OTHER).status_code == 404


# ---------------------------------------------------------------------------
# Вебхук
# ---------------------------------------------------------------------------


def test_webhook_creates_lead_and_normalizes_phone(client):
    response = hook(
        client, external_id="leadhub-8f21c3", phone="8 701 555 33 22",
        student_name="Санжар", student_age=13, discipline="барабаны",
        source="telegram_bot", utm={"utm_source": "instagram"},
        comment="удобно после 18:00",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["phone"] == "+77015553322"
    assert body["discipline"]["name"] == "Барабаны"
    assert body["stage"] == "new"
    assert body["utm"]["utm_source"] == "instagram"
    assert body["utm"]["comment"] == "удобно после 18:00"


def test_webhook_is_idempotent_by_external_id(client, sql):
    """LeadHub построен на ретраях: дубль в воронке ломает весь отчёт."""
    first = hook(client, external_id="leadhub-retry", phone="+77015553322")
    assert first.status_code == 201

    again = hook(client, external_id="leadhub-retry", phone="+77015553322",
                 name="Санжар Тлеу (повтор)")
    assert again.status_code == 200, "повторная доставка — не ошибка"
    assert again.json()["id"] == first.json()["id"]

    total = sql.execute(
        "SELECT count(*) AS n FROM lead WHERE external_id = 'leadhub-retry'"
    ).fetchone()
    assert total["n"] == 1
    # И истории тоже одна: иначе стадия new посчиталась бы дважды.
    assert stages(sql, first.json()["id"]) == ["new"]


def test_webhook_accepts_lead_with_unknown_discipline(client):
    """Терять лид из-за опечатки в названии направления нельзя."""
    response = hook(client, external_id="typo-1", discipline="барабаныы")
    assert response.status_code == 201
    assert response.json()["discipline"] is None


def test_webhook_matches_discipline_case_insensitively(client):
    body = hook(client, external_id="case-1", discipline="БАРАБАНЫ").json()
    assert body["discipline"]["name"] == "Барабаны"


def test_webhook_without_key_is_401(client):
    response = client.post("/api/v1/hooks/leads", json={"name": "Никто"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "no_api_key"


def test_webhook_with_unknown_key_is_401(client):
    response = hook(client, headers={"X-Api-Key": "rck_nonexistent"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "bad_api_key"


def test_revoked_key_is_401(client):
    response = hook(client, headers=KEY_REVOKED)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "revoked_api_key"


def test_key_without_write_scope_is_403(client):
    response = hook(client, headers=KEY_READONLY)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "scope_required"


def test_key_of_other_school_creates_lead_in_its_own_tenant(client, sql):
    """Ключ определяет школу. Подставить чужую подменой заголовка нельзя."""
    response = hook(
        client, headers=KEY_OTHER, external_id="other-1", name="Чужая заявка",
        # Заголовки внутренней авторизации здесь не читаются вовсе —
        # даже если их прислать, тенант возьмётся из ключа.
    )
    assert response.status_code == 201
    row = sql.execute(
        "SELECT tenant_id FROM lead WHERE external_id = 'other-1'"
    ).fetchone()
    assert str(row["tenant_id"]) == seed_demo.TENANT_OTHER

    # В нашей школе этой заявки нет.
    board = client.get("/api/v1/leads", headers=HEADERS).json()
    found = [c["name"] for col in board["columns"] for c in col["leads"]]
    assert "Чужая заявка" not in found


def test_webhook_marks_key_as_used(client, sql):
    before = sql.execute(
        "SELECT last_used_at FROM api_key WHERE name = 'Telegram-бот'"
    ).fetchone()
    assert before["last_used_at"] is None
    hook(client, external_id="used-1")
    after = sql.execute(
        "SELECT last_used_at FROM api_key WHERE name = 'Telegram-бот'"
    ).fetchone()
    assert after["last_used_at"] is not None


# ---------------------------------------------------------------------------
# Отчёт по воронке
# ---------------------------------------------------------------------------


def funnel(client, headers=None, **params):
    response = client.get("/api/v1/leads/funnel", params=params, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def stage_row(body: dict, stage: str) -> dict:
    return next(s for s in body["stages"] if s["stage"] == stage)


def test_funnel_is_counted_from_history_not_current_stages(client):
    """Купившие в колонке «пробный проведён» уже не лежат — и всё равно там были."""
    body = funnel(client, **{"from": "2026-07-01", "to": "2026-08-11"})
    # Пробный провели пятеро: трое купили, двое ещё думают.
    assert stage_row(body, "trial_held")["entered"] == 5
    assert stage_row(body, "trial_held")["moved_on"] == 3
    assert stage_row(body, "trial_held")["conversion_pct"] == 60
    # Через new прошли все 16 заявок демо-воронки.
    assert stage_row(body, "new")["entered"] == 16
    # У победы дальше идти некуда.
    assert stage_row(body, "won")["conversion_pct"] == 100


def test_funnel_sources_and_lost_reasons(client):
    body = funnel(client, **{"from": "2026-07-01", "to": "2026-08-11"})
    by_source = {s["source"]: s for s in body["sources"]}
    assert by_source["telegram_bot"]["leads"] == 4
    assert by_source["telegram_bot"]["won"] == 1
    assert by_source["telegram_bot"]["avg_days_to_won"] == 4.0

    reasons = {r["reason"]: r["count"] for r in body["lost_reasons"]}
    assert reasons == {"price": 2, "schedule": 1, "no_answer": 1}
    assert body["avg_days_to_won"] == 4.0


def test_funnel_reflects_a_fresh_conversion(client):
    before = funnel(client, **{"from": "2026-07-01", "to": "2026-08-11"})
    convert(client, "damir")
    after = funnel(client, **{"from": "2026-07-01", "to": "2026-08-11"})
    assert stage_row(after, "won")["entered"] == stage_row(before, "won")["entered"] + 1
    assert stage_row(after, "trial_held")["moved_on"] == (
        stage_row(before, "trial_held")["moved_on"] + 1
    )


def test_funnel_period_is_inclusive_of_the_last_day(client):
    """«По 11 августа» означает включая весь одиннадцатый."""
    empty = funnel(client, **{"from": "2020-01-01", "to": "2020-01-02"})
    assert stage_row(empty, "new")["entered"] == 0
    assert empty["avg_days_to_won"] is None


def test_funnel_of_other_tenant_is_empty(client):
    body = funnel(client, headers=HEADERS_OTHER)
    assert stage_row(body, "new")["entered"] == 0
