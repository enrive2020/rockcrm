"""Экран «Деньги и ЗП»: ведомость, закрытие периода и отчёты владельца.

Читающий слой поверх `repository`, плюс единственная пишущая операция —
закрытие периода начисления. Логики расчёта здесь нет: зарплата уже
посчитана при отметке посещаемости (`rules.compute_effect` → `payroll_entry`),
а отчёт обязан показывать то, что легло в базу, а не считать второй раз.
Второй расчёт разошёлся бы с первым при первой же смене ставки, и объяснить
преподавателю, какая из двух цифр верна, стало бы нечем.

Главное решение этапа — **принадлежность начисления периоду хранится
колонкой `payroll_entry.period_id`, а не выводится из даты занятия**.
Пока месяц идёт, начисления не проштампованы, и ведомость собирается
по датам. Закрытие проставляет штамп всем непроштампованным строкам
с датой до конца периода — и с этого момента ведомость закрытого месяца
читается только по штампу. Отметка, внесённая за июль после того, как
июль закрыт, штамп не получает и уходит в августовскую ведомость
корректировкой, как требует spec.md §6.2: июльские деньги уже отданы
людям на руки, и пересчитывать их задним числом означало бы, что сверить
выплату нельзя никогда.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from . import journal, repository as repo
from .errors import ApiError
from .rules import LOW_BALANCE_THRESHOLD, date_range_ru, money, plural

# Сколько дней после окончания абонемента ученик ещё не считается ушедшим.
# Продление покупают и через неделю после окончания — школа, считающая
# такого ученика ушедшим, увидела бы отток в треть и перестала бы верить
# отчёту вовсе.
DEFAULT_GRACE_DAYS = 14

# Сколько строк отдаём в списках отчётов по умолчанию. Отчёт читают глазами;
# список на пятьсот семей — это выгрузка, а не экран.
DEFAULT_LIST_LIMIT = 50


def _money(value: Any) -> int:
    """Деньги наружу — целое число тенге: копеек в прайсе музыкальной школы нет."""
    return int(Decimal(value).to_integral_value()) if value is not None else 0


def _pct(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def today_in(tz_name: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(tz_name)).date()


def month_of(day: dt.date) -> tuple[dt.date, dt.date]:
    """Календарный месяц, в котором лежит день: (первое, последнее число)."""
    first = day.replace(day=1)
    nxt = (first + dt.timedelta(days=32)).replace(day=1)
    return first, nxt - dt.timedelta(days=1)


def resolve_period(
    since: dt.date | None, until: dt.date | None, tz_name: str
) -> tuple[dt.date, dt.date]:
    """Период отчёта. По умолчанию — текущий месяц в поясе школы.

    Пояс школы, а не сервера: «за август» у школы в Алматы начинается раньше,
    чем в UTC, и платёж первого числа иначе выпал бы из отчёта.
    """
    if since is None or until is None:
        first, last = month_of(today_in(tz_name))
        since = since or first
        until = until or last
    if since > until:
        raise ApiError(
            400, "bad_period", "Начало периода позже его конца — проверьте from и to."
        )
    return since, until


def _bounds(since: dt.date, until: dt.date, tz_name: str) -> tuple[dt.datetime, dt.datetime]:
    """Границы периода моментами времени в поясе школы, верхняя — исключительно."""
    tz = ZoneInfo(tz_name)
    start = dt.datetime.combine(since, dt.time(0, 0), tzinfo=tz)
    end = dt.datetime.combine(until + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz)
    return start, end


def _period_out(since: dt.date, until: dt.date) -> dict[str, str]:
    return {"from": since.isoformat(), "to": until.isoformat()}


# ---------------------------------------------------------------------------
# Ведомость
# ---------------------------------------------------------------------------


def _sheet_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "teacher": {
            "id": str(row["teacher_id"]),
            "name": row["name"],
            "color": row["color"],
        },
        "lessons": int(row["lessons"]),
        # Начислений больше, чем занятий: у группы из четырёх человек
        # начисление на каждого участника. Обе цифры в ведомости, потому что
        # «занятий 62» и «строк 71» — разные ответы на разные вопросы.
        "entries": int(row["entries"]),
        "rate": int(row["rate"]) if row["rate"] is not None else 0,
        "rate_varies": int(row["rate_kinds"] or 0) > 1,
        "no_shows": int(row["no_shows"]),
        "accrued": _money(row["accrued"]),
        "corrections": _money(row["corrections"]),
        "bonuses": _money(row["bonuses"]),
        "total": _money(row["total"]),
        # Сколько из суммы приехало из уже закрытых месяцев. Без этой цифры
        # ведомость не сходится с числом занятий, и преподаватель считает,
        # что ему приписали лишнее.
        "carried_over": _money(row["carried_amount"]),
        "carried_over_entries": int(row["carried_entries"]),
    }


def _closing(period: dict[str, Any] | None, tz: ZoneInfo) -> dict[str, Any]:
    if period is None or period["closed_at"] is None:
        return {"closed": False, "period_id": None, "closed_at": None, "closed_by": None}
    return {
        "closed": True,
        "period_id": str(period["id"]),
        "closed_at": repo.iso(period["closed_at"], tz),
        "closed_by": period["closed_by_name"],
    }


def sheet(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
    tz_name: str,
) -> dict[str, Any]:
    """Ведомость по преподавателям за период."""
    tz = ZoneInfo(tz_name)
    until_excl = until + dt.timedelta(days=1)
    period = repo.payroll_period_by_range(cur, since, until_excl)
    closing = _closing(period, tz)

    rows = [
        _sheet_row(r)
        for r in repo.payroll_sheet(
            cur,
            period_id=closing["period_id"],
            since=since,
            until=until_excl,
            branch_id=branch_id,
        )
    ]

    totals = {
        "teachers": len(rows),
        "lessons": sum(r["lessons"] for r in rows),
        "entries": sum(r["entries"] for r in rows),
        "no_shows": sum(r["no_shows"] for r in rows),
        "accrued": sum(r["accrued"] for r in rows),
        "corrections": sum(r["corrections"] for r in rows),
        "bonuses": sum(r["bonuses"] for r in rows),
        "total": sum(r["total"] for r in rows),
        "carried_over": sum(r["carried_over"] for r in rows),
        "carried_over_entries": sum(r["carried_over_entries"] for r in rows),
    }

    return {
        "period": _period_out(since, until),
        **closing,
        "branch_id": branch_id,
        "teachers": rows,
        "totals": totals,
        "note": _sheet_note(closing["closed"], totals),
    }


def _sheet_note(closed: bool, totals: dict[str, int]) -> str:
    """Одна фраза, объясняющая, что за цифры на экране."""
    if closed:
        return (
            f"Период закрыт: {totals['entries']} "
            f"{plural(totals['entries'], 'начисление', 'начисления', 'начислений')} "
            f"на {money(totals['total'])}. Новые отметки за эти дни уйдут "
            "в следующий период корректировкой."
        )
    if totals["carried_over_entries"]:
        n = totals["carried_over_entries"]
        return (
            f"Период открыт. В сумму вошли {n} "
            f"{plural(n, 'начисление', 'начисления', 'начислений')} "
            f"за уже закрытые месяцы на {money(totals['carried_over'])} — "
            "это корректировки, закрытые периоды не пересчитывались."
        )
    return "Период открыт: суммы ещё изменятся, пока идут отметки."


def teacher_sheet(
    cur: psycopg.Cursor,
    staff_id: str,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
    tz_name: str,
) -> dict[str, Any] | None:
    """Расшифровка ведомости по одному преподавателю — начисление за начислением."""
    teacher = repo.get_teacher(cur, staff_id)
    if teacher is None:
        return None

    tz = ZoneInfo(tz_name)
    until_excl = until + dt.timedelta(days=1)
    period = repo.payroll_period_by_range(cur, since, until_excl)
    closing = _closing(period, tz)

    # Итоги берутся тем же запросом, что и вся ведомость, а не считаются
    # по строкам расшифровки. Иначе у двух чисел появилось бы два источника,
    # и однажды шапка карточки преподавателя разошлась бы со строкой
    # в общей таблице — ровно на том, из-за чего сюда и заходят.
    rows = repo.payroll_sheet(
        cur,
        period_id=closing["period_id"],
        since=since,
        until=until_excl,
        branch_id=branch_id,
    )
    mine = next((r for r in rows if str(r["teacher_id"]) == staff_id), None)

    entries = []
    for row in repo.payroll_entries(
        cur,
        staff_id,
        period_id=closing["period_id"],
        since=since,
        until=until_excl,
        branch_id=branch_id,
    ):
        entry_tz = ZoneInfo(row["tz"] or tz_name)
        entries.append(
            {
                "id": int(row["id"]),
                "date": row["on_day"].isoformat(),
                "starts_at": (
                    repo.iso(row["starts_at"], entry_tz) if row["starts_at"] else None
                ),
                "kind": row["kind"],
                "lesson_id": str(row["lesson_id"]) if row["lesson_id"] else None,
                "student": row["student"],
                "discipline": row["discipline"],
                "branch": row["branch"],
                "duration_min": int(row["duration_min"]) if row["duration_min"] else None,
                "mark": row["mark"],
                "amount": _money(row["amount"]),
                # Снимок расчёта, как его сохранила отметка. Через полгода
                # это единственный способ объяснить сумму: ставки к тому
                # времени поменяются, а строка ведомости останется.
                "calc": row["calc"] or {},
                # Начисление за уже закрытый месяц, приехавшее сюда
                # корректировкой. Без пометки строка выглядит как ошибка даты.
                "carried_over": row["on_day"] < since,
            }
        )

    # У преподавателя без начислений строки в ведомости нет вовсе — отдаём
    # нули той же формы. Пустой объект заставил бы интерфейс различать
    # «не работал» и «не спросили», а это одно и то же состояние экрана.
    empty = dict.fromkeys(
        (
            "lessons", "entries", "rate", "no_shows", "accrued",
            "corrections", "bonuses", "total", "carried_amount", "carried_entries",
        ),
        0,
    )
    who = {"id": str(teacher["id"]), "name": teacher["name"], "color": teacher["color"]}

    return {
        "teacher": who,
        "period": _period_out(since, until),
        **closing,
        "totals": _sheet_row(
            mine
            if mine
            else {**empty, "teacher_id": who["id"], "name": who["name"],
                  "color": who["color"], "rate": None, "rate_kinds": 0}
        ),
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Периоды и закрытие
# ---------------------------------------------------------------------------


def periods(cur: psycopg.Cursor, tz_name: str, limit: int) -> list[dict[str, Any]]:
    tz = ZoneInfo(tz_name)
    return [
        {
            "id": str(row["id"]),
            # Верхняя граница в базе исключительна (daterange '[)'), наружу
            # отдаём включительно: «по 31 июля» — то, что видит человек.
            "period": _period_out(row["from_day"], row["to_day"] - dt.timedelta(days=1)),
            "closed": row["closed_at"] is not None,
            "closed_at": repo.iso(row["closed_at"], tz) if row["closed_at"] else None,
            "closed_by": row["closed_by_name"],
            "teachers": int(row["teachers"]),
            "entries": int(row["entries"]),
            "total": _money(row["total"]),
        }
        for row in repo.list_payroll_periods(cur, limit)
    ]


def close_period(
    cur: psycopg.Cursor,
    tenant_id: str,
    actor_id: str,
    since: dt.date,
    until: dt.date,
    tz_name: str,
) -> dict[str, Any]:
    """Закрыть период начисления.

    После закрытия ведомость этого периода читается по штампу `period_id`
    и больше не меняется: начисление, появившееся позже, штампа не получит
    и попадёт в следующую ведомость (spec.md §6.2).
    """
    if since > until:
        raise ApiError(
            400, "bad_period", "Начало периода позже его конца — проверьте from и to."
        )

    today = today_in(tz_name)
    if until >= today:
        # Закрывать незакончившийся месяц нельзя: занятия в нём ещё идут,
        # и штамп отрезал бы их от ведомости, в которой они обязаны быть.
        raise ApiError(
            422,
            "period_not_over",
            f"Период заканчивается {until:%d.%m.%Y} и ещё не закончился — "
            "закрывать нечего. Дождитесь его конца.",
            {"today": today.isoformat()},
        )

    until_excl = until + dt.timedelta(days=1)
    row = repo.close_payroll_period(cur, since, until_excl, actor_id)
    period_id = str(row["id"])

    stamped = repo.stamp_payroll_entries(cur, period_id, until_excl)
    total = sum(Decimal(r["amount"]) for r in stamped) if stamped else Decimal(0)
    teachers = len({str(r["staff_id"]) for r in stamped})

    journal.audit(
        cur,
        tenant_id,
        actor_id,
        "payroll.period.close",
        "payroll_period",
        period_id,
        {
            "from": since.isoformat(),
            "to": until.isoformat(),
            "entries": len(stamped),
            "teachers": teachers,
            "total": _money(total),
        },
    )

    return {
        "id": period_id,
        "period": _period_out(since, until),
        "closed": True,
        "closed_at": repo.iso(row["closed_at"], ZoneInfo(tz_name)),
        "entries": len(stamped),
        "teachers": teachers,
        "total": _money(total),
        "message": (
            f"Период {date_range_ru(since, until)} закрыт: {len(stamped)} "
            f"{plural(len(stamped), 'начисление', 'начисления', 'начислений')} "
            f"на {money(_money(total))}. Дальнейшие правки уйдут "
            "в следующий период корректировкой."
        ),
    }


# ---------------------------------------------------------------------------
# Выручка
# ---------------------------------------------------------------------------


def _breakdown(rows: list[dict[str, Any]], total: int) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row["id"]) if row.get("id") else None,
            # Платёж без абонемента ни к филиалу, ни к направлению не привязан.
            # Прятать такие деньги нельзя — сумма перестала бы сходиться.
            "name": row.get("name") or "Не распределено",
            "amount": _money(row["amount"]),
            "payments": int(row["payments"]),
            "share_pct": _pct(float(row["amount"]), total),
        }
        for row in rows
    ]


def revenue(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
    tz_name: str,
) -> dict[str, Any]:
    start, end = _bounds(since, until, tz_name)
    head = repo.revenue_total(cur, start, end, branch_id)
    total = _money(head["amount"])

    return {
        "period": _period_out(since, until),
        "branch_id": branch_id,
        "total": total,
        "payments": int(head["payments"]),
        "by_branch": _breakdown(repo.revenue_by_branch(cur, start, end, branch_id), total),
        "by_discipline": _breakdown(
            repo.revenue_by_discipline(cur, start, end, branch_id), total
        ),
        "by_month": [
            {
                "month": row["month"],
                "amount": _money(row["amount"]),
                "payments": int(row["payments"]),
            }
            for row in repo.revenue_by_month(cur, start, end, branch_id)
        ],
        "by_method": [
            {
                "method": row["method"],
                "amount": _money(row["amount"]),
                "payments": int(row["payments"]),
                "share_pct": _pct(float(row["amount"]), total),
            }
            for row in repo.revenue_by_method(cur, start, end, branch_id)
        ],
    }


# ---------------------------------------------------------------------------
# Загрузка кабинетов
# ---------------------------------------------------------------------------


def rooms(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
    tz_name: str,
) -> dict[str, Any]:
    start, end = _bounds(since, until, tz_name)
    open_days = repo.branch_open_days(cur, start, end, branch_id)

    items: list[dict[str, Any]] = []
    by_branch: dict[str, dict[str, Any]] = {}
    for row in repo.room_load(cur, start, end, branch_id):
        branch = str(row["branch_id"])
        days = open_days.get(branch, 0)
        capacity = int(row["open_minutes"]) * days
        busy = int(row["busy_minutes"])
        items.append(
            {
                "room_id": str(row["room_id"]),
                "room": row["room_name"],
                "branch_id": branch,
                "branch": row["branch_name"],
                "lessons": int(row["lessons"]),
                "busy_minutes": busy,
                "capacity_minutes": capacity,
                "utilization_pct": _pct(busy, capacity),
            }
        )
        agg = by_branch.setdefault(
            branch,
            {
                "branch_id": branch,
                "branch": row["branch_name"],
                "rooms": 0,
                "open_days": days,
                "open_minutes_per_day": int(row["open_minutes"]),
                "busy_minutes": 0,
                "capacity_minutes": 0,
            },
        )
        agg["rooms"] += 1
        agg["busy_minutes"] += busy
        agg["capacity_minutes"] += capacity

    for agg in by_branch.values():
        agg["utilization_pct"] = _pct(agg["busy_minutes"], agg["capacity_minutes"])

    # Сортируем по загрузке, а не по имени: отчёт существует ради вопроса
    # «где кончилось место», и кабинет на 96% должен быть первой строкой.
    items.sort(key=lambda r: (-r["utilization_pct"], r["branch"], r["room"]))

    busy_total = sum(r["busy_minutes"] for r in items)
    capacity_total = sum(r["capacity_minutes"] for r in items)

    return {
        "period": _period_out(since, until),
        "branch_id": branch_id,
        "utilization_pct": _pct(busy_total, capacity_total),
        "busy_minutes": busy_total,
        "capacity_minutes": capacity_total,
        # Как считалась ёмкость — цифру, по которой решают открывать филиал,
        # надо уметь проверить.
        "capacity_note": (
            "Ёмкость = часы работы филиала × дни с занятиями × кабинеты. "
            "Календаря рабочих дней в схеме нет, поэтому рабочим считается "
            "день, в который в филиале было хотя бы одно занятие."
        ),
        "branches": sorted(
            by_branch.values(), key=lambda b: -b["utilization_pct"]
        ),
        "rooms": items,
    }


# ---------------------------------------------------------------------------
# Отток
# ---------------------------------------------------------------------------


def churn(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    grace_days: int,
    limit: int,
    tz_name: str,
) -> dict[str, Any]:
    """Отток по людям (issue #25).

    Считать по абонементам нельзя: ученик с двумя направлениями давал два
    «ухода», трёхнедельная пауза между абонементами объявлялась уходом,
    а абонемент, кончающийся в конце месяца, попадал в непродлённые уже
    в середине — все три ошибки складывались в 76% там, где школу покинули
    16%. Определение живёт в repository._CHURN_SOURCE, там же разбор.
    """
    # Верхняя граница в запросе исключительна, а в API включительна:
    # «отток за август» обязан считать абонемент, закончившийся 31 августа.
    until_excl = until + dt.timedelta(days=1)
    # Дата оценки — сегодня в поясе школы, а не конец периода. Отчёт за месяц
    # открывают в середине этого месяца, и вердикт о днях, которые ещё
    # не наступили, выносить не из чего.
    asof = today_in(tz_name)

    totals = repo.churn_totals(cur, since, until_excl, grace_days, asof)
    facts = repo.churn_subscription_facts(cur, since, until_excl, grace_days)

    people = int(totals["students"])
    churned = int(totals["churned"])
    archived = int(totals["archived"])

    by_teacher = [
        {
            "teacher_id": str(row["teacher_id"]) if row["teacher_id"] else None,
            "name": row["name"] or "Без преподавателя",
            "students": int(row["students"]),
            "ended": int(row["ended"]),
            "churned": int(row["churned"]),
            "churn_pct": _pct(int(row["churned"]), int(row["students"])),
        }
        for row in repo.churn_by_teacher(cur, since, until_excl, grace_days, asof)
    ]

    students = [
        {
            "student_id": str(row["student_id"]),
            "name": row["name"],
            "discipline": row["discipline"],
            "branch": row["branch"],
            "teacher": row["teacher"],
            "teacher_id": str(row["teacher_id"]) if row["teacher_id"] else None,
            "ended_on": row["ended_on"].isoformat(),
            "archived_on": (
                row["archived_on"].isoformat() if row["archived_on"] else None
            ),
            "last_lesson_on": (
                row["last_lesson_on"].isoformat() if row["last_lesson_on"] else None
            ),
        }
        for row in repo.churned_students(
            cur, since, until_excl, grace_days, asof, limit
        )
    ]

    return {
        "period": _period_out(since, until),
        "grace_days": grace_days,
        "as_of": asof.isoformat(),
        # Люди — то, ради чего отчёт существует. `students` остаётся списком
        # ушедших, как в контракте v4, а база периода приезжает отдельным
        # числом: переименовать список значило бы сломать экран ради имени.
        "students_total": people,
        "retained": int(totals["retained"]),
        "frozen": int(totals["frozen"]),
        "pending": int(totals["pending"]),
        "churned": churned,
        "churn_pct": _pct(churned, people),
        # Вторая, независимая оценка того же числа.
        "archived": archived,
        "archived_pct": _pct(archived, people),
        # Документы: сколько абонементов закончилось и сколько продлено.
        # Отдельно от людей, потому что это разные вопросы.
        "ended": int(facts["ended"]),
        "renewed": int(facts["renewed"]),
        "by_teacher": by_teacher,
        "students": students,
        "note": _churn_note(grace_days, people, churned, archived, totals),
    }


# Насколько «отток по абонементам» и «отток по архиву» могут разойтись,
# прежде чем на это надо указать пальцем. Пять человек — это уже разговор
# с администратором о том, что кого-то забыли заархивировать.
CHURN_DISCREPANCY = 5


def _churn_note(
    grace_days: int,
    people: int,
    churned: int,
    archived: int,
    totals: dict[str, Any],
) -> str:
    """Одна фраза, объясняющая, из чего сложилось число на экране.

    Число оттока — то, по которому увольняют преподавателя и меняют цены.
    Отдать его без определения значит отдать повод для решения без повода
    его проверить.
    """
    note = (
        f"Считается по людям, а не по абонементам: ушедшим считается ученик, "
        f"у которого не осталось действующего абонемента ни по одному "
        f"направлению и не появилось нового в течение {grace_days} "
        f"{plural(grace_days, 'дня', 'дней', 'дней')}. "
        f"Учились в периоде — {people}."
    )
    pending = int(totals["pending"])
    if pending:
        note += (
            f" Ещё {pending} {plural(pending, 'ученик', 'ученика', 'учеников')} "
            "в отсрочке: их абонемент кончился, но окно продления ещё не закрыто "
            "— в отток они не попали."
        )
    frozen = int(totals["frozen"])
    if frozen:
        note += (
            f" {frozen} {plural(frozen, 'ученик', 'ученика', 'учеников')} "
            "на заморозке — это каникулы, а не уход."
        )
    if abs(churned - archived) >= CHURN_DISCREPANCY:
        note += (
            f" Внимание: по абонементам ушло {churned}, а в архив переведены "
            f"{archived}. Расхождение значит, что архив и абонементы "
            "рассказывают разные истории; верить одному числу нельзя."
        )
    return note


# ---------------------------------------------------------------------------
# Долги
# ---------------------------------------------------------------------------


def debts(cur: psycopg.Cursor, limit: int) -> dict[str, Any]:
    totals = repo.debt_totals(cur)
    return {
        "families": int(totals["families"]),
        "total": _money(totals["amount"]),
        "items": [
            {
                "family_id": str(row["id"]),
                "payer": row["payer_name"],
                "phone": row["payer_phone"],
                "students": list(row["students"] or []),
                "charged": _money(row["charged"]),
                "paid": _money(row["paid"]),
                "debt": _money(row["debt"]),
                "since_on": row["since_on"].isoformat() if row["since_on"] else None,
                "last_paid_on": (
                    row["last_paid_at"].date().isoformat() if row["last_paid_at"] else None
                ),
            }
            for row in repo.debtors(cur, limit)
        ],
        "note": (
            "Долг = начислено по абонементам семьи минус поступившие платежи. "
            "Отдельного поля «сумма к оплате» в схеме нет: она выводится "
            "из цены и скидки самого абонемента."
        ),
    }


# ---------------------------------------------------------------------------
# Шапка экрана
#
# Три карточки прототипа и блок «Требует внимания» одним запросом. Каждое
# число здесь разворачивается в свой отчёт — карточка обязана быть ссылкой,
# а не единственным местом, где цифру видно.
# ---------------------------------------------------------------------------


def _previous_period(since: dt.date, until: dt.date) -> tuple[dt.date, dt.date]:
    """С чем сравнивать выручку периода.

    Для календарного месяца — предыдущий календарный месяц, а не «те же
    31 день назад»: карточка обещает «к июлю», и июль обязан быть июлем
    целиком, а не отрезком с 31 мая по 30 июня.
    """
    first, last = month_of(since)
    if (since, until) == (first, last):
        prev_last = first - dt.timedelta(days=1)
        return prev_last.replace(day=1), prev_last
    length = (until - since).days + 1
    prev_until = since - dt.timedelta(days=1)
    return prev_until - dt.timedelta(days=length - 1), prev_until


def summary(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
    tz_name: str,
) -> dict[str, Any]:
    start, end = _bounds(since, until, tz_name)
    prev_since, prev_until = _previous_period(since, until)
    prev_start, prev_end = _bounds(prev_since, prev_until, tz_name)

    now = _money(repo.revenue_total(cur, start, end, branch_id)["amount"])
    before = _money(repo.revenue_total(cur, prev_start, prev_end, branch_id)["amount"])

    load = rooms(cur, since, until, branch_id, tz_name)
    drop = churn(cur, since, until, DEFAULT_GRACE_DAYS, 0, tz_name)
    pay = sheet(cur, since, until, branch_id, tz_name)
    owed = repo.debt_totals(cur)
    attention = repo.attention_counts(cur, LOW_BALANCE_THRESHOLD)

    # «Худший преподаватель» — тот, у кого ушло больше людей, а не у кого
    # выше процент: 33% у преподавателя с тремя учениками — это один человек,
    # и ставить такую карточку в шапку экрана значит звать владельца
    # разбираться не туда. Список уже отсортирован по числу ушедших.
    worst_teacher = next((t for t in drop["by_teacher"] if t["churned"] > 0), None)

    return {
        "period": _period_out(since, until),
        "branch_id": branch_id,
        "revenue": {
            "amount": now,
            "previous": before,
            "previous_period": _period_out(prev_since, prev_until),
            # Без прошлого периода процента не существует: «+100%» от нуля
            # ничего не значит и читается как рост.
            "change_pct": round((now - before) / before * 100) if before else None,
        },
        "rooms": {
            "utilization_pct": load["utilization_pct"],
            "busiest": load["rooms"][0] if load["rooms"] else None,
        },
        "churn": {
            "students_total": drop["students_total"],
            "ended": drop["ended"],
            "churned": drop["churned"],
            "churn_pct": drop["churn_pct"],
            # Второе число рядом с первым: карточка шапки — последнее место,
            # где можно показать оценку без её проверки.
            "archived": drop["archived"],
            "pending": drop["pending"],
            "worst_teacher": worst_teacher,
        },
        "payroll": {
            "total": pay["totals"]["total"],
            "lessons": pay["totals"]["lessons"],
            "closed": pay["closed"],
        },
        "attention": {
            "debt_families": int(owed["families"]),
            "debt_amount": _money(owed["amount"]),
            "subscriptions_running_low": int(attention["subscriptions_running_low"]),
            "makeups_open": int(attention["makeups_open"]),
            "frozen_now": int(attention["frozen_now"]),
        },
    }
