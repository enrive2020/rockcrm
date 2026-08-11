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

from . import phone
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
# Справочники для форм интерфейса
#
# Отдельные списки, а не выборка из расписания дня. Раньше интерфейс собирал
# преподавателей и кабинеты из GET /schedule, то есть из тех, у кого в этот
# день есть занятия: преподаватель с выходным в диалог назначения пробного
# не попадал вовсе, а направление в форме заявки выбрать было не из чего.
# Срез дня — не справочник, и совпадение его со справочником случайно.
#
# Архивные строки не отдаются: справочник заполняет форму, а поставить
# занятие в закрытый кабинет или уволенному преподавателю нельзя.
# ---------------------------------------------------------------------------


def list_teachers(
    cur: psycopg.Cursor, branch_id: str | None = None, discipline_id: str | None = None
) -> list[dict[str, Any]]:
    """Преподаватели школы вместе с направлениями и филиалами.

    Направления и филиалы собираются подзапросами в json, а не отдельными
    запросами по каждому преподавателю: список открывается на каждое
    открытие диалога, и три десятка лишних обращений на нём не нужны.

    Фильтры необязательны и сужают список ровно так, как спрашивает форма:
    «кто ведёт барабаны в Аль-Фараби». Условие через EXISTS, а не через JOIN,
    чтобы преподаватель с двумя направлениями не задвоился в выдаче.
    """
    cur.execute(
        """
        SELECT s.id,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name,
               p.phone,
               s.color,
               coalesce((
                 SELECT json_agg(json_build_object('id', d.id, 'name', d.name)
                                 ORDER BY d.sort_order, d.name)
                 FROM staff_discipline sd
                 JOIN discipline d ON d.id = sd.discipline_id
                 WHERE sd.staff_id = s.id AND d.archived_at IS NULL
               ), '[]'::json) AS disciplines,
               coalesce((
                 SELECT json_agg(json_build_object('id', b.id, 'name', b.name)
                                 ORDER BY b.name)
                 FROM staff_branch sb
                 JOIN branch b ON b.id = sb.branch_id
                 WHERE sb.staff_id = s.id AND b.archived_at IS NULL
               ), '[]'::json) AS branches
        FROM staff s
        JOIN person p ON p.id = s.person_id
        WHERE s.kind = 'teacher'
          AND s.archived_at IS NULL
          AND (%(branch)s::uuid IS NULL OR EXISTS (
                SELECT 1 FROM staff_branch sb
                WHERE sb.staff_id = s.id AND sb.branch_id = %(branch)s::uuid))
          AND (%(disc)s::uuid IS NULL OR EXISTS (
                SELECT 1 FROM staff_discipline sd
                WHERE sd.staff_id = s.id AND sd.discipline_id = %(disc)s::uuid))
        ORDER BY p.last_name NULLS LAST, p.first_name
        """,
        {"branch": branch_id, "disc": discipline_id},
    )
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "phone": row["phone"],
            "color": row["color"],
            "disciplines": row["disciplines"],
            "branches": row["branches"],
        }
        for row in cur.fetchall()
    ]


def list_rooms(cur: psycopg.Cursor, branch_id: str | None = None) -> list[dict[str, Any]]:
    """Кабинеты вместе с филиалом и характеристиками.

    `features` отдаётся как есть: диалог назначения пробного сверяет их
    с `discipline.room_reqs` и гасит кабинет без установки до отправки формы,
    а не ждёт 422 от сервера. Правило при этом остаётся на сервере —
    интерфейс только избавляет администратора от заведомо неверного выбора.
    """
    cur.execute(
        """
        SELECT r.id, r.name, r.capacity, r.features,
               r.branch_id, b.name AS branch_name
        FROM room r
        JOIN branch b ON b.id = r.branch_id
        WHERE r.archived_at IS NULL
          AND b.archived_at IS NULL
          AND (%(branch)s::uuid IS NULL OR r.branch_id = %(branch)s::uuid)
        ORDER BY b.name, r.name
        """,
        {"branch": branch_id},
    )
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "branch": {"id": str(row["branch_id"]), "name": row["branch_name"]},
            "capacity": int(row["capacity"]),
            "features": dict(row["features"] or {}),
        }
        for row in cur.fetchall()
    ]


def list_disciplines(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    """Направления школы с минимальным возрастом и требованиями к кабинету.

    `min_age` отдаётся здесь же, а не зашивается в интерфейс: барабаны
    с 5 лет и скрипка с 7 — настройка школы, и первая же школа, берущая
    на барабаны с четырёх, сломала бы захардкоженную таблицу порогов.
    """
    cur.execute(
        """
        SELECT id, name, min_age, room_reqs
        FROM discipline
        WHERE archived_at IS NULL
        ORDER BY sort_order, name
        """
    )
    return [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "min_age": int(row["min_age"]) if row["min_age"] is not None else None,
            "room_reqs": dict(row["room_reqs"] or {}),
        }
        for row in cur.fetchall()
    ]


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


# Актуальность абонемента проверяется НА ДАТУ ОПЕРАЦИИ, а не на сегодня
# (ADR-001, пункт 4). Решает срок действия: если дата попала между valid_from
# и valid_until, абонемент годится — даже если к сегодняшнему дню он истёк.
#
# Статуса в условии больше нет, кроме отменённого. Прежнее
# `status IN ('active','frozen','exhausted')` отсекало отметку от 15 марта,
# внесённую в апреле: триггер успевал поставить `expired`, выборка возвращала
# пусто, списывать становилось не с чего — так 2 500 отметок из 2 900 прошли
# мимо журнала (issue #15). `exhausted` пропускается по той же причине, по
# которой пропускался и раньше: с исчерпанного абонемента списывать нечего,
# и сказать об этом обязаны правила отметки с внятным текстом, а не пустая
# выборка, неотличимая от «абонемента не было вовсе».
_SUBSCRIPTION_SQL = """
  SELECT id, lessons_total, lessons_balance, makeups_balance,
         valid_from, valid_until, status, rules, price
  FROM subscription
  WHERE student_id = %(student)s
    AND status <> 'cancelled'
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
    """Абонемент, действовавший в указанную дату.

    Дата — дата операции, а не «сегодня»: абонемент, действовавший 15 марта,
    годится для отметки от 15 марта, даже если сегодня апрель (ADR-001).

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


def applied_effects(cur: psycopg.Cursor, lesson_id: str) -> dict[str, dict[str, Any]]:
    """Что действующие отметки занятия уже сделали — по журналам, а не по правилам.

    Суммы берутся из subscription_entry и payroll_entry, то есть из того, что
    реально легло в базу. Пересчитать их правилами нельзя: правила школы могли
    смениться между отметкой и открытием карточки, а проданный абонемент живёт
    по правилам момента покупки — пересчёт показал бы не ту цифру, которая
    ушла в остаток и в ведомость.

    Компенсирующие записи отмены сюда не попадают: у них attendance_id пуст
    (уникальный индекс держит одну charge на отметку), а сама отменённая
    отметка отсеяна условием revoked_at IS NULL.
    """
    cur.execute(
        """
        SELECT a.id AS attendance_id,
               a.student_id,
               a.mark,
               coalesce(sum(e.lessons_delta), 0)::int AS lessons_delta,
               coalesce(sum(e.makeups_delta), 0)::int AS makeups_delta,
               (SELECT coalesce(sum(p.amount), 0)
                  FROM payroll_entry p
                 WHERE p.attendance_id = a.id AND p.kind = 'lesson') AS teacher_amount
        FROM attendance a
        LEFT JOIN subscription_entry e
               ON e.attendance_id = a.id AND e.kind IN ('charge', 'makeup_grant')
        WHERE a.lesson_id = %s AND a.revoked_at IS NULL
        GROUP BY a.id, a.student_id, a.mark
        """,
        (lesson_id,),
    )
    return {str(row["student_id"]): row for row in cur.fetchall()}


def lesson_note(
    cur: psycopg.Cursor, lesson_id: str, student_ids: list[str] | None = None
) -> dict[str, Any] | None:
    """Заметка к занятию.

    `student_ids` ограничивает выборку теми учениками, которых спрашивающему
    видно. Заметка привязана к ученику, а групповое занятие одно на всех:
    без этого ограничения родитель читал бы в карточке ансамбля разбор
    домашней работы чужого ребёнка.
    """
    cur.execute(
        """
        SELECT body, homework, tags
        FROM lesson_note
        WHERE lesson_id = %(lesson)s
          AND (%(students)s::uuid[] IS NULL OR student_id = ANY(%(students)s::uuid[]))
        ORDER BY created_at
        LIMIT 1
        """,
        {"lesson": lesson_id, "students": student_ids},
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"body": row["body"], "homework": row["homework"], "tags": list(row["tags"] or [])}


# ---------------------------------------------------------------------------
# Ученики: поиск и карточка
# ---------------------------------------------------------------------------

# Шапка ученика — одна и та же в поиске и в карточке, поэтому колонки вынесены
# в константу: разъехавшийся возраст или направление между двумя экранами
# выглядят как баг данных, хотя это был бы баг двух почти одинаковых запросов.
_STUDENT_COLUMNS = """
  s.id,
  s.family_id,
  s.branch_id,
  s.discipline_id,
  s.started_on,
  s.archived_at,
  btrim(concat_ws(' ', p.first_name, p.last_name)) AS name,
  p.first_name,
  p.phone      AS student_phone,
  -- Возраст считает база: date_part по age() учитывает високосные годы,
  -- а деление разницы дней на 365 в коде однажды показало бы 8 вместо 9.
  CASE WHEN p.birth_date IS NULL THEN NULL
       ELSE date_part('year', age(p.birth_date))::int END AS age,
  d.name       AS discipline,
  b.name       AS branch,
  coalesce(b.timezone, t.timezone) AS branch_timezone,
  -- Преподаватель ученика: основной, если задан, иначе тот, к кому ученик
  -- реально ходит. Пустая ячейка на экране «Ученики» бесполезна, а данные
  -- для ответа есть в расписании.
  coalesce(
    btrim(concat_ws(' ', mtp.first_name, mtp.last_name)),
    lt.teacher_name
  ) AS teacher,
  btrim(concat_ws(' ', payp.first_name, payp.last_name)) AS payer_name,
  payp.phone   AS payer_phone
"""

_STUDENT_JOINS = """
  FROM student s
  JOIN person  p ON p.id = s.person_id
  JOIN tenant  t ON t.id = s.tenant_id
  LEFT JOIN discipline d ON d.id = s.discipline_id
  LEFT JOIN branch     b ON b.id = s.branch_id
  LEFT JOIN family     f ON f.id = s.family_id
  LEFT JOIN person  payp ON payp.id = f.payer_id
  LEFT JOIN staff     mt ON mt.id = s.main_teacher_id
  LEFT JOIN person   mtp ON mtp.id = mt.person_id
  LEFT JOIN LATERAL (
    SELECT btrim(concat_ws(' ', tp2.first_name, tp2.last_name)) AS teacher_name
    FROM lesson l2
    JOIN staff  st2 ON st2.id = l2.teacher_id
    JOIN person tp2 ON tp2.id = st2.person_id
    WHERE l2.student_id = s.id
    ORDER BY l2.starts_at DESC
    LIMIT 1
  ) lt ON true
"""


