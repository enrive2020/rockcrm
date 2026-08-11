"""Продажа, продление, заморозка и её снятие.

Главное здесь — целостность: абонемент без записи `purchase` в журнале
означал бы остаток, взявшийся ниоткуда, а заморозка без сдвига срока
или без отмены занятий — половину операции, которую никто не восстановит.
Поэтому тесты смотрят не только в ответ API, но и в саму базу.
"""
from __future__ import annotations

import datetime as dt

import pytest
from conftest import HEADERS, HEADERS_OTHER, student, subscription

AMINA = "sagyndyk"
AMINA_SUB = subscription("sagyndyk")

# Тарифы демо-данных. Идентификаторы фиксированы вместе с остальным сидом.
from scripts import seed_demo  # noqa: E402

PLAN_DRUMS_8 = seed_demo.plan_id("drums8")
PLAN_DRUMS_4 = seed_demo.plan_id("drums4")

# Сентябрь: следующий период после августовского абонемента демо-данных.
NEXT_PERIOD = "2026-09-01"


def sell(client, student_key: str, headers=None, **body):
    body.setdefault("plan_id", PLAN_DRUMS_8)
    return client.post(
        f"/api/v1/students/{student(student_key)}/subscriptions",
        json=body,
        headers=headers or HEADERS,
    )


def freeze(client, subscription_id: str, day_from, day_to, reason="каникулы", headers=None):
    return client.post(
        f"/api/v1/subscriptions/{subscription_id}/holds",
        json={"from": str(day_from), "to": str(day_to), "reason": reason},
        headers=headers or HEADERS,
    )


def unfreeze(client, subscription_id: str, hold_id: str, headers=None):
    return client.delete(
        f"/api/v1/subscriptions/{subscription_id}/holds/{hold_id}",
        headers=headers or HEADERS,
    )


