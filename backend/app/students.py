"""Сборка экранов «Ученики» и «Ученик».

Читающий слой. Логики здесь нет — есть склейка десятка запросов в одну
структуру, которую рисует интерфейс. Вынесено из main.py, потому что
карточка ученика собирается из восьми источников, и маршрут, в котором
это лежало бы целиком, перестал бы читаться.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from . import repository as repo
from .rules import DEFAULT_RULES, churn_risk, ledger_title


def _money(value: Any) -> int:
    """Деньги наружу — целое число тенге: копеек в прайсе музыкальной школы нет."""
    return int(Decimal(value).to_integral_value()) if value is not None else 0


def _subscription_brief(sub: Any) -> dict[str, Any] | None:
    """Абонемент для строки поиска: только то, что видно в списке."""
    if sub is None:
        return None
    return {
        "lessons_balance": sub.lessons_balance,
        "lessons_total": sub.lessons_total,
        "valid_until": sub.valid_until.isoformat(),
        "status": sub.status,
    }


def _payer(row: dict[str, Any]) -> dict[str, str] | None:
    if not row.get("payer_name"):
        return None
    return {"name": row["payer_name"], "phone": row["payer_phone"]}


def search(
    cur: psycopg.Cursor,
    query: str,
    branch_id: str | None,
    limit: int,
    only_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """`only_ids` — кого спрашивающему вообще позволено видеть (см. authz)."""
    rows = repo.search_students(cur, query, branch_id, limit, only_ids)
    out = []
    for row in rows:
        # «Сегодня» берётся в поясе филиала ученика: у школы с филиалом
        # в другом городе день начинается в другой момент, и абонемент,
        # истекающий сегодня, не должен пропадать из списка на час раньше.
        today = dt.datetime.now(ZoneInfo(row["branch_timezone"] or "Asia/Almaty")).date()
        # Действующий абонемент ищется той же функцией, что и при отметке
        # посещаемости: «действующий» обязан означать одно и то же на всех
        # экранах, иначе список показывает один остаток, а карточка другой.
        sub = repo.active_subscription(cur, str(row["id"]), today)
        out.append(
            {
                "id": str(row["id"]),
                "name": row["name"],
                "age": row["age"],
                "discipline": row["discipline"],
                "teacher": row["teacher"],
                "branch": row["branch"],
                "subscription": _subscription_brief(sub),
                "payer": _payer(row),
            }
        )
    return out


def _family_block(cur: psycopg.Cursor, family_id: str, today: dt.date) -> dict[str, Any]:
    family = repo.family_card(cur, family_id)
    members = []
    for member in repo.family_members(cur, family_id):
        sub = repo.active_subscription(cur, str(member["student_id"]), today)
        members.append(
            {
                "student_id": str(member["student_id"]),
                "name": member["name"],
                "age": member["age"],
                "discipline": member["discipline"],
                "lessons_balance": sub.lessons_balance if sub else 0,
            }
        )
    return {
        "id": str(family["id"]),
        "payer": _payer(
            {"payer_name": family["payer_name"], "payer_phone": family["payer_phone"]}
        ),
        "discount_pct": float(family["discount_pct"]),
        "members": members,
        "paid_this_month": _money(family["paid_this_month"]),
        "debt": _money(family["debt"]),
    }


def _subscription_block(
    cur: psycopg.Cursor, student_id: str, sub: dict[str, Any], today: dt.date
) -> dict[str, Any]:
    rules = dict(DEFAULT_RULES)
    rules.update(sub["rules"] or {})

    holds = [
        {
            "id": str(h["id"]),
            "from": h["from_day"].isoformat(),
            "to": h["to_day"].isoformat(),
            "days": int(h["days"]),
            "reason": h["reason"],
        }
        for h in repo.subscription_holds(cur, str(sub["id"]))
    ]

    limit = int(rules.get("freeze_days_per_year", 0))
    used = repo.freeze_days_used(cur, student_id, today.year)
    total = int(sub["lessons_total"])
    price = _money(sub["price"])

    return {
        "id": str(sub["id"]),
        "plan_name": sub["plan_name"],
        "lessons_total": total,
        "lessons_balance": int(sub["lessons_balance"]),
        "makeups_balance": int(sub["makeups_balance"]),
        "price": price,
        # Цена занятия нужна для разговора «а если пропустим?»: делим цену
        # абонемента на его занятия — другой цены урока в схеме нет.
        "lesson_price": round(price / total) if total else 0,
        "valid_from": sub["valid_from"].isoformat(),
        "valid_until": sub["valid_until"].isoformat(),
        "status": sub["status"],
        "rules": rules,
        "holds": holds,
        "freeze_days_used": used,
        "freeze_days_left": max(limit - used, 0),
    }


def _ledger_block(cur: psycopg.Cursor, student_id: str, tz_name: str) -> list[dict[str, Any]]:
    out = []
    for row in repo.student_ledger(cur, student_id, tz_name):
        title = ledger_title(row["kind"], row["mark"], row["reason"])
        if row["kind"] == "purchase" and row["payment_method"]:
            title = f"{title} · {row['payment_method']}"
        out.append(
            {
                "id": int(row["id"]),
                "date": row["day"].isoformat(),
                "kind": row["kind"],
                "title": title,
                "teacher": row["teacher"],
                "lessons_delta": int(row["lessons_delta"]),
                "makeups_delta": int(row["makeups_delta"]),
                "amount": _money(row["amount"]) if row["amount"] is not None else None,
                "reason": row["reason"],
            }
        )
    return out


def card(cur: psycopg.Cursor, student_id: str) -> dict[str, Any] | None:
    """Карточка ученика целиком — экран «Ученик» из прототипа."""
    row = repo.get_student(cur, student_id)
    if row is None:
        return None

    tz_name = row["branch_timezone"] or "Asia/Almaty"
    today = dt.datetime.now(ZoneInfo(tz_name)).date()

    active = repo.active_subscription(cur, student_id, today)
    # Показываем действующий абонемент, а если его нет — последний по сроку:
    # у ушедшего ученика карточка должна объяснять, чем всё кончилось,
    # а не быть пустой.
    sub_row = (
        repo.subscription_card(cur, active.id)
        if active is not None
        else repo.latest_subscription(cur, student_id)
    )

    facts = repo.churn_facts(cur, student_id)
    last_day = facts["last_lesson_day"]
    risk = churn_risk(
        no_shows_30d=int(facts["no_shows_30d"]),
        has_subscription=active is not None,
        lessons_balance=active.lessons_balance if active else 0,
        days_until_expiry=(active.valid_until - today).days if active else None,
        days_since_last_lesson=(today - last_day).days if last_day else None,
        months_with_school=int(facts["months_with_school"] or 0),
        subscriptions_bought=int(facts["subscriptions"]),
    )

    makeups = [
        {
            "id": str(m["id"]),
            "granted_for": m["granted_for"].isoformat() if m["granted_for"] else None,
            "expires_on": m["expires_on"].isoformat(),
            "days_left": int(m["days_left"]),
            "used_at": m["used_at"].isoformat() if m["used_at"] else None,
        }
        for m in repo.student_makeups(cur, student_id, tz_name)
    ]

    notes = [
        {
            "date": n["day"].isoformat(),
            "author": n["author"],
            "body": n["body"],
            "homework": n["homework"],
            "tags": list(n["tags"] or []),
        }
        for n in repo.student_notes(cur, student_id, tz_name)
    ]

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "age": row["age"],
        "discipline": row["discipline"],
        "teacher": row["teacher"],
        "branch": row["branch"],
        "started_on": row["started_on"].isoformat(),
        # Архивный ученик — не «занимается»: статус читает и шапка карточки,
        # и отчёт об оттоке.
        "status": "archived" if row["archived_at"] else "active",
        "family": (
            _family_block(cur, str(row["family_id"]), today) if row["family_id"] else None
        ),
        "subscription": (
            _subscription_block(cur, student_id, sub_row, today) if sub_row else None
        ),
        "makeups": makeups,
        "ledger": _ledger_block(cur, student_id, tz_name),
        "notes": notes,
        "churn_risk": risk,
    }
