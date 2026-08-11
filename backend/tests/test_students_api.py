"""Поиск учеников, карточка ученика и список тарифов.

Карточка — это экран, которым администратор отвечает родителю. Поэтому
проверяется не только форма ответа, но и то, что журнал объясняет каждое
движение человеческими словами, а не кодами вида записи.
"""
from __future__ import annotations

from conftest import HEADERS, HEADERS_OTHER, student

AMINA = "sagyndyk"
TIMUR = "sagyndyk_t"


def search(client, headers=None, **params):
    response = client.get("/api/v1/students", params=params, headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def card(client, student_key: str, headers=None):
    return client.get(f"/api/v1/students/{student(student_key)}", headers=headers or HEADERS)


def ok_card(client, student_key: str) -> dict:
    response = card(client, student_key)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------


def test_search_finds_student_by_name(client):
    found = search(client, query="Амина")
    assert [s["name"] for s in found] == ["Амина Сагындык"]
    row = found[0]
    assert row["age"] == 9
    assert row["discipline"] == "Барабаны"
    assert row["teacher"] == "Дмитрий Шарапов"
    assert row["branch"] == "Аль-Фараби 53В"
    assert row["subscription"]["lessons_balance"] == 5
    assert row["subscription"]["lessons_total"] == 8
    assert row["subscription"]["status"] == "active"


def test_search_finds_child_by_payer_name(client):
    """Администратору звонит родитель — искать он будет по имени родителя."""
    names = {s["name"] for s in search(client, query="Гульнара")}
    assert names == {"Амина Сагындык", "Тимур Сагындык"}


def test_search_finds_child_by_payer_phone_in_any_format(client):
    """Телефон диктуют как угодно: +7, 8, через дефисы. Ищем по цифрам."""
    for typed in ("+77015552418", "8 701 555 24 18", "555-24-18"):
        names = {s["name"] for s in search(client, query=typed)}
        assert names == {"Амина Сагындык", "Тимур Сагындык"}, typed


def test_search_returns_payer_contacts(client):
    payer = search(client, query="Амина")[0]["payer"]
    assert payer == {"name": "Гульнара Сагындык", "phone": "+77015552418"}


def test_search_without_subscription_gives_null(client):
    """Амир Жанат ходит без абонемента — в списке это должно быть видно."""
    row = search(client, query="Жанат")[0]
    assert row["subscription"] is None


def test_search_empty_query_lists_students(client):
    assert len(search(client, limit=5)) == 5


def test_search_of_other_tenant_sees_nothing(client):
    assert search(client, headers=HEADERS_OTHER, query="Амина") == []


# ---------------------------------------------------------------------------
# Карточка
# ---------------------------------------------------------------------------


def test_card_of_other_tenant_is_404(client):
    response = card(client, AMINA, headers=HEADERS_OTHER)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_card_family_shows_both_children_and_discount(client):
    family = ok_card(client, AMINA)["family"]
    assert family["payer"] == {"name": "Гульнара Сагындык", "phone": "+77015552418"}
    assert family["discount_pct"] == 10
    assert [(m["name"], m["age"], m["lessons_balance"]) for m in family["members"]] == [
        ("Амина", 9, 5),
        ("Тимур", 12, 3),
    ]
    # 54 000 ₸ со скидкой 10% = 48 600 ₸ на ребёнка, двое детей — 97 200 ₸.
    assert family["paid_this_month"] == 97200
    assert family["debt"] == 0


def test_card_subscription_carries_rules_of_its_own_purchase(client):
    sub = ok_card(client, AMINA)["subscription"]
    assert sub["plan_name"] == "Барабаны, 2 раза в неделю, 55 мин"
    assert (sub["lessons_total"], sub["lessons_balance"], sub["makeups_balance"]) == (8, 5, 1)
    assert (sub["price"], sub["lesson_price"]) == (54000, 6750)
    assert sub["rules"]["freeze_days_per_year"] == 14
    assert sub["freeze_days_used"] == 0
    assert sub["freeze_days_left"] == 14


def test_card_ledger_is_newest_first_and_human_readable(client):
    """Тот самый экран, которым отвечают на «куда делось занятие»."""
    ledger = ok_card(client, AMINA)["ledger"]
    assert [(row["date"], row["title"]) for row in ledger] == [
        ("2026-08-07", "Занятие проведено"),
        ("2026-08-05", "Прогул без предупреждения"),
        ("2026-08-04", "Занятие проведено"),
        ("2026-08-02", "Отмена заранее → отработка"),
        ("2026-08-01", "Оплата абонемента · kaspi"),
    ]
    assert [row["lessons_delta"] for row in ledger] == [-1, -1, -1, 0, 8]
    assert ledger[-1]["amount"] == 48600
    assert all(row["teacher"] == "Шарапов" for row in ledger[:4])
    # Сумма журнала и есть остаток — кэш абонемента обязан с ней совпадать.
    assert sum(row["lessons_delta"] for row in ledger) == 5


def test_card_makeups_have_own_expiry(client):
    makeup = ok_card(client, AMINA)["makeups"][0]
    assert makeup["granted_for"] == "2026-08-02"
    assert makeup["expires_on"] == "2026-09-01"
    assert makeup["used_at"] is None


def test_card_notes_carry_repertoire(client):
    notes = ok_card(client, AMINA)["notes"]
    assert [n["date"] for n in notes] == ["2026-08-07", "2026-08-04", "2026-07-31"]
    assert notes[0]["author"] == "Дмитрий Шарапов"
    assert "Nirvana — Smells Like Teen Spirit" in notes[0]["tags"]
    assert notes[0]["homework"]


def test_card_churn_reasons_are_checkable_facts(client):
    risk = ok_card(client, AMINA)["churn_risk"]
    assert risk["level"] in ("low", "medium", "high")
    assert 0 <= risk["score"] <= 100
    # У Амины ровно один прогул 5 августа — причина обязана быть счётной.
    assert "1 прогул за 30 дней" in risk["reasons"]


def test_card_churn_reasons_hold_only_risk_raising_facts(client):
    """Стаж — довод против оттока, и в списке причин риска ему не место."""
    risk = ok_card(client, AMINA)["churn_risk"]
    assert not any("занимается" in r for r in risk["reasons"])
    # Факт не потерян: он на своей стороне весов и снизил оценку.
    assert risk["mitigations"] == ["занимается 6 месяцев подряд"]


def test_card_without_subscription_says_so(client):
    body = ok_card(client, "zhanat")
    assert body["subscription"] is None
    assert body["family"] is None
    assert "действующего абонемента нет" in body["churn_risk"]["reasons"]


# ---------------------------------------------------------------------------
# Тарифы
# ---------------------------------------------------------------------------


def test_plans_list_has_contract_shape(client):
    response = client.get("/api/v1/plans", headers=HEADERS)
    assert response.status_code == 200, response.text
    plans = response.json()
    drums = next(p for p in plans if p["name"] == "Барабаны, 2 раза в неделю, 55 мин")
    assert drums["discipline"] == "Барабаны"
    assert drums["format"] == "individual"
    assert (drums["duration_min"], drums["lessons_count"]) == (55, 8)
    assert (drums["valid_days"], drums["price"]) == (31, 54000)


def test_plans_filter_by_format(client):
    response = client.get("/api/v1/plans", params={"format": "trial"}, headers=HEADERS)
    assert [p["format"] for p in response.json()] == ["trial"]


def test_plans_of_other_tenant_are_empty(client):
    response = client.get("/api/v1/plans", headers=HEADERS_OTHER)
    assert response.json() == []