def sub_row(sql, subscription_id: str):
    return sql.execute(
        """SELECT lessons_total, lessons_balance, price, discount_pct, promo_code,
                  rules, valid_from, valid_until, status, family_id, plan_id
           FROM subscription WHERE id = %s""",
        (subscription_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Продажа
# ---------------------------------------------------------------------------


def test_sale_fills_the_journal(client, sql):
    """Абонемент без записи в журнале — остаток, взявшийся ниоткуда."""
    response = sell(client, AMINA, starts_on=NEXT_PERIOD, discount_pct=10)
    assert response.status_code == 201, response.text
    body = response.json()
    new_id = body["subscription_id"]

    entries = sql.execute(
        """SELECT kind, lessons_delta, makeups_delta, reason
           FROM subscription_entry WHERE subscription_id = %s ORDER BY id""",
        (new_id,),
    ).fetchall()
    assert [(e["kind"], e["lessons_delta"]) for e in entries] == [("purchase", 8)]

    # Остаток в ответе взят из кэша, который заполнил триггер по журналу.
    assert body["lessons_balance"] == 8
    assert sub_row(sql, new_id)["lessons_balance"] == 8


def test_sale_response_matches_contract(client):
    body = sell(
        client,
        AMINA,
        starts_on=NEXT_PERIOD,
        discount_pct=10,
        promo_code="RS25",
        payment={"amount": 48600, "method": "kaspi"},
    ).json()
    assert body["lessons_total"] == 8
    assert body["valid_from"] == "2026-09-01"
    # valid_days = 31, срок считается включая первый день.
    assert body["valid_until"] == "2026-10-01"
    assert body["price"] == 54000
    assert body["discount_pct"] == 10
    assert body["charged"] == 48600
    assert body["carried_over"] == 0
    assert body["payment_id"] is not None
    assert body["debt"] == 0


def test_sale_writes_payment_and_audit_in_one_transaction(client, sql):
    body = sell(
        client, AMINA, starts_on=NEXT_PERIOD,
        payment={"amount": 30000, "method": "cash"},
    ).json()

    payment = sql.execute(
        "SELECT amount, method, subscription_id, family_id FROM payment WHERE id = %s",
        (body["payment_id"],),
    ).fetchone()
    assert int(payment["amount"]) == 30000 and payment["method"] == "cash"
    assert str(payment["subscription_id"]) == body["subscription_id"]
    assert payment["family_id"] is not None, "платёж обязан лечь на семью — она платит"

    audit = sql.execute(
        "SELECT action, payload FROM audit_log WHERE entity_id = %s AND action = 'subscription.sell'",
        (body["subscription_id"],),
    ).fetchone()
    assert audit["payload"]["charged"] == body["charged"]


def test_sale_without_payment_leaves_debt(client):
    """Абонемент можно оформить с долгом — деньги донесут позже."""
    body = sell(client, AMINA, starts_on=NEXT_PERIOD, discount_pct=10).json()
    assert body["payment_id"] is None
    assert body["debt"] == 48600


def test_discount_defaults_to_family_discount(client):
    """Скидка «за второго ребёнка» задана на семье и не должна теряться."""
    body = sell(client, AMINA, starts_on=NEXT_PERIOD).json()
    assert body["discount_pct"] == 10
    assert body["charged"] == 48600


def test_renewal_does_not_touch_the_previous_subscription(client, sql):
    before = sub_row(sql, AMINA_SUB)
    body = sell(client, AMINA, starts_on=NEXT_PERIOD, discount_pct=10).json()
    after = sub_row(sql, AMINA_SUB)

    assert body["subscription_id"] != AMINA_SUB
    assert after["lessons_balance"] == before["lessons_balance"] == 5
    assert after["valid_until"] == before["valid_until"]
    assert after["status"] == before["status"]
    entries = sql.execute(
        "SELECT count(*) AS n FROM subscription_entry WHERE subscription_id = %s",
        (AMINA_SUB,),
    ).fetchone()
    assert entries["n"] == 5, "журнал старого абонемента не пополнялся"


def test_rules_are_copied_from_school_settings_not_from_previous(client, sql):
    """Старый абонемент живёт по условиям своей покупки, новый — по текущим."""
    sql.execute(
        "UPDATE subscription SET rules = rules || '{\"no_show_burns\": false}'::jsonb WHERE id = %s",
        (AMINA_SUB,),
    )
    sql.execute(
        """UPDATE tenant SET default_rules = default_rules || '{"makeup_ttl_days": 45}'::jsonb
           WHERE id = %s""",
        (seed_demo.TENANT,),
    )
    sql.commit()

    new_id = sell(client, AMINA, starts_on=NEXT_PERIOD).json()["subscription_id"]
    rules = sub_row(sql, new_id)["rules"]
    assert rules["makeup_ttl_days"] == 45, "правила берутся из настроек школы"
    assert rules["no_show_burns"] is True, "правила прошлого абонемента не наследуются"


def test_overlapping_subscription_is_refused(client):
    """Августовский абонемент ещё жив и не пуст — второй на тот же период не продаём."""
    response = sell(client, AMINA, starts_on="2026-08-15")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "subscription_overlap"
    assert error["details"]["lessons_balance"] == 5
    assert "01.09.2026" in error["message"], "ошибка обязана подсказать, с какой даты продлевать"


def test_sale_on_exhausted_subscription_is_allowed(client):
    """У Дмитрия Со остаток ноль: продать новый абонемент прямо сейчас можно."""
    response = sell(client, "so", plan_id=seed_demo.plan_id("vocal8"), starts_on="2026-08-15")
    assert response.status_code == 201, response.text


def test_carry_over_is_off_by_default_and_says_so(client, sql):
    body = sell(client, AMINA, starts_on=NEXT_PERIOD, carry_over=True).json()
    assert body["carried_over"] == 0
    assert "carry_over_lessons" in body["carry_over_note"]
    # Старый абонемент не тронут: без правила переносить нечего.
    assert sub_row(sql, AMINA_SUB)["lessons_balance"] == 5


def test_carry_over_moves_balance_when_school_allows_it(client, sql):
    sql.execute(
        """UPDATE tenant SET default_rules = default_rules || '{"carry_over_lessons": 3}'::jsonb
           WHERE id = %s""",
        (seed_demo.TENANT,),
    )
    sql.commit()

    body = sell(client, AMINA, starts_on=NEXT_PERIOD, carry_over=True).json()
    assert body["carried_over"] == 3
    assert body["lessons_balance"] == 11, "8 из тарифа плюс 3 перенесённых"

    # Списание со старого обязано быть видно в его журнале.
    moved = sql.execute(
        """SELECT kind, lessons_delta FROM subscription_entry
           WHERE subscription_id = %s AND kind = 'transfer_out'""",
        (AMINA_SUB,),
    ).fetchone()
    assert moved["lessons_delta"] == -3
    assert sub_row(sql, AMINA_SUB)["lessons_balance"] == 2


def test_sale_of_foreign_plan_is_404(client):
    """Тариф чужой школы неотличим от несуществующего — так и должно быть."""
    response = client.post(
        f"/api/v1/students/{seed_demo.OTHER_STUDENT}/subscriptions",
        json={"plan_id": PLAN_DRUMS_8, "starts_on": NEXT_PERIOD},
        headers=HEADERS_OTHER,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_sale_for_student_of_other_tenant_is_404(client):
    response = sell(client, AMINA, headers=HEADERS_OTHER, starts_on=NEXT_PERIOD)
    assert response.status_code == 404


def test_short_plan_gives_its_own_lessons_and_period(client):
    body = sell(client, AMINA, plan_id=PLAN_DRUMS_4, starts_on=NEXT_PERIOD).json()
    assert body["lessons_total"] == 4
    assert body["lessons_balance"] == 4
    assert body["price"] == 29000


# ---------------------------------------------------------------------------
# Заморозка
# ---------------------------------------------------------------------------

# Заморозка не может начинаться в прошлом, поэтому даты считаются от «сегодня»,
# а не пишутся цифрами: тест, привязанный к августу 2026, протух бы 1 сентября.
TODAY = dt.date.today()
HOLD_FROM = TODAY + dt.timedelta(days=3)
HOLD_TO = HOLD_FROM + dt.timedelta(days=11)


def test_hold_shifts_valid_until_by_exact_days(client, sql):
    before = sub_row(sql, AMINA_SUB)["valid_until"]
    response = freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["days"] == 11
    assert body["valid_until_before"] == before.isoformat()
    assert body["valid_until_after"] == (before + dt.timedelta(days=11)).isoformat()
    assert sub_row(sql, AMINA_SUB)["valid_until"] == before + dt.timedelta(days=11)
    assert body["freeze_days_left"] == 3   # лимит 14 минус 11


def test_hold_cancels_lessons_inside_the_interval_only(client, sql):
    """Занятия внутри интервала отменяются без списания, снаружи — не трогаются."""
    balance_before = sub_row(sql, AMINA_SUB)["lessons_balance"]

    # Занятия Амины стоят на 14, 19 и 21 августа демо-данных.
    response = freeze(client, AMINA_SUB, "2026-08-14", "2026-08-25")
    assert response.status_code == 201, response.text
    assert response.json()["lessons_cancelled"] == 3

    rows = sql.execute(
        """SELECT (starts_at AT TIME ZONE 'Asia/Almaty')::date AS day, status
           FROM lesson WHERE student_id = %s AND status <> 'held' ORDER BY starts_at""",
        (student(AMINA),),
    ).fetchall()
    inside = {r["day"] for r in rows if r["status"] == "cancelled"}
    assert {dt.date(2026, 8, 14), dt.date(2026, 8, 19), dt.date(2026, 8, 21)} <= inside
    # Занятие 12 августа лежит до интервала и обязано остаться запланированным.
    assert any(r["day"] == dt.date(2026, 8, 12) and r["status"] == "planned" for r in rows)

    # Отменённые занятия не списываются: остаток не изменился.
    assert sub_row(sql, AMINA_SUB)["lessons_balance"] == balance_before


def test_hold_writes_audit(client, sql):
    body = freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).json()
    audit = sql.execute(
        """SELECT payload FROM audit_log
           WHERE action = 'subscription.hold' AND payload ->> 'hold_id' = %s""",
        (body["hold_id"],),
    ).fetchone()
    assert audit["payload"]["days"] == 11
    assert audit["payload"]["reason"] == "каникулы"


def test_overlapping_holds_are_refused_with_409(client):
    """Ограничение исключения базы обязано стать понятным 409, а не 500."""
    assert freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).status_code == 201
    response = freeze(client, AMINA_SUB, HOLD_FROM + dt.timedelta(days=2), HOLD_TO)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "hold_overlap"
    assert "перекрыва" in response.json()["error"]["message"]


def test_yearly_freeze_limit_is_enforced(client):
    """Лимит 14 дней в году; в тексте обязано быть, сколько дней осталось."""
    assert freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).status_code == 201
    response = freeze(
        client, AMINA_SUB, HOLD_TO + dt.timedelta(days=1), HOLD_TO + dt.timedelta(days=8)
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "freeze_limit"
    assert error["details"] == {"limit": 14, "used": 11, "days_left": 3, "requested": 7}
    assert "осталось 3" in error["message"]


def test_freeze_limit_counts_across_subscriptions_of_the_year(client, sql):
    """Иначе лимит обнулялся бы продлением, то есть его бы не было вовсе."""
    assert freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).status_code == 201
    # Продлеваем после сдвинутого заморозкой срока, иначе продажа справедливо
    # упрётся в пересечение периодов.
    new_id = sell(client, AMINA, starts_on="2026-10-01").json()["subscription_id"]

    response = client.get(f"/api/v1/students/{student(AMINA)}", headers=HEADERS)
    assert response.json()["subscription"]["freeze_days_used"] == 11

    too_much = freeze(client, new_id, HOLD_TO + dt.timedelta(days=1), HOLD_TO + dt.timedelta(days=8))
    assert too_much.status_code == 422
    assert too_much.json()["error"]["code"] == "freeze_limit"


