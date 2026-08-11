"""Запись в журналы: движение по абонементу и аудит.

Вынесено из attendance.py, потому что этих двух функций теперь два
потребителя — отметка посещаемости и продажа абонемента с заморозкой.
Держать вторую копию рядом значило бы однажды поправить одну и забыть
другую, а журнал абонемента — источник правды об остатке: расхождение
в том, как в него пишут, разъедает всю систему.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg

from . import opdate


def add_entry(
    cur: psycopg.Cursor,
    tenant_id: str,
    subscription_id: str,
    *,
    kind: str,
    lessons_delta: int,
    makeups_delta: int,
    attendance_id: str | None = None,
    lesson_id: str | None = None,
    reason: str,
    actor_id: str | None,
    reverses_id: int | None = None,
    backdated: bool = False,
) -> int:
    """Строка в журнал абонемента. Остаток пересчитает триггер базы.

    Возвращает id записи: компенсирующая запись обязана сослаться на ту,
    которую гасит, иначе журнал перестаёт объяснять сам себя.

    `backdated` — операция внесена позже события (ADR-001). Флаг живёт
    в журнале, а не в аудите, потому что журнал — то, что администратор
    открывает при споре с родителем: без пометки восстановить, когда именно
    появилась запись, по одному `created_at` нельзя, а именно этот вопрос
    и разбирают.
    """
    columns = [
        "tenant_id", "subscription_id", "kind", "lessons_delta", "makeups_delta",
        "attendance_id", "lesson_id", "reverses_id", "reason", "created_by",
    ]
    values: list[object] = [
        tenant_id,
        subscription_id,
        kind,
        lessons_delta,
        makeups_delta,
        attendance_id,
        lesson_id,
        reverses_id,
        reason,
        actor_id,
    ]
    # Колонка приезжает миграцией 009; до неё запись идёт как раньше.
    # Подробности и срок жизни этой ветки — в opdate.marks_backdating().
    if opdate.marks_backdating(cur, "subscription_entry"):
        columns.append("backdated")
        values.append(backdated)

    placeholders = ", ".join(["%s"] * len(values))
    cur.execute(
        f"INSERT INTO subscription_entry ({', '.join(columns)}) "
        f"VALUES ({placeholders}) RETURNING id",
        values,
    )
    return int(cur.fetchone()["id"])


def audit(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str | None,
    action: str,
    entity: str,
    entity_id: str,
    payload: dict[str, Any],
) -> None:
    """Строка аудита. Пишется на всё, что двигает деньги или остаток."""
    cur.execute(
        """
        INSERT INTO audit_log (tenant_id, actor_id, action, entity, entity_id, payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (tenant_id, actor_id, action, entity, entity_id, json.dumps(payload, ensure_ascii=False)),
    )
