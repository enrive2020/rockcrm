"""Чтение из базы. SQL здесь пишется руками — намеренно.

Схема уже несёт бизнес-логику: ограничения исключения, триггеры пересчёта
остатка, политики изоляции. ORM пришлось бы учить тем же правилам заново,
и они разошлись бы при первой же миграции.

Все запросы работают внутри транзакции с выставленным app.tenant_id, поэтому
условия `WHERE tenant_id = ...` в них отсутствуют: их ставит RLS. Забытое
условие здесь не приводит к утечке — оно приводит к пустой выборке.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from .rules import SubscriptionState

# Полный набор полей занятия для расписания и карточки. Вынесен в константу,
# чтобы два запроса не разъезжались по составу колонок.
_LESSON_COLUMNS = """
  l.id,
  l.branch_id,
  l.starts_at,
  l.ends_at,
  l.kind,
  l.status,
  l.student_id,
  l.group_id,
  l.lead_id,
  l.room_id,
  l.discipline_id,
  l.overbook_ack,
  (extract(epoch FROM (l.ends_at - l.starts_at)) / 60)::int AS duration_min,
  st.id      AS teacher_id,
  st.color   AS teacher_color,
  btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher_name,
  r.name     AS room_name,
  -- Заголовок занятия: ученик, название группы или имя из заявки для пробного.
  -- nullif обязателен: concat_ws на сплошных NULL отдаёт пустую строку, а не
  -- NULL, и coalesce остановился бы на ней — у пробного по заявке заголовок
  -- приходил бы пустым, хотя имя лида есть.
  coalesce(
    nullif(btrim(concat_ws(' ', sp.first_name, sp.last_name)), ''),
    g.name,
    nullif(btrim(coalesce(ld.student_name, ld.name)), '')
  ) AS title
"""

_LESSON_JOINS = """
  FROM lesson l
  JOIN staff  st ON st.id = l.teacher_id
  JOIN person tp ON tp.id = st.person_id
  JOIN room   r  ON r.id  = l.room_id
  LEFT JOIN student     s  ON s.id  = l.student_id
  LEFT JOIN person      sp ON sp.id = s.person_id
  LEFT JOIN study_group g  ON g.id  = l.group_id
  LEFT JOIN lead        ld ON ld.id = l.lead_id
"""


def iso(moment: dt.datetime, tz: ZoneInfo) -> str:
    """Момент времени в поясе филиала: 2026-08-12T11:00:00+06:00.

    Контракт требует явное смещение. База хранит UTC, показывать нужно местное
    время школы — школы в Алматы и Астане живут в разных поясах.
    """
    return moment.astimezone(tz).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Филиалы
# ---------------------------------------------------------------------------


def list_branches(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT b.id,
               b.name,
               coalesce(b.timezone, t.timezone)      AS timezone,
               to_char(b.opens_at,  'HH24:MI')       AS opens_at,
               to_char(b.closes_at, 'HH24:MI')       AS closes_at
        FROM branch b
        JOIN tenant t ON t.id = b.tenant_id
        WHERE b.archived_at IS NULL
        ORDER BY b.name
        """
    )
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "timezone": row["timezone"],
            "opens_at": row["opens_at"],
            "closes_at": row["closes_at"],
        }
        for row in cur.fetchall()
    ]


