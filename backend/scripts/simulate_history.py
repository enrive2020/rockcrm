"""Генератор правдоподобной истории работы школы за полгода.

Демо-посев (scripts/seed_demo.py) описывает один день и 19 учеников. На таком
наборе не видно ничего из того, ради чего строилась система: накопление ошибок
за месяцы, поведение журнала абонемента на сотнях движений, отчёт по воронке
на длинной когорте, скорость экранов при полной загрузке филиала.

    python -m scripts.simulate_history --students 165 --months 6 --seed 42 --reset

Главное правило: **всё, что умеет приложение, делает приложение.** Заявка
заводится через app/leads.py, абонемент продаётся через app/billing.py,
посещаемость отмечается через app/attendance.py — тем же кодом и под той же
ролью базы (RLS включён), что и живой HTTP-запрос. Прямые INSERT остались
только там, где кода приложения не существует вовсе. Иначе проверка выродилась
бы в тест умения писать SQL.

Демо-данные генератор не трогает: у симуляции свои тенанты (SIM_TENANT_A
и SIM_TENANT_B), а --reset сносит только их.

Чего нет в приложении и поэтому написано здесь
----------------------------------------------
* **Расписание.** Редактирование расписания в этапы не входило — занятия
  вставляются напрямую. Часы раздаются так, что регулярное расписание
  не может пересечься само с собой: значит, всякое пересечение, которое
  найдут проверки, будет настоящим дефектом, а не ленью генератора.
* **Отработки.** Ни проведения отработки, ни сгорания по сроку (ночное
  задание) в приложении нет. Записи `makeup_use` и `makeup_expire` идут через
  тот же journal.add_entry, что и всё остальное: журнал обязан остаться
  единственным источником правды об остатке.
* **Сгорание остатка по окончании срока** — та же история, kind = 'expire'.
* **Закрытие зарплатного периода.** `payroll_period` в схеме есть, кода нет.
* **Скидка семье за второго ребёнка** — поле есть, операции нет.

Как получается прошлое
----------------------
Приложение прибито к системным часам, и не по недосмотру: `create_hold()`
отказывается замораживать вчерашний день, триггер `subscription_recalc`
объявляет абонемент истёкшим по `valid_until < current_date`, а
`active_subscription()` истёкшие не выдаёт. Первая попытка — писать историю
задним числом — на этом и разбилась: проданный «в марте» абонемент становился
`expired` в ту же секунду, следующая отметка не находила, с чего списывать,
и 2 500 отметок из 2 900 прошли мимо журнала. Данные при этом были формально
целы — и именно поэтому проверки бы ничего не заметили.

Поэтому симуляция идёт **вперёд от сегодняшнего дня**: день 0 — это
сегодня, день 203 — через семь месяцев. Для приложения всё происходит
в настоящем и будущем, ни одно правило не нарушено, все ветки кода живые.
А в самом конце вся история переносится назад на `--months` месяцев одним
сдвигом всех дат и меток времени (`shift_timeline`), после чего абонементам
пересчитывается статус — теперь уже по настоящему календарю.

Внутри дня метки времени всё же правятся (`stamp_day`): приложение пишет
`now()`, а полсотни движений одной секундой не дают прочитать журнал.
Сдвиг сохраняет порядок записей и не трогает ни одной суммы и ни одной дельты.

Что всё-таки осталось неточным
------------------------------
* **Пробный урок нельзя отметить** (`422 lead_lesson`): у заявки нет ученика.
  Проведённый пробный остаётся в расписании как `planned`, а стадия заявки
  двигается отдельно.
* **Занятие по отработке не отмечается.** Операции «провести занятие
  в счёт отработки» в приложении нет, а обычная отметка списала бы занятие
  с абонемента второй раз — за него уже заплачено отработкой.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import auth, billing, config, db, journal, leads as leads_app, schemas  # noqa: E402
from app.attendance import apply_mark, revoke_mark  # noqa: E402
from app.errors import ApiError  # noqa: E402

# ---------------------------------------------------------------------------
# Постоянные идентификаторы.
#
# Фиксированы по той же причине, что и в seed_demo: tests/test_integrity.py
# должен знать, куда смотреть, не разбирая вывод генератора.
# ---------------------------------------------------------------------------


def _sid(suffix: str) -> str:
    assert len(suffix) == 5, suffix
    return f"01900000-0000-7000-8000-0000000{suffix}"


SIM_TENANT_A = _sid("0000a")   # «RockSchool История» — основная школа симуляции
SIM_TENANT_B = _sid("0000b")   # вторая школа, существует ради проверки изоляции

SIM_BRANCH_A1 = _sid("000b1")
SIM_BRANCH_A2 = _sid("000b2")
SIM_BRANCH_B1 = _sid("000bf")

SIM_ADMIN_A = _sid("00091")
SIM_ADMIN_B = _sid("00092")

TZ_NAME = "Asia/Almaty"
TZ = ZoneInfo(TZ_NAME)

# Рабочая сетка: занятия ставятся строго по часам. Часовой шаг при уроке
# в 55 минут делает пересечения невозможными по построению — а значит, любое
# пересечение, найденное проверками, будет настоящей ошибкой, а не следствием
# того, что генератор поленился считать интервалы.
OPEN_HOUR, CLOSE_HOUR = 10, 20
WEEKDAYS = (0, 1, 2, 3, 4, 5)  # пн–сб; в воскресенье школа не работает

FIRST_M = [
    "Тимур", "Ержан", "Данияр", "Арман", "Нурлан", "Санжар", "Алихан", "Ильяс",
    "Аскар", "Рустам", "Дамир", "Азамат", "Бекзат", "Ерасыл", "Мирас", "Айбек",
    "Марк", "Даниал", "Амир", "Артём",
]
FIRST_F = [
    "Амина", "Айсулу", "Сабина", "Камила", "Жанна", "Алия", "Сая", "Дана",
    "Аяна", "Мадина", "Асель", "Гульнара", "Динара", "Айгерим", "Арай", "Лаура",
    "Аружан", "Инкар", "Томирис", "Ольга",
]
LAST = [
    "Ахметов", "Сагындык", "Ким", "Оспанов", "Нурланов", "Бек", "Ли", "Жанат",
    "Токтар", "Ер", "Аман", "Сеит", "Мурат", "Абдуллин", "Сериков", "Тлеу",
    "Искаков", "Мукашев", "Байжанов", "Досжан", "Калиев", "Смагулов",
    "Жумабаев", "Оразбек", "Каримов", "Нургалиев",
]

SOURCES = [
    ("instagram", 0.28), ("telegram_bot", 0.20), ("site_form", 0.16),
    ("whatsapp", 0.12), ("referral", 0.12), ("walk_in", 0.06), ("phone", 0.06),
]

# Отказ ставится с той стадии, на которой человек передумал: отказ после
# проведённого пробного и отказ после первого звонка — разные проблемы школы.
LOST_AT_STAGE = {
    "new": [("no_answer", 0.7), ("other", 0.3)],
    "contacting": [("no_answer", 0.35), ("price", 0.25), ("schedule", 0.2),
                   ("location", 0.1), ("competitor", 0.1)],
    "trial_booked": [("no_answer", 0.5), ("schedule", 0.3), ("not_ready", 0.2)],
    "trial_held": [("price", 0.4), ("not_ready", 0.25), ("schedule", 0.2),
                   ("competitor", 0.15)],
}

# Доли отметок: около 80% пришли (came + late), остальное — прогулы, отмены
# заранее, поздние отмены и отмены преподавателем.
MARK_WEIGHTS = [
    ("came", 0.72), ("late", 0.08), ("no_show", 0.07),
    ("cancelled_early", 0.06), ("cancelled_late", 0.04), ("cancelled_teacher", 0.03),
]

DISCIPLINES = [
    # (ключ, название, минимальный возраст, требования к кабинету)
    ("drums", "Барабаны", 5, {"drum_kit": True}),
    ("guitar", "Гитара", 6, {}),
    ("vocal", "Вокал", 6, {}),
    ("piano", "Фортепиано", 5, {"piano": True}),
]
DISCIPLINE_NAME = {key: name for key, name, *_ in DISCIPLINES}

# (размер тарифа, занятий, доля от базовой цены). Срок — 31 день у обоих.
PLAN_SHAPES = [("8", 8, 1.0), ("4", 4, 0.56)]
BASE_PRICE = {"drums": 54000, "guitar": 54000, "vocal": 52000, "piano": 50000}


# ---------------------------------------------------------------------------
# Мир школы
# ---------------------------------------------------------------------------


@dataclass
class Teacher:
    id: str
    name: str
    branch_id: str
    room_id: str
    disciplines: list[str]
    # Часы, навсегда отданные под регулярные занятия учеников: (день недели, час).
    # Пока ученик не ушёл, слот занят каждую неделю.
    reserved: set[tuple[int, int]] = field(default_factory=set)


@dataclass
class World:
    tenant_id: str
    admin_id: str
    branches: list[str]
    disciplines: dict[str, str]          # ключ -> discipline_id
    teachers: list[Teacher]
    plans: dict[tuple[str, str], str]    # (направление, размер) -> plan_id
    plan_price: dict[str, int]


@dataclass
class Slot:
    teacher: Teacher
    weekday: int
    hour: int


@dataclass
class StudentSim:
    id: str
    family_id: str | None
    payer_phone: str | None
    discipline: str
    branch_id: str
    slots: list[Slot]
    plan_size: str                       # '8' или '4'
    subscription_id: str | None = None
    valid_until: dt.date | None = None
    # День, после которого ученик перестаёт продлевать абонемент. Отток —
    # не мгновенное событие: человек дохаживает оплаченное и не приходит
    # за следующим, поэтому дата ухода и дата архивации разные.
    quits_on: dt.date | None = None
    archived: bool = False
    # Остаток кончился раньше срока: продлевать надо не по календарю,
    # а прямо сейчас.
    needs_renewal: bool = False


@dataclass
class LeadSim:
    id: str
    stage: str
    phone: str
    discipline: str
    branch_id: str
    student_first: str
    student_last: str
    student_age: int
    parent_first: str
    payer_phone: str | None
    next_day: dt.date


# ---------------------------------------------------------------------------
# Служебное
# ---------------------------------------------------------------------------


def admin_dsn() -> str:
    return config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=TZ)


def _pick(rng: random.Random, weighted: list[tuple[Any, float]]) -> Any:
    roll = rng.random() * sum(w for _, w in weighted)
    acc = 0.0
    for value, weight in weighted:
        acc += weight
        if roll <= acc:
            return value
    return weighted[-1][0]


class Stats(Counter):
    """Счётчики сущностей и отказов. Отчёт без цифр ничего не доказывает."""

    def show(self, title: str) -> None:
        print(f"\n{title}")
        for key in sorted(self):
            print(f"  {key:.<46} {self[key]}")


def guarded(stats: Stats, cur: psycopg.Cursor, label: str, fn: Callable, *args, **kwargs):
    """Операция в точке сохранения.

    День симуляции идёт одной транзакцией, но отказ одной операции (занятый
    слот, исчерпанный абонемент, дубль заявки) в жизни не отменяет весь день.
    Отказы не глотаются, а считаются: если их вдруг станет много, это видно
    в отчёте, а не только в форме данных.
    """
    try:
        with cur.connection.transaction():
            return fn(*args, **kwargs)
    except ApiError as exc:
        stats[f"отказ.{label}.{exc.code}"] += 1
        return None
    except psycopg.errors.IntegrityError as exc:
        stats[f"отказ.{label}.{exc.diag.constraint_name or exc.sqlstate}"] += 1
        return None


# ---------------------------------------------------------------------------
# Справочники школы. Кода приложения для них нет — заводятся напрямую.
# ---------------------------------------------------------------------------


def build_world(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    admin_id: str,
    slug: str,
    name: str,
    branch_ids: list[str],
    branch_names: list[str],
    phone_prefix: str,
    carry_over_lessons: int,
) -> World:
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO tenant (id, slug, name, timezone, default_rules)
        VALUES (%s, %s, %s, %s,
                jsonb_build_object(
                  'no_show_burns', true,
                  'cancel_notice_hours', 24,
                  'cancel_early_effect', 'makeup',
                  'teacher_cancel_effect', 'makeup',
                  'makeup_ttl_days', 30,
                  'freeze_days_per_year', 14,
                  'pay_teacher_on_no_show', true,
                  'carry_over_lessons', %s::int,
                  'allow_overlapping_subscriptions', false))
        """,
        (tenant_id, slug, name, TZ_NAME, carry_over_lessons),
    )

    for branch_id, branch_name in zip(branch_ids, branch_names):
        cur.execute(
            """INSERT INTO branch (id, tenant_id, name, timezone, opens_at, closes_at)
               VALUES (%s, %s, %s, %s, '10:00', '21:00')""",
            (branch_id, tenant_id, branch_name, TZ_NAME),
        )

    disciplines: dict[str, str] = {}
    for order, (key, disc_name, min_age, reqs) in enumerate(DISCIPLINES):
        cur.execute(
            """INSERT INTO discipline (tenant_id, name, min_age, room_reqs, sort_order)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, disc_name, min_age, Json(reqs), order),
        )
        disciplines[key] = str(cur.fetchone()[0])

    # Один преподаватель — один кабинет. Теоретически теряется гибкость,
    # практически выигрывается главное: двойная бронь кабинета не может
    # возникнуть иначе, чем вместе с двойной бронью преподавателя.
    layout = [
        ("drums", "Барабанная", {"drum_kit": True, "soundproof": True}, 4500),
        ("guitar", "Класс 1", {}, 4200),
        ("guitar", "Класс 2", {}, 4200),
        ("vocal", "Вокальная", {}, 4000),
        ("piano", "Класс 3", {"piano": True}, 4000),
    ]
    palette = ["#A65D3F", "#2F7D7A", "#4B6489", "#4E7A3E", "#7C4A72"]

    teachers: list[Teacher] = []
    counter = 0
    for branch_index, branch_id in enumerate(branch_ids):
        for index, (disc_key, room_name, features, rate) in enumerate(layout):
            counter += 1
            cur.execute(
                """INSERT INTO room (tenant_id, branch_id, name, features)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (tenant_id, branch_id, f"{room_name} · {branch_index + 1}", Json(features)),
            )
            room_id = str(cur.fetchone()[0])

            first = FIRST_M[counter % len(FIRST_M)]
            last = LAST[counter % len(LAST)]
            cur.execute(
                """INSERT INTO person (tenant_id, first_name, last_name, phone, pd_consent_at)
                   VALUES (%s, %s, %s, %s, now()) RETURNING id""",
                (tenant_id, first, last, f"{phone_prefix}90{counter:05d}"),
            )
            person_id = str(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO staff (tenant_id, person_id, kind, color, hired_on)
                   VALUES (%s, %s, 'teacher', %s, '2025-09-01') RETURNING id""",
                (tenant_id, person_id, palette[index % len(palette)]),
            )
            staff_id = str(cur.fetchone()[0])

            # Пианист ведёт ещё и вокал: подбор преподавателя не должен быть
            # вырожденным, иначе половина проверок на выбор слота бессмысленна.
            own = ["piano", "vocal"] if disc_key == "piano" else [disc_key]
            for key in own:
                cur.execute(
                    "INSERT INTO staff_discipline (staff_id, discipline_id) VALUES (%s, %s)",
                    (staff_id, disciplines[key]),
                )
            cur.execute(
                "INSERT INTO staff_branch (staff_id, branch_id) VALUES (%s, %s)",
                (staff_id, branch_id),
            )
            cur.execute(
                """INSERT INTO teacher_rate (tenant_id, staff_id, format, amount, valid_from)
                   VALUES (%s, %s, 'individual', %s, '2025-09-01'),
                          (%s, %s, 'trial', 2000, '2025-09-01'),
                          (%s, %s, 'group', %s, '2025-09-01')""",
                (tenant_id, staff_id, rate, tenant_id, staff_id, tenant_id, staff_id, rate + 800),
            )
            teachers.append(
                Teacher(id=staff_id, name=f"{first} {last}", branch_id=branch_id,
                        room_id=room_id, disciplines=own)
            )

    # Администратор, от чьего имени идут все операции: без живой учётной
    # записи аудит писался бы на несуществующего автора.
    cur.execute(
        """INSERT INTO person (tenant_id, first_name, last_name, phone, pd_consent_at)
           VALUES (%s, 'Асель', 'Нурланова', %s, now()) RETURNING id""",
        (tenant_id, f"{phone_prefix}9900001"),
    )
    admin_person = str(cur.fetchone()[0])
    cur.execute(
        """INSERT INTO app_user (id, tenant_id, person_id, login, role)
           VALUES (%s, %s, %s, %s, 'admin')""",
        (admin_id, tenant_id, admin_person, f"{phone_prefix}9900001"),
    )

    plans: dict[tuple[str, str], str] = {}
    plan_price: dict[str, int] = {}
    for disc_key, disc_name, _, _ in DISCIPLINES:
        for size, lessons, ratio in PLAN_SHAPES:
            price = int(BASE_PRICE[disc_key] * ratio) // 100 * 100
            cur.execute(
                """INSERT INTO subscription_plan
                     (tenant_id, name, discipline_id, format, duration_min,
                      lessons_count, valid_days, price)
                   VALUES (%s, %s, %s, 'individual', 55, %s, 31, %s) RETURNING id""",
                (tenant_id, f"{disc_name}, {lessons} занятий, 55 мин",
                 disciplines[disc_key], lessons, price),
            )
            plan_id = str(cur.fetchone()[0])
            plans[(disc_key, size)] = plan_id
            plan_price[plan_id] = price

    conn.commit()
    return World(
        tenant_id=tenant_id, admin_id=admin_id, branches=branch_ids,
        disciplines=disciplines, teachers=teachers, plans=plans, plan_price=plan_price,
    )


# ---------------------------------------------------------------------------
# Обратная датировка
# ---------------------------------------------------------------------------

# (таблица, колонка-якорь, колонки к сдвигу). Якорь отвечает на вопрос
# «строка появилась или изменилась в текущем смоделированном дне».
_SHIFT: list[tuple[str, str, list[str]]] = [
    ("person", "created_at", ["created_at", "updated_at"]),
    ("family", "created_at", ["created_at"]),
    ("student", "created_at", ["created_at"]),
    ("lesson", "created_at", ["created_at", "updated_at"]),
    ("lesson", "updated_at", ["updated_at"]),
    ("attendance", "marked_at", ["marked_at"]),
    ("attendance", "revoked_at", ["revoked_at"]),
    ("subscription", "created_at", ["created_at"]),
    ("subscription_hold", "created_at", ["created_at"]),
    ("makeup_credit", "created_at", ["created_at"]),
    ("makeup_credit", "used_at", ["used_at"]),
    ("makeup_credit", "expired_at", ["expired_at"]),
    ("payment", "paid_at", ["paid_at", "created_at"]),
    ("payroll_entry", "created_at", ["created_at"]),
    ("lead", "created_at", ["created_at", "updated_at"]),
    ("lead", "updated_at", ["updated_at"]),
    ("lead_stage_history", "changed_at", ["changed_at"]),
    ("notification", "created_at", ["created_at"]),
    ("lesson_note", "created_at", ["created_at"]),
    # Журналы. Правку им запрещает триггер, аварийный люк app.allow_purge
    # предусмотрен схемой; ни одна сумма и ни одна дельта при этом не меняется.
    ("subscription_entry", "created_at", ["created_at"]),
    ("audit_log", "created_at", ["created_at"]),
]


def stamp_day(
    conn: psycopg.Connection, tenant_id: str, mark: dt.datetime, day: dt.date, today: dt.date
) -> None:
    """Переносит всё, что записано после `mark`, на смоделированный день.

    Сдвиг сохраняет порядок внутри дня: new = 09:00 дня + (было − метка).
    Порядок в журнале важнее точного времени: именно по нему читается ответ
    на «куда делось занятие».

    Дни после «сегодня» датируются сегодняшним: расписание на три недели
    вперёд составлено сегодня, а не в тот день, на который оно поставлено.
    """
    base = _at(min(day, today), 9)
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.allow_purge', 'on', true)")
    # Верхняя граница окна обязательна, и это не перестраховка. Симуляция идёт
    # вперёд, поэтому проставленная метка оказывается в БУДУЩЕМ относительно
    # системных часов, и условие «позже метки» на следующий же день поймало бы
    # вчерашние строки снова — даты уехали бы на годы вперёд, а сходимость
    # журнала при этом продолжала бы сходиться. Ровно так это и было найдено.
    cur.execute("SELECT clock_timestamp()")
    upper = cur.fetchone()[0]
    bounds = {"base": base, "mark": mark, "upper": upper, "t": tenant_id}
    for table, anchor, columns in _SHIFT:
        sets = ", ".join(f"{c} = %(base)s + ({c} - %(mark)s)" for c in columns)
        cur.execute(
            f"UPDATE {table} SET {sets} "
            f"WHERE tenant_id = %(t)s AND {anchor} > %(mark)s AND {anchor} <= %(upper)s",
            bounds,
        )

    # student.started_on приложение ставит текущей датой — приводим к дате
    # конверсии. Для дней после «сегодня» правка не только бессмысленна,
    # но и опасна: окно у них то же, что у сегодняшнего дня.
    if day <= today:
        cur.execute(
            """UPDATE student SET started_on = %(day)s
                WHERE tenant_id = %(t)s
                  AND created_at >= %(base)s AND created_at < %(base)s + interval '1 day'""",
            {"day": day, "t": tenant_id, "base": base},
        )
    conn.commit()


def db_now(conn: psycopg.Connection) -> dt.datetime:
    """Часы базы, а не транзакции.

    Именно clock_timestamp(), а не now(): служебное соединение живёт весь
    прогон, и now() отдавал бы момент начала его самой первой транзакции —
    одно и то же значение на все двести дней.
    """
    cur = conn.cursor()
    cur.execute("SELECT clock_timestamp()")
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Перенос готовой истории в прошлое
# ---------------------------------------------------------------------------

# Колонки-моменты и колонки-даты, которые двигаются вместе со всей историей.
# Списки полные по схеме: пропущенная колонка означала бы занятие в марте
# с отметкой в сентябре — расхождение, которое проверки поймают, но чинить
# его придётся здесь.
_TIMESTAMP_COLUMNS: dict[str, list[str]] = {
    "person": ["created_at", "updated_at"],
    "family": ["created_at"],
    "student": ["created_at", "archived_at"],
    "attendance": ["marked_at", "revoked_at"],
    "subscription": ["created_at"],
    "subscription_hold": ["created_at"],
    "makeup_credit": ["created_at", "used_at", "expired_at"],
    "payment": ["paid_at", "created_at"],
    "payroll_entry": ["created_at"],
    "lead": ["created_at", "updated_at", "next_action_at"],
    "lead_stage_history": ["changed_at"],
    "notification": ["send_after", "created_at"],
    "lesson_note": ["created_at"],
    "subscription_entry": ["created_at"],
    "audit_log": ["created_at"],
}

_DATE_COLUMNS: dict[str, list[str]] = {
    "student": ["started_on"],
    "subscription": ["valid_from", "valid_until"],
    "makeup_credit": ["expires_on"],
}


def shift_timeline(conn: psycopg.Connection, tenant_id: str, days: int) -> None:
    """Переносит всю историю школы на `days` дней назад.

    Симуляция шла вперёд от сегодняшнего дня — иначе приложение объявило бы
    каждый проданный абонемент истёкшим ещё до первой отметки. Здесь готовая
    история целиком переезжает в прошлое: расстояния между событиями
    сохраняются, поэтому ничего пересчитывать не нужно.
    """
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.allow_purge', 'on', true)")

    for table, columns in _TIMESTAMP_COLUMNS.items():
        sets = ", ".join(f"{c} = {c} - make_interval(days => %(d)s)" for c in columns)
        cur.execute(f"UPDATE {table} SET {sets} WHERE tenant_id = %(t)s",
                    {"d": days, "t": tenant_id})
    for table, columns in _DATE_COLUMNS.items():
        sets = ", ".join(f"{c} = {c} - %(d)s" for c in columns)
        cur.execute(f"UPDATE {table} SET {sets} WHERE tenant_id = %(t)s",
                    {"d": days, "t": tenant_id})

    # Заморозки лежат интервалом: сдвигаются обе границы разом, иначе
    # ограничение исключения увидело бы пересечение там, где его не было.
    cur.execute(
        """UPDATE subscription_hold
              SET period = daterange(lower(period) - %(d)s, upper(period) - %(d)s, '[)')
            WHERE tenant_id = %(t)s""",
        {"d": days, "t": tenant_id},
    )

    # Занятия — в два приёма. Сдвиг на месте столкнул бы ещё не перенесённое
    # занятие с уже перенесённым: диапазоны «до» и «после» пересекаются,
    # а ограничение исключения по кабинету проверяется построчно и отложить
    # его нечем. Промежуточная стоянка в пустом столетии снимает вопрос.
    for step in (-20000, 20000 - days):
        cur.execute(
            """UPDATE lesson
                  SET starts_at  = starts_at  + make_interval(days => %(d)s),
                      ends_at    = ends_at    + make_interval(days => %(d)s),
                      created_at = created_at + make_interval(days => %(d)s),
                      updated_at = updated_at + make_interval(days => %(d)s)
                WHERE tenant_id = %(t)s""",
            {"d": step, "t": tenant_id},
        )

    # Статус абонемента считает триггер, и считает он его по current_date.
    # Пока история жила в будущем, истёкших не было ни одного; теперь, когда
    # даты на месте, статусы надо получить заново — тем же триггером, а не
    # руками. Достаточно тронуть по одной записи журнала на абонемент.
    cur.execute(
        """UPDATE subscription_entry SET created_at = created_at
            WHERE tenant_id = %(t)s
              AND id IN (SELECT min(id) FROM subscription_entry
                          WHERE tenant_id = %(t)s GROUP BY subscription_id)""",
        {"t": tenant_id},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Симуляция
# ---------------------------------------------------------------------------


class Simulation:
    def __init__(
        self,
        world: World,
        *,
        rng: random.Random,
        admin: psycopg.Connection,
        start: dt.date,
        end: dt.date,
        today: dt.date,
        target_students: int,
        phone_prefix: str,
        stats: Stats,
        horizon_days: int = 21,
    ) -> None:
        self.w = world
        self.rng = rng
        self.admin = admin
        self.start = start
        self.end = end
        self.today = today
        self.phone_prefix = phone_prefix
        self.stats = stats
        self.horizon = dt.timedelta(days=horizon_days)

        self.leads: list[LeadSim] = []
        self.students: list[StudentSim] = []
        self.families: list[tuple[str, str]] = []   # (телефон плательщика, фамилия)
        self.phone_seq = 0
        # (teacher_id, момент) — что уже стоит в расписании. Нужно, чтобы
        # разовые занятия (пробные, отработки) не лезли в занятый час.
        self.booked: set[tuple[str, dt.datetime]] = set()
        # день -> [(lesson_id, student_id)] регулярных занятий, которые
        # предстоит отметить.
        self.to_mark: dict[dt.date, list[tuple[str, str]]] = {}

        # Сколько заявок в день нужно, чтобы к концу периода набралось столько
        # учеников, сколько просили. Сквозная конверсия заложена цепочкой
        # вероятностей ниже; здесь её обратная величина.
        days = max((min(end, today) - start).days, 1)
        self.leads_per_day = target_students / 0.22 / days

    # -- мелочи ------------------------------------------------------------

    def phone(self) -> str:
        self.phone_seq += 1
        return f"{self.phone_prefix}{self.phone_seq:07d}"

    def teachers_for(self, discipline: str, branch_id: str | None = None) -> list[Teacher]:
        return [
            t for t in self.w.teachers
            if discipline in t.disciplines and (branch_id is None or t.branch_id == branch_id)
        ]

    def take_slots(self, discipline: str, branch_id: str, count: int) -> list[Slot]:
        """Резервирует ученику постоянные часы в неделе.

        Час отдаётся навсегда — пока ученик не ушёл, — и больше никому
        не достаётся. Поэтому регулярное расписание не может пересечься
        само с собой, и всякое пересечение, найденное проверками, настоящее.
        """
        candidates = self.teachers_for(discipline, branch_id) or self.teachers_for(discipline)
        self.rng.shuffle(candidates)
        slots: list[Slot] = []
        for teacher in candidates:
            free = [
                (wd, hour)
                for wd in WEEKDAYS
                for hour in range(OPEN_HOUR, CLOSE_HOUR)
                if (wd, hour) not in teacher.reserved
            ]
            self.rng.shuffle(free)
            # Два урока подряд в один день школа не ставит.
            used_weekdays = {s.weekday for s in slots}
            for wd, hour in free:
                if len(slots) >= count:
                    break
                if wd in used_weekdays:
                    continue
                teacher.reserved.add((wd, hour))
                used_weekdays.add(wd)
                slots.append(Slot(teacher=teacher, weekday=wd, hour=hour))
            if len(slots) >= count:
                break
        return slots

    def release_slots(self, student: StudentSim) -> None:
        for slot in student.slots:
            slot.teacher.reserved.discard((slot.weekday, slot.hour))
        student.slots = []

    def free_hour(self, teacher: Teacher, day: dt.date) -> dt.datetime | None:
        hours = list(range(OPEN_HOUR, CLOSE_HOUR))
        self.rng.shuffle(hours)
        for hour in hours:
            if (day.weekday(), hour) in teacher.reserved:
                continue
            moment = _at(day, hour)
            if (teacher.id, moment) in self.booked:
                continue
            return moment
        return None

    # -- расписание --------------------------------------------------------

    def add_lesson(
        self,
        cur: psycopg.Cursor,
        *,
        student: StudentSim,
        teacher: Teacher,
        starts_at: dt.datetime,
        minutes: int = 55,
        kind: str = "regular",
    ) -> str:
        cur.execute(
            """
            INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, discipline_id,
                                student_id, kind, starts_at, ends_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'planned')
            RETURNING id
            """,
            (
                self.w.tenant_id, teacher.branch_id, teacher.id, teacher.room_id,
                self.w.disciplines[student.discipline], student.id, kind,
                starts_at, starts_at + dt.timedelta(minutes=minutes),
            ),
        )
        lesson_id = str(cur.fetchone()["id"])
        self.booked.add((teacher.id, starts_at))
        self.stats[f"занятий.{kind}"] += 1
        if kind == "regular":
            self.to_mark.setdefault(starts_at.date(), []).append((lesson_id, student.id))
        return lesson_id

    def materialize(self, cur: psycopg.Cursor, student: StudentSim, day: dt.date) -> None:
        """Ставит регулярные занятия ученика на один конкретный день."""
        if student.archived or (student.quits_on and day > student.quits_on):
            return
        for slot in student.slots:
            if slot.weekday != day.weekday():
                continue
            moment = _at(day, slot.hour)
            if (slot.teacher.id, moment) in self.booked:
                continue
            guarded(
                self.stats, cur, "lesson",
                self.add_lesson, cur, student=student, teacher=slot.teacher, starts_at=moment,
            )

    # -- воронка -----------------------------------------------------------

    def spawn_leads(self, cur: psycopg.Cursor, day: dt.date) -> None:
        expected = self.leads_per_day * (0.6 if day.weekday() >= 5 else 1.15)
        count = int(expected) + (1 if self.rng.random() < expected % 1 else 0)
        for _ in range(count):
            self.new_lead(cur, day)

    def new_lead(self, cur: psycopg.Cursor, day: dt.date) -> None:
        rng = self.rng
        discipline = rng.choice([k for k, *_ in DISCIPLINES])
        branch_id = rng.choice(self.w.branches)
        age = rng.choice([6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 27, 34])
        child = age < 18
        student_first = rng.choice(FIRST_F if rng.random() < 0.5 else FIRST_M)

        # Примерно каждая двенадцатая заявка — второй ребёнок из уже знакомой
        # семьи. Ради этого сценария в схеме и заведена семья: скидка
        # за второго ребёнка, общий долг, один плательщик.
        sibling = bool(self.families) and rng.random() < 0.08
        if sibling:
            payer_phone, last = rng.choice(self.families)
            lead_phone = payer_phone
        else:
            last = rng.choice(LAST)
            lead_phone = self.phone()
            payer_phone = lead_phone if child else None

        parent_first = rng.choice(FIRST_F if rng.random() < 0.7 else FIRST_M)
        source = _pick(rng, SOURCES)
        name = f"{parent_first} {last}" if child else f"{student_first} {last}"

        if source in ("telegram_bot", "site_form", "instagram"):
            # Половина потока приходит из внешних систем — через вебхук, ровно
            # так же, как её принесёт LeadHub или бот, вместе с ретраями.
            body = schemas.WebhookLead(
                external_id=f"sim-{day.isoformat()}-{self.phone_seq}-{rng.randrange(10 ** 7)}",
                name=name, phone=lead_phone, student_name=student_first, student_age=age,
                discipline=DISCIPLINE_NAME[discipline], branch_id=branch_id, source=source,
                utm={"utm_source": source, "utm_campaign": f"{day:%Y-%m}"},
            )
            result = guarded(
                self.stats, cur, "webhook", leads_app.accept_webhook, cur, self.w.tenant_id, body
            )
            if result is None:
                return
            card, created = result
            if not created:
                self.stats["заявок.повтор вебхука"] += 1
                return
        else:
            body = schemas.LeadCreate(
                name=name, phone=lead_phone, student_name=student_first, student_age=age,
                discipline_id=self.w.disciplines[discipline], branch_id=branch_id, source=source,
                comment=rng.choice([None, "Удобно после 18:00", "Спрашивает про рассрочку"]),
            )
            card = guarded(
                self.stats, cur, "lead", leads_app.create_lead, cur,
                self.w.tenant_id, self.w.admin_id, body,
            )
            if card is None:
                return

        self.stats["заявок.создано"] += 1
        self.leads.append(
            LeadSim(
                id=card["id"], stage="new", phone=lead_phone, discipline=discipline,
                branch_id=branch_id, student_first=student_first, student_last=last,
                student_age=age, parent_first=parent_first, payer_phone=payer_phone,
                next_day=day + dt.timedelta(days=rng.randint(0, 2)),
            )
        )

    def advance_leads(self, cur: psycopg.Cursor, day: dt.date) -> None:
        handlers = {
            "new": self.lead_new,
            "contacting": self.lead_contacting,
            "trial_booked": self.lead_trial_booked,
            "trial_held": self.lead_trial_held,
        }
        for lead in list(self.leads):
            if lead.next_day > day:
                continue
            handler = handlers.get(lead.stage)
            if handler is None:
                self.leads.remove(lead)
                continue
            handler(cur, lead, day)

    def patch(self, cur: psycopg.Cursor, lead: LeadSim, **fields) -> bool:
        # model_validate по словарю, а не конструктор: update_lead читает
        # model_dump(exclude_unset=True), и «не передали» обязано отличаться
        # от «передали null».
        body = schemas.LeadPatch.model_validate(fields)
        return guarded(
            self.stats, cur, "lead.patch", leads_app.update_lead, cur,
            self.w.tenant_id, self.w.admin_id, lead.id, body,
        ) is not None

    def lose(self, cur: psycopg.Cursor, lead: LeadSim) -> None:
        reason = _pick(self.rng, LOST_AT_STAGE[lead.stage])
        if self.patch(cur, lead, stage="lost", lost_reason=reason):
            self.stats[f"заявок.отказ.{lead.stage}"] += 1
        self.leads.remove(lead)

    def lead_new(self, cur: psycopg.Cursor, lead: LeadSim, day: dt.date) -> None:
        if self.rng.random() > 0.86:
            self.lose(cur, lead)
            return
        if self.patch(cur, lead, stage="contacting", contact_attempts=self.rng.randint(1, 3)):
            lead.stage = "contacting"
            lead.next_day = day + dt.timedelta(days=self.rng.randint(1, 4))

    def lead_contacting(self, cur: psycopg.Cursor, lead: LeadSim, day: dt.date) -> None:
        if self.rng.random() > 0.66:
            self.lose(cur, lead)
            return
        candidates = (self.teachers_for(lead.discipline, lead.branch_id)
                      or self.teachers_for(lead.discipline))
        self.rng.shuffle(candidates)
        for teacher in candidates:
            trial_day = day + dt.timedelta(days=self.rng.randint(1, 5))
            if trial_day.weekday() == 6:
                trial_day += dt.timedelta(days=1)
            moment = self.free_hour(teacher, trial_day)
            if moment is None:
                continue
            body = schemas.TrialRequest(
                teacher_id=teacher.id, room_id=teacher.room_id,
                starts_at=moment, duration_min=45, price=2000,
            )
            booked = guarded(
                self.stats, cur, "trial", leads_app.book_trial, cur,
                self.w.tenant_id, self.w.admin_id, lead.id, body,
            )
            if booked is None:
                continue
            self.booked.add((teacher.id, moment))
            self.stats["занятий.trial"] += 1
            self.stats["заявок.пробный назначен"] += 1
            lead.stage = "trial_booked"
            lead.next_day = trial_day
            return
        # Свободного часа не нашлось — перезвоним завтра, заявка не теряется.
        lead.next_day = day + dt.timedelta(days=1)

    def lead_trial_booked(self, cur: psycopg.Cursor, lead: LeadSim, day: dt.date) -> None:
        if self.rng.random() > 0.82:
            self.lose(cur, lead)
            return
        if self.patch(cur, lead, stage="trial_held"):
            lead.stage = "trial_held"
            lead.next_day = day + dt.timedelta(days=self.rng.randint(1, 3))
            self.stats["заявок.пробный проведён"] += 1

    def lead_trial_held(self, cur: psycopg.Cursor, lead: LeadSim, day: dt.date) -> None:
        if self.rng.random() > 0.56:
            self.lose(cur, lead)
            return
        self.convert(cur, lead, day)

    def convert(self, cur: psycopg.Cursor, lead: LeadSim, day: dt.date) -> None:
        rng = self.rng
        child = lead.student_age < 18
        size = "8" if rng.random() < 0.6 else "4"
        plan_id = self.w.plans[(lead.discipline, size)]
        price = self.w.plan_price[plan_id]

        teachers = (self.teachers_for(lead.discipline, lead.branch_id)
                    or self.teachers_for(lead.discipline))
        main_teacher = rng.choice(teachers)

        # Большинство платит сразу и полностью, часть — половину, часть уходит
        # в долг. Без долгов карточка семьи не показывает ничего интересного,
        # а именно ради неё считается debt.
        roll = rng.random()
        payment = None
        if roll < 0.82:
            payment = schemas.PaymentIn(
                amount=price,
                method=_pick(rng, [("kaspi", 0.6), ("card", 0.2), ("cash", 0.15),
                                   ("transfer", 0.05)]),
            )
        elif roll < 0.93:
            payment = schemas.PaymentIn(amount=int(price * 0.5), method="kaspi")

        body = schemas.ConvertRequest(
            payer=schemas.PayerIn(
                first_name=lead.parent_first, last_name=lead.student_last,
                phone=lead.payer_phone or lead.phone,
            ) if child else None,
            student=schemas.StudentIn(
                first_name=lead.student_first, last_name=lead.student_last,
                birth_date=dt.date(day.year - lead.student_age,
                                   rng.randint(1, 12), rng.randint(1, 28)),
                discipline_id=self.w.disciplines[lead.discipline],
                branch_id=lead.branch_id, main_teacher_id=main_teacher.id,
            ),
            subscription=schemas.SellRequest(
                plan_id=plan_id, starts_on=day, payment=payment, carry_over=False
            ),
        )
        result = guarded(
            self.stats, cur, "convert", leads_app.convert, cur,
            self.w.tenant_id, self.w.admin_id, lead.id, body,
        )
        self.leads.remove(lead)
        if result is None:
            return

        self.stats["заявок.конверсия"] += 1
        slots = self.take_slots(lead.discipline, lead.branch_id, 2 if size == "8" else 1)
        if not slots:
            self.stats["отказ.slots.нет свободных часов"] += 1

        # Срок жизни ученика в школе. Треть уходит через 2–3 месяца — ровно
        # тот отток, ради которого в карточке считается churn_risk.
        roll = rng.random()
        if roll < 0.30:
            lifetime = rng.randint(55, 95)
        elif roll < 0.48:
            lifetime = rng.randint(100, 150)
        else:
            lifetime = 400  # заведомо дольше периода симуляции

        student = StudentSim(
            id=result["student_id"], family_id=result["family_id"],
            payer_phone=lead.payer_phone, discipline=lead.discipline,
            branch_id=lead.branch_id, slots=slots, plan_size=size,
            subscription_id=result["subscription_id"],
            valid_until=day + dt.timedelta(days=30),
            quits_on=day + dt.timedelta(days=lifetime),
        )
        self.students.append(student)
        self.stats["учеников.создано"] += 1

        if child and lead.payer_phone:
            known = any(phone == lead.payer_phone for phone, _ in self.families)
            if not known:
                self.families.append((lead.payer_phone, lead.student_last))
            else:
                # Второй ребёнок в семье: школа даёт скидку. Операции для этого
                # в приложении нет — скидка живёт полем на семье.
                cur.execute(
                    "UPDATE family SET discount_pct = 10 WHERE id = %s AND discount_pct = 0",
                    (student.family_id,),
                )
                if cur.rowcount:
                    self.stats["семей.скидка за второго ребёнка"] += 1

        # Расписание на весь горизонт вперёд: иначе первое занятие
        # у только что пришедшего ученика появилось бы через три недели.
        cursor_day = day
        while cursor_day <= min(day + self.horizon, self.end):
            self.materialize(cur, student, cursor_day)
            cursor_day += dt.timedelta(days=1)

    # -- абонементы --------------------------------------------------------

    def renew_subscriptions(self, cur: psycopg.Cursor, day: dt.date) -> None:
        for student in self.students:
            if student.archived or student.valid_until is None:
                continue
            if student.quits_on and day > student.quits_on:
                self.retire(cur, student, day)
                continue
            if day > student.valid_until:
                # Пропущенное продление: абонемент кончился, ученик ходит.
                # Продаём с сегодняшнего дня — так это и делает администратор.
                self.sell(cur, student, day, day)
                continue
            if student.needs_renewal:
                # Занятия кончились раньше срока. Продажа проходит: пересечение
                # запрещено только абонементам с непотраченным остатком.
                student.needs_renewal = False
                self.sell(cur, student, day, day)
                continue
            # Продление за два дня до конца срока: карточка уже показала
            # «остаток 1» или «истекает».
            if day >= student.valid_until - dt.timedelta(days=2):
                self.sell(cur, student, student.valid_until + dt.timedelta(days=1), day)

    def sell(
        self, cur: psycopg.Cursor, student: StudentSim, starts_on: dt.date, day: dt.date
    ) -> None:
        rng = self.rng
        # Часть учеников меняет интенсивность: было два раза в неделю, стало
        # одно. Это тоже история, и на неё завязан остаток.
        if rng.random() < 0.08:
            student.plan_size = "4" if student.plan_size == "8" else "8"
        plan_id = self.w.plans[(student.discipline, student.plan_size)]
        price = self.w.plan_price[plan_id]

        roll = rng.random()
        payment = None
        if roll < 0.80:
            payment = schemas.PaymentIn(amount=price, method="kaspi")
        elif roll < 0.90:
            payment = schemas.PaymentIn(amount=int(price * 0.6), method="cash")

        body = schemas.SellRequest(
            plan_id=plan_id, starts_on=starts_on, payment=payment,
            # Перенос остатка у школы включён (carry_over_lessons = 2)
            # и запрашивается не всегда: решает администратор.
            carry_over=rng.random() < 0.4,
        )
        sold = guarded(
            self.stats, cur, "sell", billing.sell_subscription, cur,
            self.w.tenant_id, self.w.admin_id, student.id, body,
        )
        if sold is None:
            return
        student.subscription_id = sold["subscription_id"]
        student.valid_until = dt.date.fromisoformat(sold["valid_until"])
        self.stats["абонементов.продано"] += 1
        if sold["carried_over"]:
            self.stats["абонементов.с переносом остатка"] += 1
        if sold["debt"]:
            self.stats["абонементов.с долгом"] += 1

    def retire(self, cur: psycopg.Cursor, student: StudentSim, day: dt.date) -> None:
        """Ученик ушёл: абонемент дожил свой срок, занятия больше не ставим."""
        if student.valid_until and day <= student.valid_until:
            return
        # Остаток неиспользованного абонемента сгорает по окончании срока.
        # Ночного задания для этого в приложении нет, а без записи `expire`
        # журнал показывал бы живой остаток на давно мёртвом абонементе.
        # Гасим не всем: часть абонементов должна остаться просроченной
        # с ненулевым остатком — это тоже реальное состояние базы.
        if student.subscription_id and self.rng.random() < 0.5:
            cur.execute(
                "SELECT lessons_balance FROM subscription WHERE id = %s",
                (student.subscription_id,),
            )
            row = cur.fetchone()
            balance = int(row["lessons_balance"]) if row else 0
            if balance > 0:
                journal.add_entry(
                    cur, self.w.tenant_id, student.subscription_id,
                    kind="expire", lessons_delta=-balance, makeups_delta=0,
                    reason=f"Срок абонемента истёк {student.valid_until:%d.%m.%Y}, "
                           "остаток сгорел",
                    actor_id=self.w.admin_id,
                )
                self.stats["абонементов.остаток сгорел"] += 1
            else:
                self.stats["абонементов.истёк исчерпанным"] += 1
        else:
            self.stats["абонементов.истёк с остатком"] += 1

        cur.execute(
            "UPDATE student SET archived_at = %s, churn_reason = %s WHERE id = %s",
            (_at(day, 12),
             self.rng.choice(["переехали", "потерял интерес", "дорого", "занятость в школе"]),
             student.id),
        )
        student.archived = True
        self.release_slots(student)
        self.stats["учеников.ушло"] += 1

    def freeze_holidays(self, cur: psycopg.Cursor, day: dt.date) -> None:
        """Каникулы: заморозка на несколько дней, иногда снятая обратно.

        Заморозить прошлое приложение не даёт — и правильно: занятия там уже
        отмечены. В симуляции, которая идёт вперёд, это не мешает: интервал
        всегда начинается через несколько дней после текущего.
        """
        living = [
            s for s in self.students
            if not s.archived and s.subscription_id and s.valid_until and s.valid_until > day
        ]
        self.rng.shuffle(living)
        for student in living[: max(1, len(living) // 25)]:
            start = day + dt.timedelta(days=self.rng.randint(2, 12))
            days = self.rng.randint(4, 11)
            body = schemas.HoldRequest.model_validate(
                {"from": start, "to": start + dt.timedelta(days=days),
                 "reason": self.rng.choice(["каникулы", "болезнь", "отъезд семьи"])}
            )
            hold = guarded(
                self.stats, cur, "hold", billing.create_hold, cur,
                self.w.tenant_id, self.w.admin_id, student.subscription_id, body,
            )
            if hold is None:
                continue
            self.stats["заморозок.создано"] += 1
            self.stats["заморозок.отменено занятий"] += hold["lessons_cancelled"]
            student.valid_until = dt.date.fromisoformat(hold["valid_until_after"])
            # Часть заморозок снимают: планы поменялись. Снятие обязано
            # оставить в журнале компенсирующую запись, а не стереть исходную.
            if self.rng.random() < 0.25:
                released = guarded(
                    self.stats, cur, "unhold", billing.release_hold, cur,
                    self.w.tenant_id, self.w.admin_id, student.subscription_id, hold["hold_id"],
                )
                if released is not None:
                    self.stats["заморозок.снято"] += 1
                    student.valid_until = dt.date.fromisoformat(released["valid_until_after"])

    # -- посещаемость ------------------------------------------------------

    def mark_day(self, cur: psycopg.Cursor, day: dt.date) -> None:
        planned = self.to_mark.get(day, [])
        if not planned:
            return
        # Часть занятий дня уже отменена заморозкой. Отмечать их приложение
        # не даст (и правильно), но сотня ожидаемых отказов в отчёте
        # маскировала бы неожиданные — поэтому отсеиваем заранее.
        cur.execute(
            "SELECT id FROM lesson WHERE id = ANY(%s) AND status = 'planned'",
            ([lid for lid, _ in planned],),
        )
        alive = {str(row["id"]) for row in cur.fetchall()}

        by_student = {s.id: s for s in self.students}
        for lesson_id, student_id in planned:
            if student_id not in by_student or lesson_id not in alive:
                continue
            mark = _pick(self.rng, MARK_WEIGHTS)
            applied = guarded(
                self.stats, cur, "attendance", apply_mark, cur,
                self.w.tenant_id, self.w.admin_id, lesson_id, student_id, mark,
            )
            if applied is None:
                continue
            self.stats[f"отметок.{mark}"] += 1
            student = by_student[student_id]
            if (applied["applied"]["lessons_after"] == 0
                    and applied["applied"]["subscription_id"] == student.subscription_id):
                # Абонемент кончился раньше срока — администратор продаёт
                # следующий, не дожидаясь конца месяца. Иначе ученик неделю
                # ходил бы на занятия, которые нечем списать.
                #
                # Сверка с текущим абонементом обязательна: списание могло уйти
                # на старый, уже продлённый, и продажа третьего упёрлась бы
                # в запрет пересечения — совершенно справедливый.
                student.needs_renewal = True
            if applied["applied"]["makeups_delta"]:
                self.stats["отработок.выдано"] += 1
                # Приложение считает срок отработки от current_date, а не от
                # даты занятия (см. отчёт о дефектах). Симуляция живёт
                # в будущем, поэтому «через 30 дней» у неё означает
                # «тридцатый день от старта прогона» — то есть отработка,
                # выданная на пятом месяце, оказывалась просроченной в момент
                # выдачи. Приводим срок к дате занятия сразу же: делать это
                # в конце дня поздно, отработка успевает сгореть.
                cur.execute(
                    """UPDATE makeup_credit
                          SET expires_on = %s::date + (expires_on - current_date)
                        WHERE subscription_id = %s AND granted_for = %s
                          AND used_at IS NULL AND expired_at IS NULL""",
                    (day, applied["applied"]["subscription_id"], lesson_id),
                )

            # Администратор ошибается: примерно одна отметка из шестидесяти
            # отменяется. Ради этого и построен журнал — компенсирующей
            # записью, а не правкой задним числом.
            if self.rng.random() < 0.017:
                revoked = guarded(
                    self.stats, cur, "revoke", revoke_mark, cur,
                    self.w.tenant_id, self.w.admin_id, applied["attendance_id"],
                )
                if revoked is not None:
                    self.stats["отметок.отменено"] += 1

    # -- отработки ---------------------------------------------------------

    def spend_makeups(self, cur: psycopg.Cursor, day: dt.date) -> None:
        """Отработки: часть проводится, часть сгорает по сроку.

        Ни того, ни другого в приложении нет, но обе операции обязаны пройти
        через журнал абонемента: иначе кэш остатка разойдётся с суммой
        движений, и вся конструкция потеряет смысл.
        """
        spent: Counter[str] = Counter()

        cur.execute(
            """
            SELECT mc.id, mc.subscription_id, s.makeups_balance
              FROM makeup_credit mc
              JOIN subscription s ON s.id = mc.subscription_id
             WHERE mc.used_at IS NULL AND mc.expired_at IS NULL
               AND mc.expires_on < %s AND s.makeups_balance > 0
             ORDER BY mc.expires_on, mc.id
             LIMIT 40
            """,
            (day,),
        )
        for row in cur.fetchall():
            sub_id = str(row["subscription_id"])
            # Отработок у абонемента может быть несколько, а баланс прочитан
            # один раз: без этого счётчика вторая запись увела бы его в минус.
            if int(row["makeups_balance"]) - spent[sub_id] <= 0:
                continue
            spent[sub_id] += 1
            journal.add_entry(
                cur, self.w.tenant_id, sub_id,
                kind="makeup_expire", lessons_delta=0, makeups_delta=-1,
                reason=f"Отработка сгорела: срок истёк {day:%d.%m.%Y}",
                actor_id=self.w.admin_id,
            )
            cur.execute(
                "UPDATE makeup_credit SET expired_at = %s WHERE id = %s",
                (_at(day, 3), row["id"]),
            )
            self.stats["отработок.сгорело"] += 1

        cur.execute(
            """
            SELECT mc.id, mc.subscription_id, mc.student_id, s.makeups_balance
              FROM makeup_credit mc
              JOIN subscription s ON s.id = mc.subscription_id
              JOIN student st ON st.id = mc.student_id
             WHERE mc.used_at IS NULL AND mc.expired_at IS NULL
               AND mc.expires_on >= %s AND s.makeups_balance > 0
               AND st.archived_at IS NULL
             ORDER BY mc.expires_on, mc.id
             LIMIT 5
            """,
            (day,),
        )
        # Счётчик заводится заново: баланс во втором запросе прочитан уже после
        # сгораний этого дня, и вычитать их второй раз значило бы объявить
        # отработку потраченной дважды.
        spent = Counter()
        by_id = {s.id: s for s in self.students}
        for row in cur.fetchall():
            # Доля намеренно небольшая: отработку не всегда есть куда поставить,
            # и часть их обязана дожить до срока и сгореть — иначе проверять
            # сгорание не на чем.
            if self.rng.random() > 0.12:
                continue
            sub_id = str(row["subscription_id"])
            if int(row["makeups_balance"]) - spent[sub_id] <= 0:
                continue
            student = by_id.get(str(row["student_id"]))
            if student is None or not student.slots:
                continue
            teacher = student.slots[0].teacher
            moment = self.free_hour(teacher, day)
            if moment is None:
                continue
            lesson_id = guarded(
                self.stats, cur, "makeup_lesson", self.add_lesson, cur,
                student=student, teacher=teacher, starts_at=moment, kind="makeup",
            )
            if lesson_id is None:
                continue
            spent[sub_id] += 1
            journal.add_entry(
                cur, self.w.tenant_id, sub_id,
                kind="makeup_use", lessons_delta=0, makeups_delta=-1, lesson_id=lesson_id,
                reason=f"Отработка проведена {day:%d.%m.%Y}",
                actor_id=self.w.admin_id,
            )
            cur.execute(
                "UPDATE makeup_credit SET used_at = %s, used_for = %s WHERE id = %s",
                (_at(day, 12), lesson_id, row["id"]),
            )
            self.stats["отработок.использовано"] += 1

    # -- овербукинг --------------------------------------------------------

    def overbook(self, cur: psycopg.Cursor, day: dt.date) -> None:
        """Осознанный овербукинг: администратор подтвердил двойную бронь.

        Нужен ровно затем, чтобы проверка пересечений отличала подтверждённый
        конфликт от настоящей двойной брони: без единого такого занятия
        она проверяла бы пустое множество.
        """
        candidates = self.to_mark.get(day, [])
        living = [s for s in self.students if not s.archived]
        if not candidates or not living:
            return
        lesson_id, student_id = self.rng.choice(candidates)
        other = self.rng.choice(living)
        if other.id == student_id:
            return
        cur.execute(
            "SELECT teacher_id, room_id, branch_id, discipline_id, starts_at, ends_at "
            "FROM lesson WHERE id = %s",
            (lesson_id,),
        )
        base = cur.fetchone()
        if base is None:
            return
        guarded(
            self.stats, cur, "overbook", cur.execute,
            """
            INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, discipline_id,
                                student_id, kind, starts_at, ends_at, status, overbook_ack)
            VALUES (%s, %s, %s, %s, %s, %s, 'extra', %s, %s, 'planned', true)
            """,
            (self.w.tenant_id, base["branch_id"], base["teacher_id"], base["room_id"],
             base["discipline_id"], other.id, base["starts_at"], base["ends_at"]),
        )
        self.stats["занятий.овербукинг подтверждён"] += 1

    # -- один день ---------------------------------------------------------

    def run_day(self, cur: psycopg.Cursor, day: dt.date) -> None:
        # Прошлое живёт полностью; будущее — только расписание. Заводить
        # заявку «послезавтра» значило бы придумать события, которых ещё
        # не было, и отчёт по воронке начал бы врать в обе стороны.
        if day <= self.today:
            if day.weekday() != 6:
                self.spawn_leads(cur, day)
            self.advance_leads(cur, day)
            self.renew_subscriptions(cur, day)
            self.mark_day(cur, day)
            self.spend_makeups(cur, day)
            if self.rng.random() < 0.03:
                self.overbook(cur, day)
            if self.rng.random() < 0.15:
                self.freeze_holidays(cur, day)

        # Горизонт расписания — три недели вперёд, как в spec.md §5.1:
        # ночное задание достраивает материализацию.
        horizon_day = day + self.horizon
        if horizon_day <= self.end:
            for student in self.students:
                self.materialize(cur, student, horizon_day)

    def run(self) -> None:
        day = self.start
        total = (self.end - self.start).days + 1
        step = 0
        while day <= self.end:
            mark = db_now(self.admin)
            with db.tenant_tx(self.w.tenant_id) as cur:
                self.run_day(cur, day)
            stamp_day(self.admin, self.w.tenant_id, mark, day, self.today)
            step += 1
            if step % 30 == 0 or day == self.end:
                active = sum(1 for s in self.students if not s.archived)
                print(f"    {day}  ({step}/{total})  учеников: {active}", flush=True)
            day += dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# Закрытие зарплатных периодов
# ---------------------------------------------------------------------------


def close_payroll(
    tenant_id: str, admin_id: str, start: dt.date, today: dt.date, stats: Stats
) -> None:
    """Помесячное закрытие ведомости.

    Кода в приложении нет: таблица `payroll_period` в схеме есть, эндпоинта
    нет (backend/README.md, «не входило в этап»). Закрываются только
    полностью прошедшие месяцы — закрыть текущий значило бы зафиксировать
    ведомость, в которую ещё придут начисления.
    """
    month = dt.date(start.year, start.month, 1)
    with db.tenant_tx(tenant_id) as cur:
        while True:
            nxt = dt.date(month.year + month.month // 12, month.month % 12 + 1, 1)
            if nxt > dt.date(today.year, today.month, 1):
                break
            cur.execute(
                """INSERT INTO payroll_period (tenant_id, period, closed_at, closed_by)
                   VALUES (%s, daterange(%s, %s, '[)'), %s, %s) RETURNING id""",
                (tenant_id, month, nxt, _at(nxt, 10), admin_id),
            )
            period_id = str(cur.fetchone()["id"])
            # Начисление попадает в период по дате самого начисления, а не
            # по дате занятия: корректировка старой отметки обязана уйти
            # в текущий период, иначе закрытая ведомость пересчиталась бы.
            cur.execute(
                """UPDATE payroll_entry SET period_id = %s
                    WHERE tenant_id = %s AND period_id IS NULL
                      AND created_at >= %s AND created_at < %s""",
                (period_id, tenant_id, _at(month, 0), _at(nxt, 0)),
            )
            stats["зарплата.периодов закрыто"] += 1
            stats["зарплата.начислений в закрытых"] += cur.rowcount
            month = nxt


# ---------------------------------------------------------------------------
# Замеры
# ---------------------------------------------------------------------------


def _measurement_session(tenant_id: str, admin_id: str) -> dict[str, str]:
    """Короткая сессия для замеров. Токен нигде не сохраняется.

    Строка `user_session` ничем не отличается от выданной входом: приложение
    проверяет её теми же двумя запросами, и замер поэтому меряет в том числе
    стоимость проверки сессии — то есть то, что заплатит настоящий запрос.
    """
    token = auth.new_session_token()
    with db.untenanted_tx() as cur:
        cur.execute(
            """INSERT INTO user_session (tenant_id, user_id, token_hash, expires_at)
               VALUES (%s, %s, %s, now() + interval '1 day')""",
            (tenant_id, admin_id, auth.hash_token(token)),
        )
    return {"Authorization": f"Bearer {token}"}


def measure(tenant_id: str, admin_id: str, branch_id: str, start: dt.date, end: dt.date) -> None:
    """Сколько занимают главные экраны на полной истории.

    Меряется настоящий маршрут FastAPI: TestClient идёт через тот же ASGI-стек,
    что и uvicorn. Интерфейсу важно время ответа целиком — вместе со сборкой
    карточки и сериализацией, а не время отдельного SELECT.
    """
    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    # Заголовков-заглушек больше нет: API узнаёт школу из сессии. Замер идёт
    # тем же путём, что и живой запрос, поэтому сессия здесь настоящая —
    # просто выданная напрямую, без SMS.
    headers = _measurement_session(tenant_id, admin_id)

    with db.tenant_tx(tenant_id) as cur:
        cur.execute(
            """SELECT (starts_at AT TIME ZONE 'Asia/Almaty')::date AS day, count(*) AS n
                 FROM lesson WHERE branch_id = %s AND status <> 'cancelled'
                GROUP BY 1 ORDER BY n DESC LIMIT 1""",
            (branch_id,),
        )
        busiest = cur.fetchone()
        cur.execute(
            """SELECT s.student_id, count(*) AS n
                 FROM subscription_entry se
                 JOIN subscription s ON s.id = se.subscription_id
                GROUP BY 1 ORDER BY n DESC LIMIT 1"""
        )
        heaviest = cur.fetchone()

    with TestClient(fastapi_app) as client:
        cases = [
            (f"расписание дня ({busiest['n']} занятий, {busiest['day']})",
             lambda: client.get(
                 f"/api/v1/schedule?branch_id={branch_id}&date={busiest['day']}",
                 headers=headers)),
            (f"карточка ученика ({heaviest['n']} движений в журнале)",
             lambda: client.get(f"/api/v1/students/{heaviest['student_id']}", headers=headers)),
            (f"отчёт по воронке за период ({start} - {end})",
             lambda: client.get(f"/api/v1/leads/funnel?from={start}&to={end}", headers=headers)),
            ("доска воронки",
             lambda: client.get("/api/v1/leads", headers=headers)),
            ("поиск ученика по фамилии",
             lambda: client.get("/api/v1/students?query=Ким", headers=headers)),
        ]

        print("\nЗамеры (7 прогонов, первый — холодный — отброшен), мс:")
        print(f"  {'экран':<48} {'мин':>7} {'медиана':>9} {'макс':>8}")
        for title, call in cases:
            samples = []
            for index in range(7):
                started = time.perf_counter()
                response = call()
                elapsed = (time.perf_counter() - started) * 1000
                assert response.status_code == 200, (title, response.status_code,
                                                     response.text[:300])
                if index:
                    samples.append(elapsed)
            print(f"  {title:<48} {min(samples):7.1f} {statistics.median(samples):9.1f} "
                  f"{max(samples):8.1f}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def tenants_exist(conn: psycopg.Connection) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM tenant WHERE id IN (%s, %s)", (SIM_TENANT_A, SIM_TENANT_B))
    return int(cur.fetchone()[0]) > 0


def purge(conn: psycopg.Connection) -> None:
    cur = conn.cursor()
    # Каскад задевает журналы, защищённые триггером «только на добавление».
    # Люк предусмотрен схемой ровно для удаления тенанта.
    cur.execute("SELECT set_config('app.allow_purge', 'on', false)")
    cur.execute("DELETE FROM tenant WHERE id IN (%s, %s)", (SIM_TENANT_A, SIM_TENANT_B))
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="История работы школы за полгода — через код приложения",
    )
    parser.add_argument("--students", type=int, default=165,
                        help="сколько учеников должно прийти за период (по умолчанию 165)")
    parser.add_argument("--months", type=int, default=6,
                        help="длительность истории в месяцах (по умолчанию 6)")
    parser.add_argument("--seed", type=int, default=20260811,
                        help="зерно случайности: один и тот же прогон воспроизводится")
    parser.add_argument("--future-days", type=int, default=21,
                        help="на сколько дней вперёд построить расписание")
    parser.add_argument("--reset", action="store_true",
                        help="снести прежние данные симуляции и начать заново")
    parser.add_argument("--no-measure", action="store_true", help="пропустить замеры")
    parser.add_argument("--measure-only", action="store_true",
                        help="только замеры на уже сгенерированных данных")
    args = parser.parse_args()

    # Консоль Windows живёт в cp1251: без замены неотображаемых символов
    # прогон падал бы на печати отчёта, а не на данных.
    sys.stdout.reconfigure(errors="replace")

    with psycopg.connect(admin_dsn()) as admin:
        # Симуляция идёт вперёд от сегодняшнего дня, а в конце вся история
        # переносится назад на `past` дней. Почему не сразу задним числом —
        # см. «Как получается прошлое» в шапке файла.
        run_from = db_now(admin).astimezone(TZ).date()
        past = int(args.months * 30.44)
        present = run_from + dt.timedelta(days=past)          # «сегодня» внутри симуляции
        run_to = present + dt.timedelta(days=args.future_days)

        # Каким календарь станет после переноса.
        start = run_from - dt.timedelta(days=past)
        today = run_from
        end = run_to - dt.timedelta(days=past)

        if args.measure_only:
            measure(SIM_TENANT_A, SIM_ADMIN_A, SIM_BRANCH_A1, start, today)
            db.close_pool()
            return

        if tenants_exist(admin):
            if not args.reset:
                print("Данные симуляции уже есть. Повторный прогон — с --reset.", file=sys.stderr)
                sys.exit(2)
            purge(admin)

        print(f"История: {start} -> {end}  (сегодня {today}), зерно {args.seed}")
        print(f"  моделируется вперёд как {run_from} -> {run_to}, "
              f"затем переносится назад на {past} дней")
        started = time.perf_counter()

        stats_a = Stats()
        world_a = build_world(
            admin, tenant_id=SIM_TENANT_A, admin_id=SIM_ADMIN_A,
            slug="sim-rockschool", name="RockSchool История",
            branch_ids=[SIM_BRANCH_A1, SIM_BRANCH_A2],
            branch_names=["Аль-Фараби 53В", "Абая 150"],
            phone_prefix="+7700", carry_over_lessons=2,
        )
        print(f"  школа 1: 2 филиала, {len(world_a.teachers)} преподавателей, "
              f"{len(world_a.plans)} тарифов")
        Simulation(
            world_a, rng=random.Random(args.seed), admin=admin,
            start=run_from, end=run_to, today=present,
            target_students=args.students, phone_prefix="+7700", stats=stats_a,
        ).run()

        stats_b = Stats()
        world_b = build_world(
            admin, tenant_id=SIM_TENANT_B, admin_id=SIM_ADMIN_B,
            slug="sim-other-school", name="Соседняя школа История",
            branch_ids=[SIM_BRANCH_B1], branch_names=["Сейфуллина 500"],
            phone_prefix="+7705", carry_over_lessons=0,
        )
        print("  школа 2 (существует ради проверки изоляции)")
        Simulation(
            world_b, rng=random.Random(args.seed + 1), admin=admin,
            start=run_from, end=run_to, today=present,
            target_students=max(20, args.students // 6), phone_prefix="+7705", stats=stats_b,
        ).run()

        # Перенос в прошлое — и только после него закрытие ведомости:
        # период обязан лечь на календарные месяцы настоящей истории,
        # а не той, в которой она моделировалась.
        print("  перенос истории в прошлое...")
        for tenant_id in (SIM_TENANT_A, SIM_TENANT_B):
            shift_timeline(admin, tenant_id, past)
        close_payroll(SIM_TENANT_A, SIM_ADMIN_A, start, today, stats_a)
        close_payroll(SIM_TENANT_B, SIM_ADMIN_B, start, today, stats_b)

        elapsed = time.perf_counter() - started
        stats_a.show(f"Школа 1 — {SIM_TENANT_A}")
        stats_b.show(f"Школа 2 — {SIM_TENANT_B}")
        print(f"\nсгенерировано за {elapsed / 60:.1f} мин")

    if not args.no_measure:
        measure(SIM_TENANT_A, SIM_ADMIN_A, SIM_BRANCH_A1, start, today)
    db.close_pool()


if __name__ == "__main__":
    main()