def search_students(
    cur: psycopg.Cursor,
    query: str,
    branch_id: str | None,
    limit: int,
    only_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Поиск для строки поиска в интерфейсе.

    Ищет и по ученику, и по плательщику: администратору звонит родитель,
    а не ребёнок, и первое, что он называет, — своё имя или свой номер.

    Телефон сравнивается по цифрам: в базе он в E.164 (+77015552418),
    а в трубке его диктуют как «8 701 555 24 18» или «555-24-18».

    `only_ids` ограничивает выборку теми учениками, кого спрашивающему видно
    (родителю — своих детей, преподавателю — своих). Ограничение стоит
    в SQL, а не поверх ответа, именно из-за `limit`: у школы на пять тысяч
    учеников двое детей одного родителя в первую страницу могли и не попасть,
    и кабинет показал бы родителю пустой список вместо его же семьи.
    """
    text = (query or "").strip()
    digits = phone.digits_of(text)
    cur.execute(
        f"""
        SELECT {_STUDENT_COLUMNS}
        {_STUDENT_JOINS}
        WHERE s.archived_at IS NULL
          AND (%(only)s::uuid[] IS NULL OR s.id = ANY(%(only)s::uuid[]))
          AND (%(branch)s::uuid IS NULL OR s.branch_id = %(branch)s::uuid)
          AND (
            %(text)s = ''
            OR btrim(concat_ws(' ', p.first_name, p.last_name))       ILIKE %(like)s
            OR btrim(concat_ws(' ', payp.first_name, payp.last_name)) ILIKE %(like)s
            OR (%(digits)s <> '' AND regexp_replace(coalesce(payp.phone, ''), '\\D', '', 'g') LIKE %(phone)s)
            OR (%(digits)s <> '' AND regexp_replace(coalesce(p.phone, ''),    '\\D', '', 'g') LIKE %(phone)s)
          )
        ORDER BY p.last_name, p.first_name
        LIMIT %(limit)s
        """,
        {
            "only": only_ids,
            "branch": branch_id,
            "text": text,
            "like": f"%{text}%",
            "digits": digits,
            "phone": f"%{digits}%",
            "limit": limit,
        },
    )
    return cur.fetchall()


def get_student(cur: psycopg.Cursor, student_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"""
        SELECT {_STUDENT_COLUMNS}
        {_STUDENT_JOINS}
        WHERE s.id = %s
        """,
        (student_id,),
    )
    return cur.fetchone()


def family_card(cur: psycopg.Cursor, family_id: str) -> dict[str, Any] | None:
    """Семья: плательщик, скидка, оплачено за календарный месяц, долг.

    Долг считается как «начислено по абонементам семьи минус оплачено»:
    абонемент можно оформить с долгом, деньги донесут позже. Отдельного
    поля «начислено» в схеме нет — сумма к оплате выводится из цены
    и скидки, зафиксированных в самом абонементе.
    """
    cur.execute(
        """
        SELECT f.id,
               f.name,
               f.discount_pct,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS payer_name,
               p.phone AS payer_phone,
               (SELECT coalesce(sum(pm.amount), 0)
                  FROM payment pm
                 WHERE pm.family_id = f.id
                   AND pm.status = 'succeeded'
                   AND pm.paid_at >= date_trunc('month', now())
                   AND pm.paid_at <  date_trunc('month', now()) + interval '1 month'
               ) AS paid_this_month,
               greatest(
                 (SELECT coalesce(sum(round(sub.price * (100 - sub.discount_pct) / 100)), 0)
                    FROM subscription sub
                   WHERE sub.family_id = f.id AND sub.status <> 'cancelled')
                 - (SELECT coalesce(sum(pm.amount), 0)
                      FROM payment pm
                     WHERE pm.family_id = f.id AND pm.status = 'succeeded'),
                 0
               ) AS debt
        FROM family f
        LEFT JOIN person p ON p.id = f.payer_id
        WHERE f.id = %s
        """,
        (family_id,),
    )
    return cur.fetchone()


def family_members(cur: psycopg.Cursor, family_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT s.id AS student_id,
               p.first_name AS name,
               CASE WHEN p.birth_date IS NULL THEN NULL
                    ELSE date_part('year', age(p.birth_date))::int END AS age,
               d.name AS discipline
        FROM student s
        JOIN person p ON p.id = s.person_id
        LEFT JOIN discipline d ON d.id = s.discipline_id
        WHERE s.family_id = %s AND s.archived_at IS NULL
        ORDER BY p.birth_date DESC NULLS LAST, p.first_name
        """,
        (family_id,),
    )
    return cur.fetchall()


def subscription_card(cur: psycopg.Cursor, subscription_id: str) -> dict[str, Any] | None:
    """Абонемент целиком, вместе с названием тарифа."""
    cur.execute(
        """
        SELECT sub.id, sub.plan_id, sub.lessons_total, sub.lessons_balance,
               sub.makeups_balance, sub.price, sub.discount_pct, sub.promo_code,
               sub.valid_from, sub.valid_until, sub.status, sub.rules,
               coalesce(pl.name, 'Абонемент') AS plan_name
        FROM subscription sub
        LEFT JOIN subscription_plan pl ON pl.id = sub.plan_id
        WHERE sub.id = %s
        """,
        (subscription_id,),
    )
    return cur.fetchone()


def latest_subscription(cur: psycopg.Cursor, student_id: str) -> dict[str, Any] | None:
    """Последний по сроку абонемент ученика — включая уже истёкший.

    Нужен карточке: у ученика без действующего абонемента экран всё равно
    обязан показать, чем он занимался и когда всё кончилось.
    """
    cur.execute(
        """
        SELECT id FROM subscription
        WHERE student_id = %s AND status <> 'cancelled'
        ORDER BY valid_until DESC, created_at DESC
        LIMIT 1
        """,
        (student_id,),
    )
    row = cur.fetchone()
    return None if row is None else subscription_card(cur, str(row["id"]))


def subscription_holds(cur: psycopg.Cursor, subscription_id: str) -> list[dict[str, Any]]:
    """Заморозки абонемента.

    period — daterange '[)': верхняя граница не входит, поэтому число
    замороженных дней равно разности границ, а не разности плюс один.
    """
    cur.execute(
        """
        SELECT id,
               lower(period) AS from_day,
               upper(period) AS to_day,
               (upper(period) - lower(period)) AS days,
               reason
        FROM subscription_hold
        WHERE subscription_id = %s
        ORDER BY lower(period)
        """,
        (subscription_id,),
    )
    return cur.fetchall()


def freeze_days_used(cur: psycopg.Cursor, student_id: str, year: int) -> int:
    """Сколько дней заморозки ученик израсходовал за календарный год.

    Считается по ученику, а не по абонементу: лимит в правилах годовой,
    а абонементов за год несколько. Считать по абонементу значило бы
    обнулять лимит каждым продлением — то есть не иметь лимита вовсе.
    """
    cur.execute(
        """
        SELECT coalesce(sum(upper(h.period * yr) - lower(h.period * yr)), 0)::int AS days
        FROM subscription_hold h
        JOIN subscription sub ON sub.id = h.subscription_id,
             LATERAL (SELECT daterange(make_date(%(y)s, 1, 1), make_date(%(y)s + 1, 1, 1)) AS yr) t
        WHERE sub.student_id = %(student)s
          AND h.period && t.yr
        """,
        {"student": student_id, "y": year},
    )
    return int(cur.fetchone()["days"])


def student_ledger(
    cur: psycopg.Cursor, student_id: str, tz_name: str, limit: int = 100
) -> list[dict[str, Any]]:
    """Движение по абонементам ученика, новые сверху.

    Берётся по всем абонементам, а не только по действующему: вопрос
    «куда делось занятие» не обрывается на дате продления.

    Дата строки — день занятия, если движение связано с занятием, иначе день
    самой записи. Оба приводятся к поясу филиала: журнал, показывающий
    занятие 12 августа как 11-е, спор не разрешает, а создаёт.
    """
    cur.execute(
        """
        SELECT e.id,
               e.kind,
               e.lessons_delta,
               e.makeups_delta,
               e.reason,
               coalesce(
                 (l.starts_at AT TIME ZONE %(tz)s)::date,
                 (e.created_at AT TIME ZONE %(tz)s)::date
               ) AS day,
               a.mark,
               tp.last_name AS teacher,
               -- Деньги показываются только у покупки: остальные движения
               -- денег не двигают, и цифра в этой колонке вводила бы в заблуждение.
               CASE WHEN e.kind = 'purchase' THEN (
                 SELECT sum(pm.amount) FROM payment pm
                 WHERE pm.subscription_id = e.subscription_id AND pm.status = 'succeeded'
               ) END AS amount,
               (SELECT string_agg(DISTINCT pm.method, ', ') FROM payment pm
                WHERE pm.subscription_id = e.subscription_id AND pm.status = 'succeeded'
                  AND e.kind = 'purchase') AS payment_method
        FROM subscription_entry e
        JOIN subscription sub ON sub.id = e.subscription_id
        LEFT JOIN lesson     l  ON l.id  = e.lesson_id
        LEFT JOIN staff      st ON st.id = l.teacher_id
        LEFT JOIN person     tp ON tp.id = st.person_id
        LEFT JOIN attendance a  ON a.id  = e.attendance_id
        WHERE sub.student_id = %(student)s
        ORDER BY e.id DESC
        LIMIT %(limit)s
        """,
        {"student": student_id, "tz": tz_name, "limit": limit},
    )
    return cur.fetchall()