def get_branch(cur: psycopg.Cursor, branch_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT b.id, b.name,
               coalesce(b.timezone, t.timezone) AS timezone,
               b.opens_at, b.closes_at,
               to_char(b.opens_at,  'HH24:MI')  AS opens_at_txt,
               to_char(b.closes_at, 'HH24:MI')  AS closes_at_txt
        FROM branch b
        JOIN tenant t ON t.id = b.tenant_id
        WHERE b.id = %s AND b.archived_at IS NULL
        """,
        (branch_id,),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Расписание на день
# ---------------------------------------------------------------------------


def day_bounds(day: dt.date, tz: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
    """Границы учебного дня в поясе филиала, а не в UTC.

    12 августа для школы в Алматы начинается в 18:00 UTC 11 августа. Считать
    границы в UTC значило бы показывать соседний день по краям расписания.
    """
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz)
    return start, start + dt.timedelta(days=1)


def lessons_of_day(
    cur: psycopg.Cursor, branch_id: str, day: dt.date, tz: ZoneInfo
) -> list[dict[str, Any]]:
    start, end = day_bounds(day, tz)
    cur.execute(
        f"""
        SELECT {_LESSON_COLUMNS}
        {_LESSON_JOINS}
        WHERE l.branch_id = %(branch)s
          AND l.status <> 'cancelled'          -- отменённые в расписании не показываем
          AND l.starts_at >= %(start)s
          AND l.starts_at <  %(end)s
        ORDER BY l.starts_at, teacher_name
        """,
        {"branch": branch_id, "start": start, "end": end},
    )
    return cur.fetchall()


def marks_by_lesson(cur: psycopg.Cursor, lesson_ids: list[str]) -> dict[str, str | None]:
    """Отметка занятия для расписания.

    Контракт даёт одно значение на занятие, а отметки хранятся по ученику.
    Для группы, где один пришёл, а другой прогулял, одного значения не
    существует — возвращаем null, детали видны в карточке занятия.
    """
    if not lesson_ids:
        return {}
    cur.execute(
        """
        SELECT lesson_id, array_agg(DISTINCT mark) AS marks
        FROM attendance
        WHERE lesson_id = ANY(%s) AND revoked_at IS NULL
        GROUP BY lesson_id
        """,
        (lesson_ids,),
    )
    result: dict[str, str | None] = {}
    for row in cur.fetchall():
        marks = row["marks"]
        result[str(row["lesson_id"])] = marks[0] if len(marks) == 1 else None
    return result


def disciplines_by_teacher(cur: psycopg.Cursor, staff_ids: list[str]) -> dict[str, list[str]]:
    if not staff_ids:
        return {}
    cur.execute(
        """
        SELECT sd.staff_id, d.name
        FROM staff_discipline sd
        JOIN discipline d ON d.id = sd.discipline_id
        WHERE sd.staff_id = ANY(%s) AND d.archived_at IS NULL
        ORDER BY d.sort_order, d.name
        """,
        (staff_ids,),
    )
    out: dict[str, list[str]] = {}
    for row in cur.fetchall():
        out.setdefault(str(row["staff_id"]), []).append(row["name"])
    return out


def branch_room_count(cur: psycopg.Cursor, branch_id: str) -> int:
    cur.execute(
        "SELECT count(*) AS n FROM room WHERE branch_id = %s AND archived_at IS NULL",
        (branch_id,),
    )
    return int(cur.fetchone()["n"])


def find_conflicts(lessons: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Пересечения по кабинету и преподавателю внутри дня.

    База не даёт создать такое пересечение без overbook_ack, но подтверждённый
    овербукинг в базу попадает — и обязан остаться видимым в интерфейсе,
    иначе администратор забудет, что кабинет занят дважды.
    """
    out: dict[str, list[dict[str, str]]] = {}
    for i in range(len(lessons)):
        for j in range(i + 1, len(lessons)):
            a, b = lessons[i], lessons[j]
            if a["starts_at"] >= b["ends_at"] or b["starts_at"] >= a["ends_at"]:
                continue
            for kind, key, label in (
                ("room", "room_id", "Кабинет"),
                ("teacher", "teacher_id", "Преподаватель"),
            ):
                if a[key] != b[key]:
                    continue
                name = a["room_name"] if kind == "room" else a["teacher_name"]
                message = f"{label} «{name}» занят"
                out.setdefault(str(a["id"]), []).append(
                    {"kind": kind, "with_lesson_id": str(b["id"]), "message": message}
                )
                out.setdefault(str(b["id"]), []).append(
                    {"kind": kind, "with_lesson_id": str(a["id"]), "message": message}
                )
    return out


# ---------------------------------------------------------------------------
# Карточка занятия
# ---------------------------------------------------------------------------


def get_lesson(cur: psycopg.Cursor, lesson_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"""
        SELECT {_LESSON_COLUMNS},
               coalesce(b.timezone, t.timezone) AS branch_timezone
        {_LESSON_JOINS}
        JOIN branch b ON b.id = l.branch_id
        JOIN tenant t ON t.id = b.tenant_id
        WHERE l.id = %s
        """,
        (lesson_id,),
    )
    return cur.fetchone()


def lesson_format(lesson: dict[str, Any]) -> str:
    """Формат занятия для подбора ставки преподавателя."""
    if lesson["kind"] == "trial":
        return "trial"
    if lesson["group_id"] is not None:
        return "group"
    return "individual"


def teacher_rate(
    cur: psycopg.Cursor, lesson: dict[str, Any]
) -> tuple[Decimal | None, Decimal | None]:
    """Ставка преподавателя на дату занятия.

    Более точная ставка выигрывает: заданная под конкретное направление важнее
    общей, заданная под длительность важнее «любой». Иначе школа не смогла бы
    задать «обычно 4200, но за 85 минут 6000».
    """
    cur.execute(
        """
        SELECT amount, percent
        FROM teacher_rate
        WHERE staff_id = %(staff)s
          AND format = %(format)s
          AND (discipline_id IS NULL OR discipline_id = %(disc)s)
          AND (duration_min IS NULL OR duration_min = %(dur)s)
          AND valid_from <= %(on)s
          AND (valid_until IS NULL OR valid_until >= %(on)s)
        ORDER BY (discipline_id IS NOT NULL) DESC,
                 (duration_min  IS NOT NULL) DESC,
                 valid_from DESC
        LIMIT 1
        """,
        {
            "staff": lesson["teacher_id"],
            "format": lesson_format(lesson),
            "disc": lesson["discipline_id"],
            "dur": lesson["duration_min"],
            "on": lesson["starts_at"].date(),
        },
    )
    row = cur.fetchone()
    if row is None:
        return None, None
    return row["amount"], row["percent"]


def lesson_participants(cur: psycopg.Cursor, lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Ученики занятия.

    Пробный урок по заявке участников не имеет: attendance требует student_id,
    а лид ещё не заведён учеником. Отметить такое занятие нельзя до конверсии
    заявки — это ограничение схемы, а не недосмотр.
    """
    if lesson["student_id"] is not None:
        cur.execute(
            """
            SELECT s.id AS student_id,
                   btrim(concat_ws(' ', p.first_name, p.last_name)) AS name
            FROM student s JOIN person p ON p.id = s.person_id
            WHERE s.id = %s
            """,
            (lesson["student_id"],),
        )
        return cur.fetchall()

    if lesson["group_id"] is not None:
        cur.execute(
            """
            SELECT s.id AS student_id,
                   btrim(concat_ws(' ', p.first_name, p.last_name)) AS name
            FROM group_member gm
            JOIN student s ON s.id = gm.student_id
            JOIN person  p ON p.id = s.person_id
            WHERE gm.group_id = %(g)s
              AND gm.joined_on <= %(d)s
              AND (gm.left_on IS NULL OR gm.left_on >= %(d)s)
              AND s.archived_at IS NULL
            ORDER BY name
            """,
            {"g": lesson["group_id"], "d": lesson["starts_at"].date()},
        )
        return cur.fetchall()

    return []


_SUBSCRIPTION_SQL = """
  SELECT id, lessons_total, lessons_balance, makeups_balance,
         valid_from, valid_until, status, rules, price
  FROM subscription
  WHERE student_id = %(student)s
    AND status IN ('active', 'frozen', 'exhausted')
    AND valid_from <= %(on)s
    AND valid_until >= %(on)s
  -- Сначала тот, с которого есть что списать; при равенстве — истекающий
  -- раньше, иначе он сгорит неиспользованным.
  ORDER BY (lessons_balance > 0) DESC, valid_until ASC, created_at ASC
  LIMIT 1
"""


def active_subscription(
    cur: psycopg.Cursor, student_id: str, on: dt.date, for_update: bool = False
) -> SubscriptionState | None:
    """Абонемент, по которому пойдёт занятие в указанную дату.

    for_update блокирует строку до конца транзакции: два администратора,
    отмечающие два занятия одного ученика одновременно, иначе оба увидели бы
    остаток 1 и оба списали бы, уведя абонемент в минус.
    """
    sql = _SUBSCRIPTION_SQL + (" FOR UPDATE" if for_update else "")
    cur.execute(sql, {"student": student_id, "on": on})
    row = cur.fetchone()
    if row is None:
        return None
    return SubscriptionState(
        id=str(row["id"]),
        lessons_total=int(row["lessons_total"]),
        lessons_balance=int(row["lessons_balance"]),
        makeups_balance=int(row["makeups_balance"]),
        valid_until=row["valid_until"],
        status=row["status"],
        rules=row["rules"] or {},
        price=row["price"] or Decimal(0),
    )


def attendance_by_student(cur: psycopg.Cursor, lesson_id: str) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT id, student_id, mark, marked_at, revoked_at
        FROM attendance
        WHERE lesson_id = %s AND revoked_at IS NULL
        """,
        (lesson_id,),
    )
    return {str(row["student_id"]): row for row in cur.fetchall()}


def lesson_note(cur: psycopg.Cursor, lesson_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT body, homework, tags
        FROM lesson_note
        WHERE lesson_id = %s
        ORDER BY created_at
        LIMIT 1
        """,
        (lesson_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"body": row["body"], "homework": row["homework"], "tags": list(row["tags"] or [])}
