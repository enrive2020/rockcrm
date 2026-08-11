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


def _digits(value: str) -> str:
    """Цифры запроса, приведённые к тому виду, в котором телефон лежит в базе.

    В базе номер в E.164 (+77015552418), а в трубке его диктуют как
    «8 701 555 24 18»: восьмёрка — это казахстанский код выхода на межгород,
    тот же номер, что и +7. Без этой замены поиск по продиктованному номеру
    не находил бы ничего, и администратор решил бы, что клиента нет в базе.
    """
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 11 and digits.startswith("8"):
        digits = digits[1:]
    return digits


def search_students(
    cur: psycopg.Cursor, query: str, branch_id: str | None, limit: int
) -> list[dict[str, Any]]:
    """Поиск для строки поиска в интерфейсе.

    Ищет и по ученику, и по плательщику: администратору звонит родитель,
    а не ребёнок, и первое, что он называет, — своё имя или свой номер.

    Телефон сравнивается по цифрам: в базе он в E.164 (+77015552418),
    а в трубке его диктуют как «8 701 555 24 18» или «555-24-18».
    """
    text = (query or "").strip()
    digits = _digits(text)
    cur.execute(
        f"""
        SELECT {_STUDENT_COLUMNS}
        {_STUDENT_JOINS}
        WHERE s.archived_at IS NULL
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