def student_notes(
    cur: psycopg.Cursor, student_id: str, tz_name: str, limit: int = 20
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT (l.starts_at AT TIME ZONE %(tz)s)::date AS day,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS author,
               n.body, n.homework, n.tags
        FROM lesson_note n
        JOIN lesson l ON l.id = n.lesson_id
        LEFT JOIN staff  st ON st.id = n.author_id
        LEFT JOIN person p  ON p.id  = st.person_id
        WHERE n.student_id = %(student)s AND n.visible_to_family
        ORDER BY l.starts_at DESC
        LIMIT %(limit)s
        """,
        {"student": student_id, "tz": tz_name, "limit": limit},
    )
    return cur.fetchall()


def student_makeups(
    cur: psycopg.Cursor, student_id: str, tz_name: str
) -> list[dict[str, Any]]:
    """Отработки ученика со сроками. Сгоревшие не показываем — они уже история."""
    cur.execute(
        """
        SELECT mc.id,
               (l.starts_at AT TIME ZONE %(tz)s)::date AS granted_for,
               mc.expires_on,
               (mc.expires_on - current_date) AS days_left,
               mc.used_at
        FROM makeup_credit mc
        LEFT JOIN lesson l ON l.id = mc.granted_for
        WHERE mc.student_id = %(student)s AND mc.expired_at IS NULL
        ORDER BY mc.expires_on
        """,
        {"student": student_id, "tz": tz_name},
    )
    return cur.fetchall()


def churn_facts(cur: psycopg.Cursor, student_id: str) -> dict[str, Any]:
    """Факты для оценки риска оттока. Только измеримое, никаких оценок."""
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM attendance a
             JOIN lesson l ON l.id = a.lesson_id
            WHERE a.student_id = %(student)s
              AND a.revoked_at IS NULL
              AND a.mark = 'no_show'
              AND l.starts_at >= now() - interval '30 days') AS no_shows_30d,
          (SELECT max(l.starts_at)::date FROM lesson l
             JOIN attendance a ON a.lesson_id = l.id AND a.student_id = %(student)s
            WHERE a.revoked_at IS NULL AND a.mark IN ('came', 'late')) AS last_lesson_day,
          (SELECT count(*) FROM subscription sub
            WHERE sub.student_id = %(student)s AND sub.status <> 'cancelled') AS subscriptions,
          (SELECT date_part('year', age(s.started_on)) * 12
                + date_part('month', age(s.started_on))
             FROM student s WHERE s.id = %(student)s)::int AS months_with_school
        """,
        {"student": student_id},
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Тарифы
# ---------------------------------------------------------------------------


def list_plans(
    cur: psycopg.Cursor, discipline_id: str | None, plan_format: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT pl.id, pl.name, d.name AS discipline, pl.format,
               pl.duration_min, pl.lessons_count, pl.valid_days, pl.price
        FROM subscription_plan pl
        LEFT JOIN discipline d ON d.id = pl.discipline_id
        WHERE pl.is_active
          AND (%(disc)s::uuid IS NULL OR pl.discipline_id = %(disc)s::uuid)
          AND (%(fmt)s::text  IS NULL OR pl.format = %(fmt)s::text)
        ORDER BY d.sort_order NULLS LAST, pl.lessons_count, pl.name
        """,
        {"disc": discipline_id, "fmt": plan_format},
    )
    return cur.fetchall()


def get_plan(cur: psycopg.Cursor, plan_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, name, discipline_id, format, duration_min,
               lessons_count, valid_days, price, is_active
        FROM subscription_plan
        WHERE id = %s
        """,
        (plan_id,),
    )
    return cur.fetchone()


def tenant_timezone(cur: psycopg.Cursor, tenant_id: str) -> str:
    """Пояс школы. Отчёты считаются в нём, а не в поясе сервера."""
    cur.execute("SELECT timezone FROM tenant WHERE id = %s", (tenant_id,))
    row = cur.fetchone()
    return (row["timezone"] if row else None) or "Asia/Almaty"


def tenant_rules(cur: psycopg.Cursor, tenant_id: str) -> dict[str, Any]:
    """Правила школы на текущий момент — то, что копируется в новый абонемент."""
    cur.execute("SELECT default_rules FROM tenant WHERE id = %s", (tenant_id,))
    row = cur.fetchone()
    return dict(row["default_rules"]) if row else {}


def subscription_context(cur: psycopg.Cursor, subscription_id: str) -> dict[str, Any] | None:
    """Абонемент вместе с учеником и поясом его филиала.

    Пояс нужен заморозке: «сегодня» и границы дня у школы в Алматы и у школы
    в другом городе разные, а захардкоженное смещение однажды отменит
    занятие не того дня.
    """
    cur.execute(
        """
        SELECT sub.id, sub.student_id, sub.family_id, sub.lessons_total,
               sub.lessons_balance, sub.valid_from, sub.valid_until,
               sub.status, sub.rules,
               coalesce(b.timezone, t.timezone) AS branch_timezone
        FROM subscription sub
        JOIN student s ON s.id = sub.student_id
        JOIN tenant  t ON t.id = sub.tenant_id
        LEFT JOIN branch b ON b.id = s.branch_id
        WHERE sub.id = %s
        FOR NO KEY UPDATE OF sub
        """,
        (subscription_id,),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Заявки
# ---------------------------------------------------------------------------

# Заявка вместе со всем, что нужно и доске, и карточке. Пробный подтягивается
# латералью: у заявки может быть несколько занятий (пробный переносили),
# а на экране показывается последнее назначенное.
_LEAD_COLUMNS = """
  l.id, l.name, l.phone, l.student_name, l.student_age,
  l.stage, l.lost_reason, l.source, l.utm, l.promo_code, l.external_id,
  l.next_action_at, l.contact_attempts, l.created_at, l.updated_at,
  l.person_id, l.student_id,
  l.discipline_id, d.name AS discipline, d.min_age AS discipline_min_age,
  l.branch_id, b.name AS branch,
  coalesce(b.timezone, t.timezone) AS branch_timezone,
  l.assigned_to,
  btrim(concat_ws(' ', ap.first_name, ap.last_name)) AS assigned_name,
  -- Момент последней смены стадии. Именно от него считается «сколько ждёт»:
  -- время с создания говорило бы, что заявка висит неделю, хотя вчера
  -- по ней провели пробный.
  coalesce(
    (SELECT max(h.changed_at) FROM lead_stage_history h WHERE h.lead_id = l.id),
    l.created_at
  ) AS stage_since,
  tr.lesson_id, tr.starts_at AS trial_starts_at, tr.ends_at AS trial_ends_at,
  tr.status AS trial_status, tr.teacher_name AS trial_teacher,
  tr.room_name AS trial_room, tr.room_id AS trial_room_id,
  tr.teacher_id AS trial_teacher_id
"""

_LEAD_JOINS_HEAD = """
  FROM lead l
  JOIN tenant t ON t.id = l.tenant_id
  LEFT JOIN discipline d ON d.id = l.discipline_id
  LEFT JOIN branch     b ON b.id = l.branch_id
  LEFT JOIN app_user  au ON au.id = l.assigned_to
  LEFT JOIN person    ap ON ap.id = au.person_id
"""

# Последний назначенный пробный: колонки и таблицы у него одни, а способ
# доставки — два. Держим их рядом, чтобы правка колонки не задела только один.
_TRIAL_COLUMNS = """
    le.id AS lesson_id, le.starts_at, le.ends_at, le.status,
    le.room_id, le.teacher_id,
    r.name AS room_name,
    btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher_name
"""
_TRIAL_FROM = """
    FROM lesson le
    JOIN staff  st ON st.id = le.teacher_id
    JOIN person tp ON tp.id = st.person_id
    JOIN room   r  ON r.id  = le.room_id
"""

# Одна заявка: боковое соединение ищет её пробный по lead_id.
_TRIAL_LATERAL = f"""
  LEFT JOIN LATERAL (
    SELECT {_TRIAL_COLUMNS}
    {_TRIAL_FROM}
    WHERE le.lead_id = l.id AND le.status <> 'cancelled'
    ORDER BY le.starts_at DESC
    LIMIT 1
  ) tr ON true
"""

# Список заявок: то же самое, но один проход по занятиям на всю выборку.
# Индекса по lesson.lead_id в схеме нет, поэтому боковое соединение читает
# таблицу занятий заново для каждой строки: на доске это 124 полных прохода
# и 60 мс из 75. DISTINCT ON даёт тот же «последний пробный», прочитав
# занятия один раз. Для одной заявки такой обмен невыгоден — там остаётся
# LATERAL. Настоящее решение — частичный индекс по lead_id, но это миграция.
_TRIAL_GROUPED = f"""
  LEFT JOIN (
    SELECT DISTINCT ON (le.lead_id) le.lead_id, {_TRIAL_COLUMNS}
    {_TRIAL_FROM}
    WHERE le.lead_id IS NOT NULL AND le.status <> 'cancelled'
    ORDER BY le.lead_id, le.starts_at DESC
  ) tr ON tr.lead_id = l.id
"""

_LEAD_JOINS = _LEAD_JOINS_HEAD + _TRIAL_LATERAL


_LEAD_FILTERS = """
  (%(stage)s::text  IS NULL OR l.stage = %(stage)s::text)
  AND (%(source)s::text IS NULL OR l.source = %(source)s::text)
  AND (%(user)s::uuid   IS NULL OR l.assigned_to = %(user)s::uuid)
  AND (%(branch)s::uuid IS NULL OR l.branch_id = %(branch)s::uuid)
"""


def list_leads(
    cur: psycopg.Cursor,
    stage: str | None,
    source: str | None,
    assigned_to: str | None,
    branch_id: str | None,
    limit: int,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Страница карточек — по `limit` штук в КАЖДОЙ колонке доски.

    Ограничение обязано быть постадийным, а не общим: колонка «Отказ» на живой
    школе набирает пятьсот заявок и при общем LIMIT съела бы всю выборку,
    оставив остальные колонки пустыми. Целиком её всё равно не открывает никто.

    Номер строки считается по дешёвой выборке из одной таблицы, и только
    отобранные идентификаторы догружаются со всеми join'ами: боковое
    соединение за пробным уроком иначе отработало бы на всех 688 заявках,
    чтобы 638 из них тут же выбросить.

    Сортировка — по created_at DESC, id DESC: без второго ключа две заявки,
    заведённые в одну миллисекунду, могли бы поменяться местами между
    страницами, и одна из них не показалась бы ни на одной.
    """
    params = {
        "stage": stage, "source": source, "user": assigned_to, "branch": branch_id,
        "limit": limit, "offset": offset,
    }
    cur.execute(
        f"""
        WITH page AS (
          SELECT l.id,
                 row_number() OVER (
                   PARTITION BY l.stage ORDER BY l.created_at DESC, l.id DESC
                 ) AS rn
          FROM lead l
          WHERE {_LEAD_FILTERS}
        )
        SELECT {_LEAD_COLUMNS}
        {_LEAD_JOINS_HEAD}
        {_TRIAL_GROUPED}
        JOIN page ON page.id = l.id
        WHERE page.rn > %(offset)s AND page.rn <= %(offset)s + %(limit)s
        ORDER BY l.created_at DESC, l.id DESC
        """,
        params,
    )
    return cur.fetchall()


def lead_stage_counts(
    cur: psycopg.Cursor,
    stage: str | None,
    source: str | None,
    assigned_to: str | None,
    branch_id: str | None,
) -> dict[str, dict[str, int]]:
    """Сколько заявок в каждой колонке и сколько из них просрочено.

    Считается по всей воронке, а не по загруженной странице: счётчик колонки
    и «просрочено» в шапке — это ответ на вопрос «сколько всего», и подменять
    его размером страницы значит врать администратору тем сильнее, чем
    больше у школы заявок.

    Условие просрочки повторяет leads._flags(): напоминание в прошлом
    у незакрытой заявки. Оба места обязаны говорить одно и то же — иначе
    в шапке будет одно число, а помеченных карточек на доске другое.
    """
    cur.execute(
        f"""
        SELECT l.stage,
               count(*) AS total,
               count(*) FILTER (
                 WHERE l.next_action_at < now() AND l.stage NOT IN ('won', 'lost')
               ) AS overdue
        FROM lead l
        WHERE {_LEAD_FILTERS}
        GROUP BY l.stage
        """,
        {"stage": stage, "source": source, "user": assigned_to, "branch": branch_id},
    )
    return {
        row["stage"]: {"total": int(row["total"]), "overdue": int(row["overdue"])}
        for row in cur.fetchall()
    }


def get_lead(cur: psycopg.Cursor, lead_id: str, for_update: bool = False) -> dict[str, Any] | None:
    """Заявка целиком.

    for_update блокирует строку до конца транзакции: два администратора,
    одновременно двигающие одну заявку, иначе записали бы две строки истории
    с одной и той же исходной стадией, и отчёт увидел бы лишний переход.
    """
    lock = "FOR NO KEY UPDATE OF l" if for_update else ""
    cur.execute(
        f"""
        SELECT {_LEAD_COLUMNS}
        {_LEAD_JOINS}
        WHERE l.id = %s
        {lock}
        """,
        (lead_id,),
    )
    return cur.fetchone()


def lead_by_external_id(cur: psycopg.Cursor, external_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"""
        SELECT {_LEAD_COLUMNS}
        {_LEAD_JOINS}
        WHERE l.external_id = %s
        """,
        (external_id,),
    )
    return cur.fetchone()


def open_lead_with_phone(cur: psycopg.Cursor, phone: str) -> dict[str, Any] | None:
    """Незакрытая заявка на тот же телефон.

    Закрытые (`won`, `lost`) не считаются: тот же человек через полгода
    возвращается со вторым ребёнком, и это новая заявка, а не дубль.
    """
    cur.execute(
        """
        SELECT id, name, stage, created_at FROM lead
        WHERE phone = %s AND stage NOT IN ('won', 'lost')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (phone,),
    )
    return cur.fetchone()


def lead_history(cur: psycopg.Cursor, lead_id: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT h.from_stage, h.to_stage, h.changed_at,
               -- nullif обязателен: concat_ws на сплошных NULL отдаёт пустую
               -- строку, и заявка, принесённая ботом, показывала бы автором
               -- пустоту вместо честного «автора нет».
               nullif(btrim(concat_ws(' ', p.first_name, p.last_name)), '') AS changed_by
        FROM lead_stage_history h
        LEFT JOIN app_user u ON u.id = h.changed_by
        LEFT JOIN person   p ON p.id = u.person_id
        WHERE h.lead_id = %s
        ORDER BY h.changed_at, h.id
        """,
        (lead_id,),
    )
    return cur.fetchall()


def discipline_by_name(cur: psycopg.Cursor, name: str) -> dict[str, Any] | None:
    """Направление по названию без учёта регистра — как его прислал бот.

    Не нашли — вернём None и всё равно примем заявку: терять лид из-за
    опечатки в слове «барабаны» нельзя, направление уточнит администратор.
    """
    cur.execute(
        """
        SELECT id, name, min_age, room_reqs FROM discipline
        WHERE archived_at IS NULL AND lower(btrim(name)) = lower(btrim(%s))
        LIMIT 1
        """,
        (name,),
    )
    return cur.fetchone()


def get_discipline(cur: psycopg.Cursor, discipline_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT id, name, min_age, room_reqs FROM discipline WHERE id = %s",
        (discipline_id,),
    )
    return cur.fetchone()


def get_room(cur: psycopg.Cursor, room_id: str) -> dict[str, Any] | None:
    cur.execute(
        "SELECT id, branch_id, name, features FROM room WHERE id = %s AND archived_at IS NULL",
        (room_id,),
    )
    return cur.fetchone()


def get_staff(cur: psycopg.Cursor, staff_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT s.id, btrim(concat_ws(' ', p.first_name, p.last_name)) AS name
        FROM staff s JOIN person p ON p.id = s.person_id
        WHERE s.id = %s AND s.archived_at IS NULL
        """,
        (staff_id,),
    )
    return cur.fetchone()


def slot_occupants(
    cur: psycopg.Cursor,
    room_id: str,
    teacher_id: str,
    starts_at: dt.datetime,
    ends_at: dt.datetime,
    exclude_lesson_id: str | None = None,
) -> list[dict[str, Any]]:
    """Кто уже занимает кабинет или преподавателя в это время.

    База не даст создать пересечение без подтверждённого овербукинга, но
    сообщение «нарушено ограничение» администратору бесполезно: ему нужно
    имя того, кто в кабинете, чтобы решить — двигать время или подтверждать.
    """
    cur.execute(
        """
        SELECT le.id, le.room_id, le.teacher_id, le.starts_at, le.ends_at,
               r.name AS room_name,
               btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher_name,
               coalesce(
                 nullif(btrim(concat_ws(' ', sp.first_name, sp.last_name)), ''),
                 g.name,
                 nullif(btrim(coalesce(ld.student_name, ld.name)), '')
               ) AS title
        FROM lesson le
        JOIN room   r  ON r.id  = le.room_id
        JOIN staff  st ON st.id = le.teacher_id
        JOIN person tp ON tp.id = st.person_id
        LEFT JOIN student     s  ON s.id  = le.student_id
        LEFT JOIN person      sp ON sp.id = s.person_id
        LEFT JOIN study_group g  ON g.id  = le.group_id
        LEFT JOIN lead        ld ON ld.id = le.lead_id
        WHERE le.status <> 'cancelled'
          AND (le.room_id = %(room)s OR le.teacher_id = %(teacher)s)
          AND le.starts_at < %(ends)s
          AND le.ends_at   > %(starts)s
          AND (%(exclude)s::uuid IS NULL OR le.id <> %(exclude)s::uuid)
        ORDER BY le.starts_at
        """,
        {
            "room": room_id,
            "teacher": teacher_id,
            "starts": starts_at,
            "ends": ends_at,
            "exclude": exclude_lesson_id,
        },
    )
    return cur.fetchall()


def slot_occupants_bulk(
    cur: psycopg.Cursor, slots: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Занятость слотов сразу по многим занятиям: один запрос на всю доску.

    Тот же вопрос, что и у slot_occupants, но заданный оптом. По запросу
    на карточку доска воронки делала 395 запросов на 688 заявках — и почти
    все они были вот этим. Ответ раскладывается по lesson_id того занятия,
    ради которого спрашивали.

    Слоты приезжают через VALUES с явными приведениями типов: без них
    PostgreSQL не знает типа колонок списка значений и не может сравнить
    их с uuid и timestamptz занятия.
    """
    if not slots:
        return {}

    values = ", ".join(
        "(%s::uuid, %s::uuid, %s::uuid, %s::timestamptz, %s::timestamptz)"
        for _ in slots
    )
    params: list[Any] = []
    for slot in slots:
        params += [
            slot["lesson_id"], slot["room_id"], slot["teacher_id"],
            slot["starts_at"], slot["ends_at"],
        ]

    # Условие «тот же кабинет ИЛИ тот же преподаватель» разбито на две ветки
    # с UNION. С OR внутри соединения планировщик не может опереться ни на один
    # индекс и сводит слоты с занятиями перебором: 78 слотов × 3 528 занятий —
    # 275 тысяч проверок. Две ветки с равенством по одной колонке дают
    # хеш-соединение и на порядок меньше работы, а UNION заодно снимает
    # задвоение занятия, которое конфликтует и кабинетом, и педагогом сразу.
    cur.execute(
        f"""
        WITH slot (lesson_id, room_id, teacher_id, starts_at, ends_at) AS (
          VALUES {values}
        ),
        clash AS (
          SELECT slot.lesson_id AS for_lesson, le.id AS lesson_id
          FROM slot
          JOIN lesson le ON le.room_id = slot.room_id
           AND le.starts_at < slot.ends_at AND le.ends_at > slot.starts_at
          WHERE le.status <> 'cancelled' AND le.id <> slot.lesson_id
          UNION
          SELECT slot.lesson_id AS for_lesson, le.id AS lesson_id
          FROM slot
          JOIN lesson le ON le.teacher_id = slot.teacher_id
           AND le.starts_at < slot.ends_at AND le.ends_at > slot.starts_at
          WHERE le.status <> 'cancelled' AND le.id <> slot.lesson_id
        )
        SELECT clash.for_lesson,
               le.id, le.room_id, le.teacher_id, le.starts_at, le.ends_at,
               r.name AS room_name,
               btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher_name,
               coalesce(
                 nullif(btrim(concat_ws(' ', sp.first_name, sp.last_name)), ''),
                 g.name,
                 nullif(btrim(coalesce(ld.student_name, ld.name)), '')
               ) AS title
        FROM clash
        JOIN lesson le ON le.id = clash.lesson_id
        JOIN room   r  ON r.id  = le.room_id
        JOIN staff  st ON st.id = le.teacher_id
        JOIN person tp ON tp.id = st.person_id
        LEFT JOIN student     s  ON s.id  = le.student_id
        LEFT JOIN person      sp ON sp.id = s.person_id
        LEFT JOIN study_group g  ON g.id  = le.group_id
        LEFT JOIN lead        ld ON ld.id = le.lead_id
        ORDER BY clash.for_lesson, le.starts_at
        """,
        params,
    )

    found: dict[str, list[dict[str, Any]]] = {str(s["lesson_id"]): [] for s in slots}
    for row in cur.fetchall():
        found[str(row.pop("for_lesson"))].append(row)
    return found


def person_by_phone(cur: psycopg.Cursor, phone: str) -> dict[str, Any] | None:
    """Живая персона с этим телефоном.

    Ключевой запрос конверсии: тот же родитель приводит второго ребёнка,
    и второй профиль на него означает потерянную семейную скидку
    и разъехавшуюся историю платежей.
    """
    cur.execute(
        """
        SELECT id, first_name, last_name, phone
        FROM person
        WHERE phone = %s AND archived_at IS NULL
        LIMIT 1
        """,
        (phone,),
    )
    return cur.fetchone()


def family_of_payer(cur: psycopg.Cursor, person_id: str) -> dict[str, Any] | None:
    """Семья, где эта персона уже числится плательщиком."""
    cur.execute(
        """
        SELECT f.id, f.name, f.discount_pct
        FROM family f
        JOIN family_member fm ON fm.family_id = f.id AND fm.relation = 'payer'
        WHERE fm.person_id = %s
        LIMIT 1
        """,
        (person_id,),
    )
    return cur.fetchone()


def user_exists(cur: psycopg.Cursor, user_id: str) -> bool:
    cur.execute("SELECT 1 FROM app_user WHERE id = %s AND is_active", (user_id,))
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Кто спрашивает: учётная запись, её сотрудник, её филиалы и её ученики.
#
# Всё это читается ПОСЛЕ set_config, то есть уже под политиками изоляции:
# сессия сообщает тенанта, а дальше запрос ничем не отличается от любого
# другого — забытое условие даёт пустую выборку, а не чужую школу.
# ---------------------------------------------------------------------------


def actor_row(cur: psycopg.Cursor, user_id: str) -> dict[str, Any] | None:
    """Учётная запись вместе с её записью сотрудника.

    `staff` подбирается по виду, соответствующему роли: один человек бывает
    и преподавателем, и администратором (UNIQUE (tenant_id, person_id, kind)
    это разрешает), и взять «любую его запись» значило бы выдать преподавателю
    права администратора при первом же совмещении.
    """
    cur.execute(
        """
        SELECT u.id, u.tenant_id, u.person_id, u.role, u.is_active, u.login,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name,
               s.id AS staff_id
        FROM app_user u
        JOIN person p ON p.id = u.person_id
        LEFT JOIN staff s
               ON s.person_id = u.person_id
              AND s.archived_at IS NULL
              AND s.kind = CASE u.role
                             WHEN 'owner'   THEN 'owner'
                             WHEN 'admin'   THEN 'admin'
                             WHEN 'teacher' THEN 'teacher'
                           END
        WHERE u.id = %s
        """,
        (user_id,),
    )
    return cur.fetchone()


def staff_branch_ids(cur: psycopg.Cursor, staff_id: str) -> list[str]:
    cur.execute("SELECT branch_id FROM staff_branch WHERE staff_id = %s", (staff_id,))
    return [str(row["branch_id"]) for row in cur.fetchall()]


def guardian_student_ids(cur: psycopg.Cursor, person_id: str) -> list[str]:
    """Дети родителя — через семью, а не через «фамилия совпала».

    Плательщик учитывается двумя способами: строкой в `family_member`
    и полем `family.payer_id`. Они должны совпадать, но семья, заведённая
    конверсией заявки, может получить плательщика без строки в составе,
    и терять из-за этого доступ родителя к собственному ребёнку нельзя.
    """
    cur.execute(
        """
        SELECT DISTINCT st.id
        FROM student st
        JOIN family f ON f.id = st.family_id
        WHERE st.archived_at IS NULL
          AND (
            f.payer_id = %(person)s
            OR EXISTS (
              SELECT 1 FROM family_member fm
              WHERE fm.family_id = f.id
                AND fm.person_id = %(person)s
                AND fm.relation IN ('payer', 'guardian')
            )
          )
        """,
        {"person": person_id},
    )
    return [str(row["id"]) for row in cur.fetchall()]


def own_student_ids(cur: psycopg.Cursor, person_id: str) -> list[str]:
    """Учебные профили самой персоны: взрослый ученик платит за себя сам."""
    cur.execute(
        "SELECT id FROM student WHERE person_id = %s AND archived_at IS NULL",
        (person_id,),
    )
    return [str(row["id"]) for row in cur.fetchall()]


def teacher_student_ids(cur: psycopg.Cursor, staff_id: str) -> list[str]:
    """Ученики преподавателя: закреплённые за ним и те, кого он реально ведёт.

    Одного `main_teacher_id` мало — на замене преподаватель ведёт чужого
    ученика и обязан открыть его карточку; одних занятий мало — новый ученик
    закреплён, но первого урока ещё не было. Участники группы входят через
    `group_member`: ансамбль ведёт он же.
    """
    if not staff_id:
        return []
    cur.execute(
        """
        SELECT DISTINCT s.id
        FROM student s
        WHERE s.archived_at IS NULL
          AND (
            s.main_teacher_id = %(staff)s
            OR EXISTS (
              SELECT 1 FROM lesson l
              WHERE l.teacher_id = %(staff)s AND l.student_id = s.id
            )
            OR EXISTS (
              SELECT 1 FROM lesson l
              JOIN group_member gm ON gm.group_id = l.group_id
              WHERE l.teacher_id = %(staff)s AND gm.student_id = s.id
            )
          )
        """,
        {"staff": staff_id},
    )
    return [str(row["id"]) for row in cur.fetchall()]


def lesson_of_attendance(cur: psycopg.Cursor, attendance_id: str) -> dict[str, Any] | None:
    """Занятие, к которому относится отметка. Нужно, чтобы проверить право
    на ОТМЕНУ: отменяют не отметку саму по себе, а чужой урок."""
    cur.execute(
        """
        SELECT l.id, l.teacher_id, l.branch_id, l.student_id, l.group_id, l.starts_at
        FROM attendance a JOIN lesson l ON l.id = a.lesson_id
        WHERE a.id = %s
        """,
        (attendance_id,),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Отчёт по воронке
#
# Считается из lead_stage_history, а не из текущих стадий: заявка, дошедшая
# до покупки, в колонке «пробный проведён» уже не лежит, и по текущим стадиям
# конверсия «пробный → абонемент» вышла бы заниженной ровно на тех, кто купил.
#
# Когорта — заявки, СОЗДАННЫЕ в периоде, а переходы берутся из всей их истории.
# Иначе заявка, пришедшая 30-го и купившая 2-го, попала бы в знаменатель
# одного месяца и в числитель другого, и сумма конверсий не сошлась бы ни с чем.
# ---------------------------------------------------------------------------

_COHORT = """
  WITH cohort AS (
    SELECT l.id, l.source, l.created_at, l.lost_reason
    FROM lead l
    WHERE l.created_at >= %(from)s AND l.created_at < %(to)s
  ),
  reached AS (
    SELECT DISTINCT h.lead_id, h.to_stage, min(h.changed_at) AS at
    FROM lead_stage_history h
    JOIN cohort c ON c.id = h.lead_id
    GROUP BY h.lead_id, h.to_stage
  )
"""


def funnel_stage_counts(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime
) -> list[dict[str, Any]]:
    """Сколько заявок когорты побывало в каждой стадии."""
    cur.execute(
        _COHORT
        + """
        SELECT to_stage AS stage, count(*)::int AS entered
        FROM reached
        GROUP BY to_stage
        """,
        {"from": since, "to": until},
    )
    return cur.fetchall()


def funnel_stage_pairs(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime
) -> dict[str, set[str]]:
    """Какие стадии прошла каждая заявка когорты.

    Возвращается множество на заявку: «прошёл дальше» вычисляется в коде
    по порядку стадий из rules.STAGE_ORDER — единственному их порядку
    в системе.
    """
    cur.execute(
        _COHORT
        + """
        SELECT lead_id, to_stage FROM reached
        """,
        {"from": since, "to": until},
    )
    out: dict[str, set[str]] = {}
    for row in cur.fetchall():
        out.setdefault(str(row["lead_id"]), set()).add(row["to_stage"])
    return out


def funnel_sources(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime
) -> list[dict[str, Any]]:
    cur.execute(
        _COHORT
        + """
        SELECT c.source,
               count(*)::int AS leads,
               count(*) FILTER (
                 WHERE EXISTS (SELECT 1 FROM reached r
                               WHERE r.lead_id = c.id AND r.to_stage = 'trial_booked')
               )::int AS trials,
               count(*) FILTER (
                 WHERE EXISTS (SELECT 1 FROM reached r
                               WHERE r.lead_id = c.id AND r.to_stage = 'won')
               )::int AS won,
               avg(
                 EXTRACT(epoch FROM (
                   (SELECT r.at FROM reached r WHERE r.lead_id = c.id AND r.to_stage = 'won')
                   - c.created_at
                 )) / 86400.0
               ) AS avg_days_to_won
        FROM cohort c
        GROUP BY c.source
        ORDER BY leads DESC, c.source
        """,
        {"from": since, "to": until},
    )
    return cur.fetchall()


def funnel_lost_reasons(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT lost_reason AS reason, count(*)::int AS count
        FROM lead
        WHERE created_at >= %(from)s AND created_at < %(to)s
          AND stage = 'lost' AND lost_reason IS NOT NULL
        GROUP BY lost_reason
        ORDER BY count DESC, lost_reason
        """,
        {"from": since, "to": until},
    )
    return cur.fetchall()


def funnel_days_to_won(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime
) -> float | None:
    cur.execute(
        _COHORT
        + """
        SELECT avg(EXTRACT(epoch FROM (r.at - c.created_at)) / 86400.0) AS days
        FROM cohort c
        JOIN reached r ON r.lead_id = c.id AND r.to_stage = 'won'
        """,
        {"from": since, "to": until},
    )
    row = cur.fetchone()
    return None if row is None or row["days"] is None else float(row["days"])


# ---------------------------------------------------------------------------
# Деньги и ЗП: ведомость, периоды, отчёты
#
# Всё здесь считается в поясе ФИЛИАЛА, а не сервера: занятие в 20:00 в Алматы
# для базы в UTC приходится ещё на предыдущие сутки, и последний день месяца
# уехал бы в соседний период — ровно на том занятии, из-за которого
# преподаватель и приходит спорить о ведомости.
#
# Начисление относится к периоду ПО КОЛОНКЕ period_id, а не по дате занятия.
# Дата занятия отвечает на «когда это было», а period_id — на «в какой
# ведомости это уже выплачено», и после закрытия периода это разные вопросы:
# отметка, внесённая задним числом за июль, обязана попасть в августовскую
# выплату, потому что июльская уже отдана людям на руки (spec.md §6.2).
# ---------------------------------------------------------------------------

# День, к которому относится начисление. У начисления за занятие это день
# занятия в поясе его филиала; у бонуса и корректировки без занятия —
# день создания записи в поясе школы.
_PAYROLL_DAY = """
  coalesce(
    (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date,
    (pe.created_at AT TIME ZONE t.timezone)::date
  )
"""

# Строки начислений, попадающие в ведомость. Два режима выборки, и разница
# между ними — весь смысл закрытия периода:
#   закрытый период — ровно те строки, что были проштампованы при закрытии;
#   открытый период — всё непроштампованное с датой до конца периода, включая
#   правки за уже закрытые месяцы (они и есть «корректировка в следующем»).
_PAYROLL_SOURCE = (
    """
  WITH entry AS (
    SELECT pe.id, pe.staff_id, pe.kind, pe.amount, pe.calc, pe.period_id,
           pe.lesson_id, pe.attendance_id, pe.created_at,
           l.branch_id,
           l.starts_at,
           (extract(epoch FROM (l.ends_at - l.starts_at)) / 60)::int AS duration_min,
           coalesce(b.timezone, t.timezone) AS tz,
           -- Отметка берётся из attendance, а не из снимка calc: calc пишет
           -- приложение, и у начислений, попавших в базу мимо него (посев,
           -- перенос из старой системы), ключа mark в нём нет. Отметка —
           -- факт в своей таблице, и колонка «прогулы» обязана считаться
           -- по факту, а не по тому, насколько полон снимок.
           a.mark,
           """
    + _PAYROLL_DAY
    + """ AS on_day
    FROM payroll_entry pe
    JOIN tenant t ON t.id = pe.tenant_id
    LEFT JOIN lesson     l ON l.id = pe.lesson_id
    LEFT JOIN branch     b ON b.id = l.branch_id
    LEFT JOIN attendance a ON a.id = pe.attendance_id
    WHERE CASE
            WHEN %(period_id)s::uuid IS NOT NULL THEN pe.period_id = %(period_id)s::uuid
            ELSE pe.period_id IS NULL AND """
    + _PAYROLL_DAY
    + """ < %(to)s
          END
      AND (%(branch)s::uuid IS NULL OR l.branch_id = %(branch)s::uuid)
  )
"""
)


def payroll_sheet(
    cur: psycopg.Cursor,
    *,
    period_id: str | None,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
) -> list[dict[str, Any]]:
    """Ведомость: строка на преподавателя. `until` — граница исключительно."""
    cur.execute(
        _PAYROLL_SOURCE
        + """
        SELECT st.id AS teacher_id,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name,
               st.color,
               count(DISTINCT e.lesson_id) FILTER (WHERE e.kind = 'lesson')::int AS lessons,
               count(*) FILTER (WHERE e.kind = 'lesson')::int AS entries,
               -- Прогул и поздняя отмена оплачиваются по одному правилу
               -- (pay_teacher_on_no_show) и стоят одной колонкой: спор
               -- преподавателя с ведомостью всегда именно про неё.
               count(*) FILTER (
                 WHERE e.kind = 'lesson'
                   AND e.mark IN ('no_show', 'cancelled_late')
               )::int AS no_shows,
               -- В прототипе ставка — одна цифра, но у преподавателя их
               -- несколько (55 и 85 минут, индивидуальное и группа).
               -- Показываем самую частую и отдельным флагом честно говорим,
               -- что она не одна: «4 500 ₸» без оговорки не сходится с суммой.
               --
               -- Ставка берётся из самих начислений, а не из teacher_rate:
               -- в ведомости обязана стоять та цифра, по которой посчитали,
               -- а не та, что действует сегодня. Неоплаченные отметки
               -- (отмена заранее) в расчёт ставки не идут — это не ставка 0.
               mode() WITHIN GROUP (ORDER BY e.amount)
                 FILTER (WHERE e.kind = 'lesson' AND e.amount > 0) AS rate,
               count(DISTINCT e.amount)
                 FILTER (WHERE e.kind = 'lesson' AND e.amount > 0)::int AS rate_kinds,
               coalesce(sum(e.amount) FILTER (WHERE e.kind = 'lesson'), 0) AS accrued,
               coalesce(sum(e.amount) FILTER (WHERE e.kind IN ('correction','deduction')), 0)
                 AS corrections,
               coalesce(sum(e.amount) FILTER (WHERE e.kind = 'bonus'), 0) AS bonuses,
               coalesce(sum(e.amount), 0) AS total,
               coalesce(sum(e.amount) FILTER (WHERE e.on_day < %(from)s), 0) AS carried_amount,
               count(*) FILTER (WHERE e.on_day < %(from)s)::int AS carried_entries
        FROM entry e
        JOIN staff  st ON st.id = e.staff_id
        JOIN person p  ON p.id  = st.person_id
        GROUP BY st.id, p.first_name, p.last_name, st.color
        ORDER BY total DESC, name
        """,
        {"period_id": period_id, "from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


def payroll_entries(
    cur: psycopg.Cursor,
    staff_id: str,
    *,
    period_id: str | None,
    since: dt.date,
    until: dt.date,
    branch_id: str | None,
) -> list[dict[str, Any]]:
    """Расшифровка ведомости: строка на начисление.

    Это тот список, которым администратор отвечает преподавателю на «откуда
    сумма»: дата, ученик, отметка и снимок расчёта из `calc`. Без снимка
    через полгода объяснить сумму нечем — ставки к тому времени поменяются.
    """
    cur.execute(
        _PAYROLL_SOURCE
        + """
        SELECT e.id,
               e.kind,
               e.amount,
               e.calc,
               e.on_day,
               e.duration_min,
               e.lesson_id,
               e.starts_at,
               e.tz,
               e.mark,
               d.name  AS discipline,
               br.name AS branch,
               coalesce(
                 nullif(btrim(concat_ws(' ', sp.first_name, sp.last_name)), ''),
                 g.name
               ) AS student
        FROM entry e
        LEFT JOIN lesson      l  ON l.id  = e.lesson_id
        LEFT JOIN branch      br ON br.id = l.branch_id
        LEFT JOIN discipline  d  ON d.id  = l.discipline_id
        LEFT JOIN study_group g  ON g.id  = l.group_id
        LEFT JOIN attendance  a  ON a.id  = e.attendance_id
        LEFT JOIN student     s  ON s.id  = a.student_id
        LEFT JOIN person      sp ON sp.id = s.person_id
        WHERE e.staff_id = %(staff)s
        ORDER BY e.on_day, e.starts_at NULLS LAST, e.id
        """,
        {
            "period_id": period_id,
            "from": since,
            "to": until,
            "branch": branch_id,
            "staff": staff_id,
        },
    )
    return cur.fetchall()


def get_teacher(cur: psycopg.Cursor, staff_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT st.id, st.color,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name
        FROM staff st
        JOIN person p ON p.id = st.person_id
        WHERE st.id = %s
        """,
        (staff_id,),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Периоды начисления
# ---------------------------------------------------------------------------


def payroll_period_by_range(
    cur: psycopg.Cursor, since: dt.date, until: dt.date
) -> dict[str, Any] | None:
    """Период ровно по этим границам, если он заведён.

    Сравниваем диапазоны целиком, а не «пересекается»: ведомость за половину
    закрытого месяца — другой вопрос, и отвечать на него всем закрытым
    периодом значило бы показать суммы, которых в запрошенных днях нет.
    """
    cur.execute(
        """
        SELECT pp.id, lower(pp.period) AS from_day, upper(pp.period) AS to_day,
               pp.closed_at, pp.closed_by,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS closed_by_name
        FROM payroll_period pp
        LEFT JOIN app_user u ON u.id = pp.closed_by
        LEFT JOIN person   p ON p.id = u.person_id
        WHERE pp.period = daterange(%s, %s, '[)')
        """,
        (since, until),
    )
    return cur.fetchone()


def list_payroll_periods(cur: psycopg.Cursor, limit: int = 24) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT pp.id, lower(pp.period) AS from_day, upper(pp.period) AS to_day,
               pp.closed_at, pp.closed_by,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS closed_by_name,
               (SELECT count(*) FROM payroll_entry pe WHERE pe.period_id = pp.id)::int
                 AS entries,
               (SELECT count(DISTINCT pe.staff_id) FROM payroll_entry pe
                 WHERE pe.period_id = pp.id)::int AS teachers,
               (SELECT coalesce(sum(pe.amount), 0) FROM payroll_entry pe
                 WHERE pe.period_id = pp.id) AS total
        FROM payroll_period pp
        LEFT JOIN app_user u ON u.id = pp.closed_by
        LEFT JOIN person   p ON p.id = u.person_id
        ORDER BY lower(pp.period) DESC
        LIMIT %s
        """,
        (limit,),
    )
    return cur.fetchall()


def close_payroll_period(
    cur: psycopg.Cursor, since: dt.date, until: dt.date, actor_id: str
) -> dict[str, Any]:
    """Заводит период и сразу помечает его закрытым.

    Открытого периода в этой системе не существует, и строки под него нет:
    пока месяц идёт, начисления просто не проштампованы, а ведомость
    собирается по датам. Строка в payroll_period появляется ровно в тот
    момент, когда деньги посчитаны и отданы, — то есть при закрытии.
    Пересечение периодов ловит ограничение исключения в схеме.
    """
    cur.execute(
        """
        INSERT INTO payroll_period (tenant_id, period, closed_at, closed_by)
        VALUES (current_tenant(), daterange(%s, %s, '[)'), now(), %s)
        RETURNING id, closed_at
        """,
        (since, until, actor_id),
    )
    return cur.fetchone()


def stamp_payroll_entries(cur: psycopg.Cursor, period_id: str, until: dt.date) -> list[dict[str, Any]]:
    """Проштамповать непроштампованные начисления с датой до конца периода.

    Условие `period_id IS NULL` — единственный ключ идемпотентности. Оно же
    и есть правило «закрытый период не пересчитывается»: начисление,
    появившееся после закрытия, остаётся непроштампованным, в закрытую
    ведомость уже не попадает и уходит в следующую — корректировкой,
    как требует spec.md §6.2.
    """
    cur.execute(
        """
        WITH target AS (
          SELECT pe.id
          FROM payroll_entry pe
          JOIN tenant t ON t.id = pe.tenant_id
          LEFT JOIN lesson l ON l.id = pe.lesson_id
          LEFT JOIN branch b ON b.id = l.branch_id
          WHERE pe.period_id IS NULL AND """
        + _PAYROLL_DAY
        + """ < %(to)s
        )
        UPDATE payroll_entry pe SET period_id = %(period)s
        FROM target WHERE pe.id = target.id
        RETURNING pe.staff_id, pe.amount
        """,
        {"to": until, "period": period_id},
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Выручка
#
# Выручка = поступившие деньги (payment), а не выставленные счета. Считать её
# по проданным абонементам значило бы показать владельцу деньги, которых он
# ещё не получил: продажа с долгом — обычное дело, а решение «пора ли
# открывать третий филиал» принимается по кассе. Возврат лежит в той же
# таблице отрицательной суммой и вычитается сам.
# ---------------------------------------------------------------------------

_REVENUE_SOURCE = """
  WITH paid AS (
    SELECT pm.id, pm.amount, pm.method, pm.paid_at,
           t.timezone AS tz,
           stu.branch_id,
           coalesce(pl.discipline_id, stu.discipline_id) AS discipline_id
    FROM payment pm
    JOIN tenant t ON t.id = pm.tenant_id
    LEFT JOIN subscription      sub ON sub.id = pm.subscription_id
    LEFT JOIN student           stu ON stu.id = sub.student_id
    LEFT JOIN subscription_plan pl  ON pl.id  = sub.plan_id
    WHERE pm.status = 'succeeded'
      AND pm.paid_at >= %(from)s AND pm.paid_at < %(to)s
      AND (%(branch)s::uuid IS NULL OR stu.branch_id = %(branch)s::uuid)
  )
"""


def revenue_total(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> dict[str, Any]:
    cur.execute(
        _REVENUE_SOURCE
        + "SELECT coalesce(sum(amount), 0) AS amount, count(*)::int AS payments FROM paid",
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchone()


def revenue_by_branch(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        _REVENUE_SOURCE
        + """
        SELECT p.branch_id AS id, b.name,
               coalesce(sum(p.amount), 0) AS amount,
               count(*)::int AS payments
        FROM paid p
        LEFT JOIN branch b ON b.id = p.branch_id
        GROUP BY p.branch_id, b.name
        ORDER BY amount DESC, b.name
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


def revenue_by_discipline(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        _REVENUE_SOURCE
        + """
        SELECT p.discipline_id AS id, d.name,
               coalesce(sum(p.amount), 0) AS amount,
               count(*)::int AS payments
        FROM paid p
        LEFT JOIN discipline d ON d.id = p.discipline_id
        GROUP BY p.discipline_id, d.name
        ORDER BY amount DESC, d.name
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


def revenue_by_month(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        _REVENUE_SOURCE
        + """
        SELECT to_char(p.paid_at AT TIME ZONE p.tz, 'YYYY-MM') AS month,
               coalesce(sum(p.amount), 0) AS amount,
               count(*)::int AS payments
        FROM paid p
        GROUP BY month
        ORDER BY month
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


def revenue_by_method(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        _REVENUE_SOURCE
        + """
        SELECT p.method, coalesce(sum(p.amount), 0) AS amount, count(*)::int AS payments
        FROM paid p
        GROUP BY p.method
        ORDER BY amount DESC, p.method
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Загрузка кабинетов
#
# Ёмкость = часы работы филиала × число дней, когда филиал работал × кабинеты.
# «Дни, когда филиал работал» берутся из фактов — это дни, в которые в филиале
# было хотя бы одно занятие. Календаря рабочих дней в схеме нет, а считать
# рабочими все семь дней недели значило бы занизить загрузку школы, закрытой
# по воскресеньям, ровно на седьмую часть — и ответить «филиал не нужен» там,
# где он нужен. Число дней уходит в ответ: цифру, на которую опираются
# при открытии филиала, надо уметь проверить, а не принимать на веру.
# ---------------------------------------------------------------------------


def room_load(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT r.id AS room_id, r.name AS room_name,
               b.id AS branch_id, b.name AS branch_name,
               (extract(epoch FROM (b.closes_at - b.opens_at)) / 60)::int AS open_minutes,
               count(l.id)::int AS lessons,
               coalesce(
                 sum(extract(epoch FROM (l.ends_at - l.starts_at)) / 60), 0
               )::int AS busy_minutes
        FROM room r
        JOIN branch b ON b.id = r.branch_id
        LEFT JOIN lesson l
               ON l.room_id = r.id
              AND l.status <> 'cancelled'
              AND l.starts_at >= %(from)s AND l.starts_at < %(to)s
        WHERE r.archived_at IS NULL AND b.archived_at IS NULL
          AND (%(branch)s::uuid IS NULL OR b.id = %(branch)s::uuid)
        GROUP BY r.id, r.name, b.id, b.name, b.opens_at, b.closes_at
        ORDER BY b.name, r.name
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return cur.fetchall()


def branch_open_days(
    cur: psycopg.Cursor, since: dt.datetime, until: dt.datetime, branch_id: str | None
) -> dict[str, int]:
    """Сколько дней филиал работал: дни, в которые было хотя бы одно занятие."""
    cur.execute(
        """
        SELECT l.branch_id,
               count(DISTINCT
                 (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date
               )::int AS days
        FROM lesson l
        JOIN branch b ON b.id = l.branch_id
        JOIN tenant t ON t.id = l.tenant_id
        WHERE l.status <> 'cancelled'
          AND l.starts_at >= %(from)s AND l.starts_at < %(to)s
          AND (%(branch)s::uuid IS NULL OR l.branch_id = %(branch)s::uuid)
        GROUP BY l.branch_id
        """,
        {"from": since, "to": until, "branch": branch_id},
    )
    return {str(row["branch_id"]): int(row["days"]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Отток
#
# Отток считается ПО ЛЮДЯМ, а не по абонементам (issue #25). Прежняя версия
# считала документы, и это давало 76% там, где школу покинули 16% учеников:
#
# * ученик с гитарой и барабанами давал два «ухода» вместо одного человека;
# * пауза в три недели между абонементами объявлялась уходом, хотя ученик
#   вернулся;
# * абонемент, кончающийся 25 августа, в отчёте «за август» уже считался
#   непродлённым — хотя продлевать его ещё рано, сегодня одиннадцатое.
#
# Последнее — главное и самое неочевидное: **вердикт нельзя выносить раньше,
# чем истекла отсрочка**. Ученик, у которого абонемент кончится послезавтра,
# ушедшим быть не может ни при каком определении, а именно такие и составляли
# три четверти «оттока».
#
# Отсюда четыре состояния вместо двух:
#   retained — абонемент действует (сегодня или за пределами периода);
#   frozen   — на каникулах: заморозка действует на дату оценки;
#   pending  — абонемент кончился, но отсрочка ещё идёт: судить рано;
#   churned  — кончился, отсрочка истекла, нового нет ни по одному направлению.
#
# `last_end` берётся по ВСЕМ абонементам человека, а не по одному направлению:
# уйти из школы можно только целиком.
# ---------------------------------------------------------------------------

_CHURN_SOURCE = """
  WITH span AS (
    -- Один ряд на человека: докуда действует последний из его абонементов
    -- по любому направлению и учился ли он вообще в запрошенном периоде.
    SELECT s.student_id,
           max(s.valid_until) AS last_end,
           bool_or(s.valid_from < %(to)s AND s.valid_until >= %(from)s) AS studied
    FROM subscription s
    WHERE s.status <> 'cancelled'
    GROUP BY s.student_id
  ),
  on_hold AS (
    -- Ученик на каникулах не ушёл. Заморозка сдвигает срок абонемента вперёд,
    -- поэтому обычно человек и так попадёт в retained; проверка нужна для
    -- случая, когда заморозили уже истёкший абонемент — тогда без неё
    -- ученик, о котором школа точно знает, что он вернётся, попал бы в отток.
    SELECT DISTINCT s.student_id
    FROM subscription_hold h
    JOIN subscription s ON s.id = h.subscription_id
    WHERE h.period @> %(asof)s::date
  ),
  verdict AS (
    SELECT sp.student_id,
           sp.last_end,
           st.archived_at,
           (st.archived_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date
             AS archived_on,
           CASE
             -- Абонемент ещё действует сегодня либо продолжается за границей
             -- периода. Второе условие отсекает чужой уход: человек, ушедший
             -- в июне, не должен попасть в отчёт за март.
             WHEN sp.last_end >= %(asof)s OR sp.last_end >= %(to)s THEN 'retained'
             WHEN h.student_id IS NOT NULL THEN 'frozen'
             -- Отсрочка ещё идёт: продление покупают и через неделю после
             -- окончания, и объявлять уход, пока окно открыто, — гадание.
             WHEN sp.last_end + %(grace)s >= %(asof)s THEN 'pending'
             ELSE 'churned'
           END AS state
    FROM span sp
    JOIN student st ON st.id = sp.student_id
    JOIN tenant  t  ON t.id  = st.tenant_id
    LEFT JOIN branch  b ON b.id = st.branch_id
    LEFT JOIN on_hold h ON h.student_id = sp.student_id
    WHERE sp.studied
  )
"""


def _churn_params(
    since: dt.date, until: dt.date, grace_days: int, asof: dt.date
) -> dict[str, Any]:
    return {
        "from": since,
        "to": until,
        "grace": dt.timedelta(days=grace_days),
        "asof": asof,
    }


def churn_totals(
    cur: psycopg.Cursor, since: dt.date, until: dt.date, grace_days: int, asof: dt.date
) -> dict[str, Any]:
    """Люди: сколько учились, сколько осталось, сколько ушло.

    `archived` считается по `student.archived_at` — это вторая, независимая
    оценка того же числа. Расходиться они будут всегда: администратор
    архивирует ученика тогда, когда узнал об уходе, а не когда кончился
    абонемент. Показывать одно число вместо двух значит выдать оценку
    за факт.
    """
    cur.execute(
        _CHURN_SOURCE
        + """
        SELECT count(*)::int AS students,
               count(*) FILTER (WHERE state = 'retained')::int AS retained,
               count(*) FILTER (WHERE state = 'frozen')::int   AS frozen,
               count(*) FILTER (WHERE state = 'pending')::int  AS pending,
               count(*) FILTER (WHERE state = 'churned')::int  AS churned,
               count(*) FILTER (
                 WHERE archived_on >= %(from)s AND archived_on < %(to)s
               )::int AS archived
        FROM verdict
        """,
        _churn_params(since, until, grace_days, asof),
    )
    return cur.fetchone()


def churn_subscription_facts(
    cur: psycopg.Cursor, since: dt.date, until: dt.date, grace_days: int
) -> dict[str, Any]:
    """Те же события в документах: сколько абонементов кончилось и продлено.

    Держится отдельно от людей намеренно. Число закончившихся абонементов —
    проверяемый факт, по нему сходится касса; число ушедших людей — вывод
    из этих фактов. Смешивать их в одной цифре и значило бы получить 76%
    оттока при 16% реальных.
    """
    cur.execute(
        """
        SELECT count(*)::int AS ended,
               count(*) FILTER (WHERE renewed)::int AS renewed
        FROM (
          SELECT EXISTS (
                   SELECT 1 FROM subscription n
                   WHERE n.student_id = s.student_id
                     AND n.id <> s.id
                     AND n.status <> 'cancelled'
                     AND n.valid_until > s.valid_until
                     AND n.valid_from <= s.valid_until + %(grace)s
                 ) AS renewed
          FROM subscription s
          WHERE s.status <> 'cancelled'
            AND s.valid_until >= %(from)s AND s.valid_until < %(to)s
        ) x
        """,
        {"from": since, "to": until, "grace": dt.timedelta(days=grace_days)},
    )
    return cur.fetchone()


def churn_by_teacher(
    cur: psycopg.Cursor, since: dt.date, until: dt.date, grace_days: int, asof: dt.date
) -> list[dict[str, Any]]:
    """Разрез по преподавателям — тоже по людям.

    `students` (сколько человек у преподавателя учились в периоде) отдаётся
    рядом с процентом намеренно: 33% у преподавателя с тремя учениками
    и 33% у преподавателя с сорока — разные новости, и по первому числу
    никого увольнять нельзя.
    """
    cur.execute(
        _CHURN_SOURCE
        + """
        SELECT st.id AS teacher_id,
               btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS name,
               count(*)::int AS students,
               count(*) FILTER (WHERE v.state <> 'retained')::int AS ended,
               count(*) FILTER (WHERE v.state = 'churned')::int AS churned
        FROM verdict v
        JOIN student s   ON s.id  = v.student_id
        LEFT JOIN staff  st ON st.id = s.main_teacher_id
        LEFT JOIN person tp ON tp.id = st.person_id
        GROUP BY st.id, tp.first_name, tp.last_name
        ORDER BY churned DESC, students DESC, name
        """,
        _churn_params(since, until, grace_days, asof),
    )
    return cur.fetchall()


def churned_students(
    cur: psycopg.Cursor,
    since: dt.date,
    until: dt.date,
    grace_days: int,
    asof: dt.date,
    limit: int,
) -> list[dict[str, Any]]:
    cur.execute(
        _CHURN_SOURCE
        + """
        SELECT v.student_id, v.last_end AS ended_on,
               v.archived_on,
               btrim(concat_ws(' ', p.first_name, p.last_name)) AS name,
               d.name  AS discipline,
               b.name  AS branch,
               st.id   AS teacher_id,
               btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher,
               (SELECT max((l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date)
                  FROM attendance a
                  JOIN lesson l ON l.id = a.lesson_id
                 WHERE a.student_id = v.student_id AND a.revoked_at IS NULL
               ) AS last_lesson_on
        FROM verdict v
        JOIN student s  ON s.id = v.student_id
        JOIN person  p  ON p.id = s.person_id
        JOIN tenant  t  ON t.id = s.tenant_id
        LEFT JOIN branch     b  ON b.id  = s.branch_id
        LEFT JOIN discipline d  ON d.id  = s.discipline_id
        LEFT JOIN staff      st ON st.id = s.main_teacher_id
        LEFT JOIN person     tp ON tp.id = st.person_id
        WHERE v.state = 'churned'
        ORDER BY v.last_end DESC, name
        LIMIT %(limit)s
        """,
        _churn_params(since, until, grace_days, asof) | {"limit": limit},
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Долги
#
# Формула та же, что в карточке семьи (family_card): начислено по абонементам
# семьи минус поступившие платежи. Держать её в двух местах разной нельзя —
# родитель, которому в карточке назвали одну сумму, а в отчёте другую,
# перестанет верить обеим.
# ---------------------------------------------------------------------------

# Начисленное и оплаченное считаются одним проходом по каждой таблице, а
# не подзапросом на семью: коррелированный вариант повторял его сто семьдесят
# раз и стоил 180 мс на полугодовых данных — на экране, который открывают
# каждое утро, это заметно.
_DEBT_SQL = """
  charged AS (
    SELECT sub.family_id,
           sum(round(sub.price * (100 - sub.discount_pct) / 100)) AS charged,
           min(sub.valid_from) AS since_on
    FROM subscription sub
    WHERE sub.status <> 'cancelled' AND sub.family_id IS NOT NULL
    GROUP BY sub.family_id
  ),
  settled AS (
    SELECT pm.family_id, sum(pm.amount) AS paid, max(pm.paid_at) AS last_paid_at
    FROM payment pm
    WHERE pm.status = 'succeeded' AND pm.family_id IS NOT NULL
    GROUP BY pm.family_id
  ),
  owed AS (
    SELECT f.id,
           btrim(concat_ws(' ', p.first_name, p.last_name)) AS payer_name,
           p.phone AS payer_phone,
           coalesce(c.charged, 0) AS charged,
           coalesce(s.paid, 0)    AS paid,
           s.last_paid_at,
           c.since_on
    FROM family f
    LEFT JOIN person  p ON p.id = f.payer_id
    LEFT JOIN charged c ON c.family_id = f.id
    LEFT JOIN settled s ON s.family_id = f.id
  )
"""


def debtors(cur: psycopg.Cursor, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        "WITH " + _DEBT_SQL + """
        SELECT o.*, (o.charged - o.paid) AS debt,
               (SELECT array_agg(btrim(concat_ws(' ', sp.first_name, sp.last_name))
                                 ORDER BY sp.first_name)
                  FROM student s
                  JOIN person sp ON sp.id = s.person_id
                 WHERE s.family_id = o.id AND s.archived_at IS NULL) AS students
        FROM owed o
        WHERE o.charged - o.paid > 0
        ORDER BY debt DESC, payer_name
        LIMIT %s
        """,
        (limit,),
    )
    return cur.fetchall()


def debt_totals(cur: psycopg.Cursor) -> dict[str, Any]:
    cur.execute(
        "WITH " + _DEBT_SQL + """
        SELECT count(*)::int AS families,
               coalesce(sum(o.charged - o.paid), 0) AS amount
        FROM owed o WHERE o.charged - o.paid > 0
        """
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Блок «Требует внимания» — счётные факты, а не оценки. Каждое число
# администратор обязан уметь развернуть в список и позвонить по нему.
# ---------------------------------------------------------------------------


def attention_counts(cur: psycopg.Cursor, low_threshold: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
          -- Исчерпанный абонемент со ещё живым сроком — тот же повод звонить,
          -- что и остаток 2: ученик придёт на занятие, а списывать нечего.
          -- Истёкшие сюда не идут: это уже отток, а не «пора продлить».
          (SELECT count(*) FROM subscription
            WHERE status IN ('active', 'exhausted')
              AND lessons_balance <= %(low)s
              AND valid_until >= current_date)::int
            AS subscriptions_running_low,
          (SELECT count(*) FROM makeup_credit
            WHERE used_at IS NULL AND expired_at IS NULL)::int AS makeups_open,
          (SELECT count(*) FROM subscription_hold
            WHERE period @> current_date)::int AS frozen_now
        """,
        {"low": low_threshold},
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Кабинет родителя (issue #5)
#
# Запросы здесь НАМЕРЕННО не переиспользуют выборки административных экранов,
# хотя половина колонок совпадает. Карточка ученика собирается вычитанием —
# берём всё и убираем лишнее, — и ошибка в ней молчит: добавленное завтра поле
# уедет родителю, если про него забыли. Здесь состав перечислен поимённо,
# и ошибка выглядит как отсутствующее поле, а не как утечка долга семьи,
# риска оттока или ставки преподавателя.
#
# Все `student_ids` приезжают из сессии (authz.visible_student_ids), а не
# из параметров запроса: идентификатор в пути только сверяется со списком.
# ---------------------------------------------------------------------------

# Ученик глазами семьи. Ни семьи, ни плательщика, ни скидки, ни долга,
# ни риска оттока — их нет даже в выборке, чтобы неоткуда было просочиться.
_FAMILY_STUDENT_COLUMNS = """
  s.id,
  s.started_on,
  p.first_name AS name,
  btrim(concat_ws(' ', p.first_name, p.last_name)) AS full_name,
  CASE WHEN p.birth_date IS NULL THEN NULL
       ELSE date_part('year', age(p.birth_date))::int END AS age,
  d.name AS discipline,
  b.name    AS branch_name,
  b.address AS branch_address,
  coalesce(b.timezone, t.timezone) AS timezone,
  -- Имя преподавателя и только имя: ставка сюда не выбирается вовсе.
  coalesce(
    nullif(btrim(concat_ws(' ', mtp.first_name, mtp.last_name)), ''),
    lt.teacher_name
  ) AS teacher
"""

_FAMILY_STUDENT_JOINS = """
  FROM student s
  JOIN person  p ON p.id = s.person_id
  JOIN tenant  t ON t.id = s.tenant_id
  LEFT JOIN discipline d ON d.id = s.discipline_id
  LEFT JOIN branch     b ON b.id = s.branch_id
  LEFT JOIN staff    mt  ON mt.id  = s.main_teacher_id
  LEFT JOIN person   mtp ON mtp.id = mt.person_id
  LEFT JOIN LATERAL (
    SELECT btrim(concat_ws(' ', tp2.first_name, tp2.last_name)) AS teacher_name
    FROM lesson l2
    JOIN staff  st2 ON st2.id = l2.teacher_id
    JOIN person tp2 ON tp2.id = st2.person_id
    WHERE l2.student_id = s.id
    ORDER BY l2.starts_at DESC
    LIMIT 1
  ) lt ON true
"""


def family_children(cur: psycopg.Cursor, student_ids: list[str]) -> list[dict[str, Any]]:
    """Шапки своих детей. Порядок — по имени: список короткий и постоянный."""
    if not student_ids:
        return []
    cur.execute(
        f"""
        SELECT {_FAMILY_STUDENT_COLUMNS}
        {_FAMILY_STUDENT_JOINS}
        WHERE s.id = ANY(%(ids)s::uuid[])
        ORDER BY p.first_name, p.last_name
        """,
        {"ids": student_ids},
    )
    return cur.fetchall()


def family_child(cur: psycopg.Cursor, student_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"""
        SELECT {_FAMILY_STUDENT_COLUMNS}
        {_FAMILY_STUDENT_JOINS}
        WHERE s.id = %(id)s
        """,
        {"id": student_id},
    )
    return cur.fetchone()


# Занятие глазами семьи. Заголовка чужого ученика здесь нет: имя приезжает
# из подзапроса по СВОИМ детям, а групповое занятие даёт строку на своего
# ребёнка и ни одной на соседей по ансамблю.
_FAMILY_LESSON_SOURCE = """
  FROM lesson l
  JOIN branch b ON b.id = l.branch_id
  JOIN tenant t ON t.id = l.tenant_id
  JOIN room   r ON r.id = l.room_id
  JOIN staff  st ON st.id = l.teacher_id
  JOIN person tp ON tp.id = st.person_id
  -- LATERAL идёт последним: он ссылается на b и t, а SQL позволяет боковому
  -- подзапросу видеть только то, что стоит в FROM выше него.
  JOIN LATERAL (
    SELECT s.id AS student_id,
           btrim(concat_ws(' ', p.first_name, p.last_name)) AS student_name
    FROM student s
    JOIN person p ON p.id = s.person_id
    WHERE s.id = ANY(%(ids)s::uuid[])
      AND (
        l.student_id = s.id
        OR EXISTS (
          SELECT 1 FROM group_member gm
          WHERE gm.group_id = l.group_id
            AND gm.student_id = s.id
            AND gm.joined_on <= (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date
            AND (gm.left_on IS NULL
                 OR gm.left_on >= (l.starts_at AT TIME ZONE coalesce(b.timezone, t.timezone))::date)
        )
      )
  ) ch ON true
"""


def family_lessons(
    cur: psycopg.Cursor,
    student_ids: list[str],
    start: dt.datetime,
    end: dt.datetime,
) -> list[dict[str, Any]]:
    """Занятия всех своих детей за период, включая отменённые.

    Отменённые не прячутся, в отличие от расписания филиала: родитель должен
    увидеть, что урок отменён, а не обнаружить пустоту в сетке и приехать зря.
    """
    if not student_ids:
        return []
    cur.execute(
        f"""
        SELECT l.id AS lesson_id,
               ch.student_id,
               ch.student_name,
               l.starts_at,
               l.ends_at,
               (extract(epoch FROM (l.ends_at - l.starts_at)) / 60)::int AS duration_min,
               l.kind,
               l.status,
               btrim(concat_ws(' ', tp.first_name, tp.last_name)) AS teacher,
               b.name AS branch,
               r.name AS room,
               coalesce(b.timezone, t.timezone) AS timezone,
               a.mark AS attendance
        {_FAMILY_LESSON_SOURCE}
        LEFT JOIN attendance a
               ON a.lesson_id = l.id AND a.student_id = ch.student_id
              AND a.revoked_at IS NULL
        WHERE l.starts_at >= %(start)s AND l.starts_at < %(end)s
        ORDER BY l.starts_at, ch.student_name
        """,
        {"ids": student_ids, "start": start, "end": end},
    )
    return cur.fetchall()


def family_next_lessons(
    cur: psycopg.Cursor, student_ids: list[str], now: dt.datetime
) -> dict[str, dict[str, Any]]:
    """Ближайшее занятие каждого ребёнка. Главный вопрос кабинета — «когда вести»."""
    if not student_ids:
        return {}
    cur.execute(
        f"""
        SELECT DISTINCT ON (ch.student_id)
               ch.student_id,
               l.id AS lesson_id,
               l.starts_at,
               r.name AS room,
               coalesce(b.timezone, t.timezone) AS timezone
        {_FAMILY_LESSON_SOURCE}
        WHERE l.status = 'planned' AND l.starts_at >= %(now)s
        ORDER BY ch.student_id, l.starts_at
        """,
        {"ids": student_ids, "now": now},
    )
    return {str(row["student_id"]): row for row in cur.fetchall()}


def family_lesson(
    cur: psycopg.Cursor, lesson_id: str, student_ids: list[str]
) -> dict[str, Any] | None:
    """Занятие, на котором стоит один из своих детей. Чужое даёт None -> 404.

    Ограничение по детям стоит в этом же запросе, а не проверкой после:
    занятие чужого ребёнка обязано быть неотличимо от несуществующего.
    """
    if not student_ids:
        return None
    cur.execute(
        f"""
        SELECT l.id AS lesson_id,
               ch.student_id,
               ch.student_name,
               l.starts_at,
               l.status,
               coalesce(b.timezone, t.timezone) AS timezone,
               a.mark AS attendance
        {_FAMILY_LESSON_SOURCE}
        LEFT JOIN attendance a
               ON a.lesson_id = l.id AND a.student_id = ch.student_id
              AND a.revoked_at IS NULL
        WHERE l.id = %(lesson)s
        LIMIT 1
        """,
        {"ids": student_ids, "lesson": lesson_id},
    )
    return cur.fetchone()


def family_history(
    cur: psycopg.Cursor, student_id: str, tz_name: str, limit: int = 50
) -> list[dict[str, Any]]:
    """История: движение по абонементу вместе с занятием и заметкой.

    Условие видимости заметки стоит В УСЛОВИИ СОЕДИНЕНИЯ. Фильтр поверх ответа
    («выбрали всё, потом убрали невидимое») забудут при первом же добавлении
    поля — а цена забывчивости здесь в том, что внутренняя пометка
    преподавателя уезжает родителю.
    """
    cur.execute(
        """
        SELECT e.id,
               e.kind,
               e.lessons_delta,
               e.makeups_delta,
               e.reason,
               coalesce(
                 (l.starts_at AT TIME ZONE %(tz)s)::date,
                 (e.created_at AT TIME ZONE %(tz)s)::date
               ) AS day,
               l.starts_at,
               a.mark AS attendance,
               n.body     AS note_body,
               n.homework AS note_homework,
               n.tags     AS note_tags
        FROM subscription_entry e
        JOIN subscription sub ON sub.id = e.subscription_id
        LEFT JOIN lesson     l ON l.id = e.lesson_id
        LEFT JOIN attendance a ON a.id = e.attendance_id
        LEFT JOIN lesson_note n
               ON n.lesson_id  = e.lesson_id
              AND n.student_id = %(student)s
              AND n.visible_to_family
        WHERE sub.student_id = %(student)s
        ORDER BY e.id DESC
        LIMIT %(limit)s
        """,
        {"student": student_id, "tz": tz_name, "limit": limit},
    )
    return cur.fetchall()


def family_repertoire(cur: psycopg.Cursor, student_id: str) -> list[str]:
    """Репертуар из тегов заметок — то, ради чего родитель платит.

    Только видимые семье заметки: тег внутренней пометки — такая же
    внутренняя пометка, только в одно слово.
    """
    cur.execute(
        """
        SELECT t.tag, max(n.created_at) AS last_seen
        FROM lesson_note n, unnest(n.tags) AS t(tag)
        WHERE n.student_id = %(student)s AND n.visible_to_family
        GROUP BY t.tag
        ORDER BY last_seen DESC, t.tag
        """,
        {"student": student_id},
    )
    return [row["tag"] for row in cur.fetchall()]


def family_progress(cur: psycopg.Cursor, student_id: str) -> dict[str, Any]:
    """Счётные факты о занятиях: сколько посещено и сколько месяцев в школе."""
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM attendance a
            WHERE a.student_id = %(student)s
              AND a.revoked_at IS NULL
              AND a.mark IN ('came', 'late'))::int AS lessons_attended,
          (SELECT (date_part('year', age(s.started_on)) * 12
                 + date_part('month', age(s.started_on)))::int
             FROM student s WHERE s.id = %(student)s) AS months
        """,
        {"student": student_id},
    )
    return cur.fetchone()


def family_makeups(cur: psycopg.Cursor, student_id: str) -> list[dict[str, Any]]:
    """Отработки, которыми ещё можно воспользоваться.

    Потраченные и сгоревшие не показываем: родителю нужен ответ на вопрос
    «сколько занятий мне ещё должны», а не история движения отработок.
    """
    cur.execute(
        """
        SELECT mc.expires_on, (mc.expires_on - current_date) AS days_left
        FROM makeup_credit mc
        WHERE mc.student_id = %(student)s
          AND mc.used_at IS NULL
          AND mc.expired_at IS NULL
        ORDER BY mc.expires_on
        """,
        {"student": student_id},
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Заявки из кабинета: перенос занятия и продление абонемента
# ---------------------------------------------------------------------------

# Заявка целиком — для очереди администратора и для ответа на создание.
_REQUEST_COLUMNS = """
  fr.id,
  fr.kind,
  fr.status,
  fr.reason,
  fr.preferred,
  fr.answer,
  fr.answered_at,
  fr.created_at,
  fr.student_id,
  fr.lesson_id,
  fr.moved_to,
  btrim(concat_ws(' ', sp.first_name, sp.last_name)) AS student_name,
  -- nullif обязателен: concat_ws на сплошных NULL отдаёт пустую строку,
  -- и «заявку рассмотрел никто» приезжало бы в интерфейс как имя без букв.
  nullif(btrim(concat_ws(' ', rp.first_name, rp.last_name)), '') AS requested_by_name,
  rp.phone AS requested_by_phone,
  nullif(btrim(concat_ws(' ', ap.first_name, ap.last_name)), '') AS answered_by_name,
  l.starts_at AS lesson_starts_at,
  l.status    AS lesson_status,
  r.name      AS lesson_room,
  b.name      AS lesson_branch,
  nullif(btrim(concat_ws(' ', tp.first_name, tp.last_name)), '') AS lesson_teacher,
  coalesce(b.timezone, t.timezone) AS timezone
"""

_REQUEST_JOINS = """
  FROM family_request fr
  JOIN tenant  t  ON t.id = fr.tenant_id
  JOIN student s  ON s.id = fr.student_id
  JOIN person  sp ON sp.id = s.person_id
  LEFT JOIN app_user au  ON au.id = fr.requested_by
  LEFT JOIN person   rp  ON rp.id = au.person_id
  LEFT JOIN app_user ans ON ans.id = fr.answered_by
  LEFT JOIN person   ap  ON ap.id = ans.person_id
  LEFT JOIN lesson l  ON l.id = fr.lesson_id
  LEFT JOIN room   r  ON r.id = l.room_id
  LEFT JOIN branch b  ON b.id = l.branch_id
  LEFT JOIN staff  st ON st.id = l.teacher_id
  LEFT JOIN person tp ON tp.id = st.person_id
"""


def create_family_request(
    cur: psycopg.Cursor,
    tenant_id: str,
    *,
    kind: str,
    requested_by: str,
    student_id: str,
    lesson_id: str | None,
    reason: str | None,
    preferred: list[dt.datetime],
) -> str:
    cur.execute(
        """
        INSERT INTO family_request
          (tenant_id, kind, requested_by, student_id, lesson_id, reason, preferred)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (tenant_id, kind, requested_by, student_id, lesson_id, reason, preferred),
    )
    return str(cur.fetchone()["id"])


def get_family_request(cur: psycopg.Cursor, request_id: str) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT {_REQUEST_COLUMNS} {_REQUEST_JOINS} WHERE fr.id = %s",
        (request_id,),
    )
    return cur.fetchone()


def list_family_requests(
    cur: psycopg.Cursor,
    status: str | None,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Очередь администратора. Нерассмотренные — старые сверху: заявка, которую
    не заметили три дня, важнее пришедшей минуту назад."""
    cur.execute(
        f"""
        SELECT {_REQUEST_COLUMNS} {_REQUEST_JOINS}
        WHERE (%(status)s::text IS NULL OR fr.status = %(status)s)
          AND (%(kind)s::text   IS NULL OR fr.kind   = %(kind)s)
        ORDER BY (fr.status = 'pending') DESC,
                 CASE WHEN fr.status = 'pending' THEN fr.created_at END ASC,
                 fr.created_at DESC
        LIMIT %(limit)s
        """,
        {"status": status, "kind": kind, "limit": limit},
    )
    return cur.fetchall()


def family_request_counts(cur: psycopg.Cursor) -> dict[str, Any]:
    cur.execute(
        """
        SELECT count(*) FILTER (WHERE status = 'pending')::int AS pending,
               count(*) FILTER (WHERE status = 'pending'
                                  AND kind = 'reschedule')::int AS reschedule,
               count(*) FILTER (WHERE status = 'pending'
                                  AND kind = 'renew')::int      AS renew
        FROM family_request
        """
    )
    return cur.fetchone()


def pending_family_request(
    cur: psycopg.Cursor, kind: str, student_id: str, lesson_id: str | None
) -> dict[str, Any] | None:
    """Открытая заявка того же вида. Повторная почти всегда двойное нажатие."""
    cur.execute(
        """
        SELECT id, created_at FROM family_request
        WHERE status = 'pending'
          AND kind = %(kind)s
          AND student_id = %(student)s
          AND (%(lesson)s::uuid IS NULL OR lesson_id = %(lesson)s::uuid)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"kind": kind, "student": student_id, "lesson": lesson_id},
    )
    return cur.fetchone()


def open_reschedule_requests(
    cur: psycopg.Cursor, lesson_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Открытые заявки на перенос по списку занятий — для расписания кабинета."""
    if not lesson_ids:
        return {}
    cur.execute(
        """
        SELECT id, lesson_id, status, created_at
        FROM family_request
        WHERE kind = 'reschedule'
          AND status = 'pending'
          AND lesson_id = ANY(%s::uuid[])
        """,
        (lesson_ids,),
    )
    return {str(row["lesson_id"]): row for row in cur.fetchall()}


def answer_family_request(
    cur: psycopg.Cursor,
    request_id: str,
    *,
    status: str,
    answer: str | None,
    moved_to: str | None,
    answered_by: str,
) -> bool:
    """Рассмотрение заявки. Условие `status = 'pending'` и есть ключ гонки:
    два администратора, открывшие очередь одновременно, не ответят дважды."""
    cur.execute(
        """
        UPDATE family_request
        SET status = %(status)s,
            answer = %(answer)s,
            moved_to = %(moved_to)s,
            answered_by = %(by)s,
            answered_at = now()
        WHERE id = %(id)s AND status = 'pending'
        """,
        {
            "id": request_id,
            "status": status,
            "answer": answer,
            "moved_to": moved_to,
            "by": answered_by,
        },
    )
    return cur.rowcount > 0