def test_hold_in_the_past_is_refused(client):
    response = freeze(client, AMINA_SUB, TODAY - dt.timedelta(days=1), TODAY + dt.timedelta(days=2))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "hold_in_past"


def test_hold_with_reversed_dates_is_refused(client):
    response = freeze(client, AMINA_SUB, HOLD_TO, HOLD_FROM)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bad_period"


def test_hold_of_other_tenant_is_404(client):
    response = freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO, headers=HEADERS_OTHER)
    assert response.status_code == 404


def test_hold_is_visible_in_the_card(client):
    hold_id = freeze(client, AMINA_SUB, "2026-08-14", "2026-08-25").json()["hold_id"]
    sub = client.get(f"/api/v1/students/{student(AMINA)}", headers=HEADERS).json()["subscription"]
    assert sub["holds"] == [
        {
            "id": hold_id,
            "from": "2026-08-14",
            "to": "2026-08-25",
            "days": 11,
            "reason": "каникулы",
        }
    ]
    assert sub["freeze_days_used"] == 11
    assert sub["freeze_days_left"] == 3
    assert sub["valid_until"] == "2026-09-11"


# ---------------------------------------------------------------------------
# Снятие заморозки
# ---------------------------------------------------------------------------


def test_release_returns_valid_until_back(client, sql):
    before = sub_row(sql, AMINA_SUB)["valid_until"]
    hold_id = freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).json()["hold_id"]

    response = unfreeze(client, AMINA_SUB, hold_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days_returned"] == 11
    assert body["valid_until_after"] == before.isoformat()
    assert sub_row(sql, AMINA_SUB)["valid_until"] == before

    # Лимит освободился вместе со сроком.
    assert body["freeze_days_left"] == 14
    assert sql.execute(
        "SELECT count(*) AS n FROM subscription_hold WHERE id = %s", (hold_id,)
    ).fetchone()["n"] == 0


def test_release_says_lessons_are_not_restored(client, sql):
    """Слоты отменённых занятий могли занять — восстанавливать их нельзя."""
    hold_id = freeze(client, AMINA_SUB, "2026-08-14", "2026-08-25").json()["hold_id"]
    body = unfreeze(client, AMINA_SUB, hold_id).json()

    assert body["lessons_cancelled"] == 3
    assert body["lessons_restored"] == 0
    assert "заново" in body["message"]

    still_cancelled = sql.execute(
        """SELECT count(*) AS n FROM lesson
           WHERE student_id = %s AND status = 'cancelled'""",
        (student(AMINA),),
    ).fetchone()
    assert still_cancelled["n"] >= 3


def test_release_of_unknown_hold_is_404(client):
    response = unfreeze(client, AMINA_SUB, "0189b0de-0000-7000-8000-0000000000ff")
    assert response.status_code == 404


def test_release_by_other_tenant_is_404(client):
    hold_id = freeze(client, AMINA_SUB, HOLD_FROM, HOLD_TO).json()["hold_id"]
    assert unfreeze(client, AMINA_SUB, hold_id, headers=HEADERS_OTHER).status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/students", "/api/v1/plans"])
def test_new_endpoints_require_tenant_header(client, path):
    assert client.get(path).status_code == 401
