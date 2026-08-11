"""Демонстрационные данные: 12 августа 2026, филиал Аль-Фараби.

Наполняет базу тем же днём, который нарисован в prototype/index.html, чтобы
фронтенд мог переключиться с моков на живой API и увидеть знакомый экран:
те же преподаватели, те же занятия, тот же конфликт кабинета в 13:00.

    python -m scripts.seed_demo

Скрипт сносит и заново создаёт оба демо-тенанта целиком. На боевой базе
запускать нечего.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402

# --- идентификаторы, на которые может опереться фронтенд и тесты -------------
TENANT = "0189b0de-0000-7000-8000-00000000000a"   # RockSchool Алматы
TENANT_OTHER = "0189b0de-0000-7000-8000-00000000000b"  # соседняя школа, для проверки изоляции

BRANCH_AF = "0189b0de-0000-7000-8000-0000000000b1"
BRANCH_AB = "0189b0de-0000-7000-8000-0000000000b2"
BRANCH_OTHER = "0189b0de-0000-7000-8000-0000000000bf"

ADMIN_USER = "0189b0de-0000-7000-8000-0000000000u1".replace("u", "9")
OTHER_USER = "0189b0de-0000-7000-8000-0000000000u2".replace("u", "9")


def _id(suffix: str) -> str:
    """Собирает узнаваемый UUID из трёхсимвольного суффикса.

    Фиксированные идентификаторы нужны, чтобы фронтенд и curl-примеры
    в README не переписывались после каждого пересева.
    """
    assert len(suffix) == 3, suffix
    return f"0189b0de-0000-7000-8000-00000000a{suffix}"


# Занятие соседней школы. Существует ровно затем, чтобы проверять, что оно
# не видно из RockSchool, и что при своих заголовках оно всё-таки читается.
OTHER_LESSON = _id("4ff")
OTHER_STUDENT = _id("2fe")

ROOMS = {
    "drum_a": _id("0c1"),
    "class1": _id("0c2"),
    "class2": _id("0c3"),
    "drum_b": _id("0c4"),
    "class3": _id("0c5"),
}

DISC = {
    "drums": _id("0d1"),
    "guitar": _id("0d2"),
    "vocal": _id("0d3"),
    "piano": _id("0d4"),
    "ukulele": _id("0d5"),
    "bass": _id("0d6"),
    "perc": _id("0d7"),
}

# Преподаватели: имена и цвета дорожек взяты из прототипа — расписание должно
# остаться узнаваемым при переходе с моков на живые данные.
TEACHERS = [
    ("sharapov", "Дмитрий", "Шарапов", "#A65D3F", ["drums"], 4500),
    ("fedko", "Глеб", "Федько", "#2F7D7A", ["guitar", "ukulele"], 4200),
    ("madratov", "Егор", "Мадратов", "#4B6489", ["drums", "perc"], 4500),
    ("merenkov", "Андрей", "Меренков", "#4E7A3E", ["guitar", "bass"], 4200),
    ("isenova", "Алия", "Исенова", "#7C4A72", ["vocal", "piano"], 4000),
]

# Ученики: (ключ, имя, фамилия, направление, куплено, уже списано)
# «уже списано» задаёт остаток и вместе с ним — состояние экрана:
# у Ольги Ким остаток 2 (алерт о продлении), у Дмитрия Со ноль (списывать
# нечего, отметка «пришёл» обязана дать 422), у Амира Жаната абонемента нет.
STUDENTS = [
    ("ahmetov", "Тимур", "Ахметов", "guitar", 8, 2),
    ("sagyndyk", "Амина", "Сагындык", "drums", 8, 3),
    ("kim_d", "Даниал", "Ким", "guitar", 8, 1),
    ("ospanov", "Ержан", "Оспанов", "drums", 8, 4),
    ("nurlan", "Сабина", "Нурлан", "guitar", 8, 5),
    ("bek", "Айсулу", "Бек", "vocal", 8, 0),
    ("li_m", "Марк", "Ли", "drums", 8, 3),
    ("zhanat", "Амир", "Жанат", "drums", 0, 0),      # без абонемента — разовая оплата
    ("kim_o", "Ольга", "Ким", "guitar", 8, 6),        # остаток 2 → алерт
    ("so", "Дмитрий", "Со", "vocal", 8, 8),           # остаток 0 → 422 при списании
    ("seit", "Арман", "Сеит", "guitar", 8, 2),
    ("kim_zh", "Жанна", "Ким", "guitar", 8, 2),
    ("bek_n", "Нурлан", "Бек", "guitar", 8, 1),
    ("murat", "Сая", "Мурат", "guitar", 8, 3),
    ("toktar", "Арай", "Токтар", "drums", 8, 2),
    ("er", "Камила", "Ер", "vocal", 8, 4),
    ("li_r", "Рустам", "Ли", "guitar", 8, 1),
    ("aman", "Данияр", "Аман", "guitar", 8, 5),
]

GROUP_ENSEMBLE = _id("070")
ENSEMBLE_MEMBERS = ["seit", "kim_zh", "bek_n", "murat"]

LEAD_ALISA = _id("071")
LEAD_ILYAS = _id("072")

# Занятия 12 августа 2026. Время местное, как его видит администратор.
#
# Смещение НЕ пишем цифрой: Казахстан с марта 2024 живёт в едином поясе UTC+5,
# и захардкоженный «+06» сдвинул бы всё расписание на час назад. Зону задаём
# именем — тогда правила смещения берёт база и они остаются верными,
# что бы ни решили с переводом часов дальше.
TZ = "Asia/Almaty"
# (ключ, филиал, преподаватель, кабинет, минуты, начало, кого учим, вид, отметка)
LESSONS = [
    ("les01", BRANCH_AF, "fedko", "class1", 55, "10:00", ("student", "ahmetov"), "regular", "came"),
    ("les02", BRANCH_AF, "sharapov", "drum_a", 55, "11:00", ("student", "sagyndyk"), "regular", None),
    ("les03", BRANCH_AF, "merenkov", "class2", 85, "11:00", ("student", "kim_d"), "regular", None),
    ("les04", BRANCH_AF, "sharapov", "drum_a", 55, "12:30", ("student", "ospanov"), "regular", None),
    # Пробный в занятой Барабанной A — тот самый конфликт из прототипа.
    # Он попал в базу только потому, что администратор подтвердил овербукинг.
    ("les05", BRANCH_AF, "madratov", "drum_a", 45, "13:00", ("lead", LEAD_ALISA), "trial", None),
    ("les06", BRANCH_AF, "fedko", "class1", 55, "14:00", ("student", "nurlan"), "regular", "no_show"),
    ("les07", BRANCH_AF, "isenova", "class2", 55, "15:00", ("student", "bek"), "regular", None),
    ("les08", BRANCH_AF, "sharapov", "drum_a", 85, "16:00", ("student", "li_m"), "regular", None),
    ("les09", BRANCH_AF, "merenkov", "class1", 55, "17:30", ("group", GROUP_ENSEMBLE), "regular", None),
    ("les10", BRANCH_AF, "sharapov", "drum_a", 55, "18:30", ("student", "zhanat"), "regular", None),
    ("les11", BRANCH_AF, "fedko", "class2", 55, "19:00", ("student", "kim_o"), "regular", None),
    ("les12", BRANCH_AF, "isenova", "class1", 55, "20:00", ("student", "so"), "regular", None),
    ("les13", BRANCH_AB, "madratov", "drum_b", 55, "11:00", ("student", "toktar"), "regular", None),
    ("les14", BRANCH_AB, "isenova", "class3", 55, "12:00", ("student", "er"), "regular", "came"),
    ("les15", BRANCH_AB, "fedko", "class3", 55, "15:00", ("student", "li_r"), "regular", None),
    ("les16", BRANCH_AB, "madratov", "drum_b", 45, "16:30", ("lead", LEAD_ILYAS), "trial", None),
    ("les17", BRANCH_AB, "merenkov", "class3", 55, "19:00", ("student", "aman"), "regular", None),
]

OVERBOOKED = {"les05"}  # осознанный овербукинг: иначе база не даст вставить

DAY = "2026-08-12"


def teacher_id(key: str) -> str:
    return _id("0e" + str(1 + [t[0] for t in TEACHERS].index(key)))


def person_id(key: str) -> str:
    keys = [t[0] for t in TEACHERS] + [s[0] for s in STUDENTS]
    return _id(f"1{keys.index(key):02d}")


def student_id(key: str) -> str:
    return _id(f"2{[s[0] for s in STUDENTS].index(key):02d}")


def subscription_id(key: str) -> str:
    return _id(f"3{[s[0] for s in STUDENTS].index(key):02d}")


def lesson_id(key: str) -> str:
    return _id(f"4{[l[0] for l in LESSONS].index(key):02d}")


def lesson_discipline(target: tuple[str, str]) -> str:
    """Направление занятия. Нужно для подбора ставки и требований к кабинету."""
    kind, value = target
    if kind == "student":
        return DISC[dict((s[0], s[3]) for s in STUDENTS)[value]]
    if kind == "lead":
        return DISC["drums"]      # обе заявки пришли на барабаны
    return DISC["guitar"]         # ансамбль


def seed(conn: psycopg.Connection) -> None:
    cur = conn.cursor()

    # Каскадное удаление тенанта задевает журналы, которые защищены триггером
    # «только на добавление». Люк предусмотрен схемой ровно для этого случая.
    cur.execute("SELECT set_config('app.allow_purge', 'on', false)")
    cur.execute("DELETE FROM tenant WHERE id IN (%s, %s)", (TENANT, TENANT_OTHER))

    cur.execute(
        """
        INSERT INTO tenant (id, slug, name, timezone) VALUES
          (%s, 'rockschool-demo', 'RockSchool Алматы', 'Asia/Almaty'),
          (%s, 'other-school-demo', 'Соседняя школа', 'Asia/Almaty')
        """,
        (TENANT, TENANT_OTHER),
    )

    cur.execute(
        """
        INSERT INTO branch (id, tenant_id, name, address, opens_at, closes_at) VALUES
          (%s, %s, 'Аль-Фараби 53В', 'пр. Аль-Фараби, 53В', '10:00', '21:00'),
          (%s, %s, 'Абая 150',       'пр. Абая, 150',       '10:00', '21:00'),
          (%s, %s, 'Чужой филиал',   NULL,                  '10:00', '21:00')
        """,
        (BRANCH_AF, TENANT, BRANCH_AB, TENANT, BRANCH_OTHER, TENANT_OTHER),
    )

    cur.execute(
        """
        INSERT INTO room (id, tenant_id, branch_id, name, features) VALUES
          (%(drum_a)s, %(t)s, %(af)s, 'Барабанная A', '{"drum_kit": true, "soundproof": true}'),
          (%(class1)s, %(t)s, %(af)s, 'Класс 1',      '{"piano": true}'),
          (%(class2)s, %(t)s, %(af)s, 'Класс 2',      '{}'),
          (%(drum_b)s, %(t)s, %(ab)s, 'Барабанная B', '{"drum_kit": true, "soundproof": true}'),
          (%(class3)s, %(t)s, %(ab)s, 'Класс 3',      '{}')
        """,
        {"t": TENANT, "af": BRANCH_AF, "ab": BRANCH_AB, **ROOMS},
    )

    for order, (key, name, min_age, reqs) in enumerate(
        [
            ("drums", "Барабаны", 5, '{"drum_kit": true}'),
            ("guitar", "Гитара", 6, "{}"),
            ("vocal", "Вокал", 6, "{}"),
            ("piano", "Фортепиано", 5, '{"piano": true}'),
            ("ukulele", "Укулеле", 5, "{}"),
            ("bass", "Бас-гитара", 8, "{}"),
            ("perc", "Перкуссия", 6, "{}"),
        ]
    ):
        cur.execute(
            """INSERT INTO discipline (id, tenant_id, name, min_age, room_reqs, sort_order)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (DISC[key], TENANT, name, min_age, reqs, order),
        )

    # --- преподаватели --------------------------------------------------
    for idx, (key, first, last, color, disciplines, rate) in enumerate(TEACHERS, start=1):
        pid, sid = person_id(key), teacher_id(key)
        cur.execute(
            """INSERT INTO person (id, tenant_id, first_name, last_name, phone, pd_consent_at)
               VALUES (%s, %s, %s, %s, %s, now())""",
            (pid, TENANT, first, last, f"+7701555{idx:04d}"),
        )
        cur.execute(
            """INSERT INTO staff (id, tenant_id, person_id, kind, color, hired_on)
               VALUES (%s, %s, %s, 'teacher', %s, '2024-09-01')""",
            (sid, TENANT, pid, color),
        )
        for d in disciplines:
            cur.execute(
                "INSERT INTO staff_discipline (staff_id, discipline_id) VALUES (%s, %s)",
                (sid, DISC[d]),
            )
        for b in (BRANCH_AF, BRANCH_AB):
            cur.execute("INSERT INTO staff_branch (staff_id, branch_id) VALUES (%s, %s)", (sid, b))

        # Ставки: индивидуальное занятие и пробный. Пробный дешевле — это
        # презентация школы, а не урок.
        cur.execute(
            """INSERT INTO teacher_rate (tenant_id, staff_id, format, amount, valid_from)
               VALUES (%s, %s, 'individual', %s, '2024-09-01'),
                      (%s, %s, 'trial',      %s, '2024-09-01'),
                      (%s, %s, 'group',      %s, '2024-09-01')""",
            (TENANT, sid, rate, TENANT, sid, 2000, TENANT, sid, rate + 800),
        )

    # --- администратор, от чьего имени идут отметки ----------------------
    admin_person = _id("1ff")
    cur.execute(
        """INSERT INTO person (id, tenant_id, first_name, last_name, phone, pd_consent_at)
           VALUES (%s, %s, 'Асель', 'Нурланова', '+77015550100', now())""",
        (admin_person, TENANT),
    )
    cur.execute(
        """INSERT INTO app_user (id, tenant_id, person_id, login, role)
           VALUES (%s, %s, %s, '+77015550100', 'admin')""",
        (ADMIN_USER, TENANT, admin_person),
    )

    # --- тариф и ученики -------------------------------------------------
    plan_id = _id("05a")
    cur.execute(
        """INSERT INTO subscription_plan
             (id, tenant_id, name, format, duration_min, lessons_count, valid_days, price)
           VALUES (%s, %s, '8 занятий, 55 минут', 'individual', 55, 8, 31, 54000)""",
        (plan_id, TENANT),
    )

    for idx, (key, first, last, disc, bought, used) in enumerate(STUDENTS, start=1):
        pid, sid = person_id(key), student_id(key)
        cur.execute(
            """INSERT INTO person (id, tenant_id, first_name, last_name, phone, birth_date, pd_consent_at)
               VALUES (%s, %s, %s, %s, %s, '2014-05-12', now())""",
            (pid, TENANT, first, last, f"+7702555{idx:04d}"),
        )
        cur.execute(
            """INSERT INTO student (id, tenant_id, person_id, branch_id, discipline_id, started_on)
               VALUES (%s, %s, %s, %s, %s, '2026-02-04')""",
            (sid, TENANT, pid, BRANCH_AF, DISC[disc]),
        )
        if bought == 0:
            continue
        sub = subscription_id(key)
        # rules копируются из настроек школы на момент продажи — дальше
        # абонемент живёт по ним, что бы школа ни поменяла у себя (spec §4.2).
        cur.execute(
            """INSERT INTO subscription
                 (id, tenant_id, student_id, plan_id, lessons_total, price, rules,
                  valid_from, valid_until, sold_by)
               SELECT %s, %s, %s, %s, %s, 54000, default_rules,
                      '2026-08-01', '2026-08-31', %s
               FROM tenant WHERE id = %s""",
            (sub, TENANT, sid, plan_id, bought, ADMIN_USER, TENANT),
        )
        cur.execute(
            """INSERT INTO subscription_entry
                 (tenant_id, subscription_id, kind, lessons_delta, reason, created_by)
               VALUES (%s, %s, 'purchase', %s, 'Продажа абонемента', %s)""",
            (TENANT, sub, bought, ADMIN_USER),
        )
        if used:
            cur.execute(
                """INSERT INTO subscription_entry
                     (tenant_id, subscription_id, kind, lessons_delta, reason, created_by)
                   VALUES (%s, %s, 'charge', %s, 'Занятия до 12 августа', %s)""",
                (TENANT, sub, -used, ADMIN_USER),
            )

    # Амина Сагындык — герой прототипа: у неё есть и отработка.
    cur.execute(
        """INSERT INTO subscription_entry
             (tenant_id, subscription_id, kind, makeups_delta, reason, created_by)
           VALUES (%s, %s, 'makeup_grant', 1, 'Отмена за 2 дня, 2 августа', %s)""",
        (TENANT, subscription_id("sagyndyk"), ADMIN_USER),
    )

    # --- группа ----------------------------------------------------------
    cur.execute(
        """INSERT INTO study_group (id, tenant_id, name, discipline_id, max_size)
           VALUES (%s, %s, 'Ансамбль', %s, 6)""",
        (GROUP_ENSEMBLE, TENANT, DISC["guitar"]),
    )
    for key in ENSEMBLE_MEMBERS:
        cur.execute(
            """INSERT INTO group_member (group_id, student_id, joined_on)
               VALUES (%s, %s, '2026-03-01')""",
            (GROUP_ENSEMBLE, student_id(key)),
        )

    # --- заявки под пробные ----------------------------------------------
    cur.execute(
        """INSERT INTO lead (id, tenant_id, name, phone, student_name, student_age,
                             discipline_id, branch_id, stage, source) VALUES
             (%s, %s, 'Алиса Ким',  '+77013330001', 'Алиса Ким',  9, %s, %s, 'trial_booked', 'telegram_bot'),
             (%s, %s, 'Ильяс Абен', '+77013330002', 'Ильяс Абен', 11, %s, %s, 'trial_booked', 'instagram')""",
        (LEAD_ALISA, TENANT, DISC["drums"], BRANCH_AF,
         LEAD_ILYAS, TENANT, DISC["drums"], BRANCH_AB),
    )

    # --- занятия ---------------------------------------------------------
    for key, branch, teacher, room, minutes, start, target, kind, mark in LESSONS:
        target_kind, target_id = target
        cur.execute(
            """INSERT INTO lesson (id, tenant_id, branch_id, teacher_id, room_id, discipline_id,
                                   student_id, group_id, lead_id, kind, starts_at, ends_at,
                                   status, overbook_ack)
               VALUES (%(id)s, %(t)s, %(b)s, %(tc)s, %(rm)s, %(disc)s,
                       %(st)s, %(gr)s, %(ld)s, %(kind)s,
                       %(start)s::timestamp AT TIME ZONE %(tz)s,
                       (%(start)s::timestamp AT TIME ZONE %(tz)s) + make_interval(mins => %(min)s),
                       'planned', %(ack)s)""",
            {
                "id": lesson_id(key),
                "t": TENANT,
                "b": branch,
                "tc": teacher_id(teacher),
                "rm": ROOMS[room],
                "disc": lesson_discipline(target),
                "st": student_id(target_id) if target_kind == "student" else None,
                "gr": target_id if target_kind == "group" else None,
                "ld": target_id if target_kind == "lead" else None,
                "kind": kind,
                "start": f"{DAY} {start}",
                "tz": TZ,
                "min": minutes,
                "ack": key in OVERBOOKED,
            },
        )

    # --- уже проставленные отметки ---------------------------------------
    # Пара занятий приходит из прошлого отмеченными: расписание обязано
    # показывать не только «что будет», но и «что уже случилось».
    for key, mark in [(l[0], l[8]) for l in LESSONS if l[8]]:
        lid = lesson_id(key)
        student_key = dict((l[0], l[6][1]) for l in LESSONS)[key]
        sid = student_id(student_key)
        cur.execute(
            """INSERT INTO attendance (tenant_id, lesson_id, student_id, mark, marked_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (TENANT, lid, sid, mark, ADMIN_USER),
        )
        att_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO subscription_entry
                 (tenant_id, subscription_id, kind, lessons_delta, attendance_id, lesson_id,
                  reason, created_by)
               VALUES (%s, %s, 'charge', -1, %s, %s, %s, %s)""",
            (TENANT, subscription_id(student_key), att_id, lid, f"Отметка «{mark}»", ADMIN_USER),
        )
        teacher_key = dict((l[0], l[2]) for l in LESSONS)[key]
        rate = dict((t[0], t[5]) for t in TEACHERS)[teacher_key]
        cur.execute(
            """INSERT INTO payroll_entry
                 (tenant_id, staff_id, lesson_id, attendance_id, kind, amount, calc)
               VALUES (%s, %s, %s, %s, 'lesson', %s,
                       '{"kind":"fixed","share":1.0,"seeded":true}')""",
            (TENANT, teacher_id(teacher_key), lid, att_id, rate),
        )
        cur.execute("UPDATE lesson SET status = 'held' WHERE id = %s", (lid,))

    cur.execute(
        """INSERT INTO lesson_note (tenant_id, lesson_id, student_id, author_id, body, homework, tags)
           VALUES (%s, %s, %s, %s,
                   'Разобрали переход между рифами, правая рука держит темп ровнее.',
                   'Метроном 80 bpm, восьмые по 5 минут ежедневно.',
                   ARRAY['Nirvana — Smells Like Teen Spirit', 'Single Paradiddle'])""",
        (TENANT, lesson_id("les01"), student_id("ahmetov"), teacher_id("fedko")),
    )

    # --- соседняя школа: нужна ровно затем, чтобы её данные не было видно ---
    other_person = _id("1fe")
    other_admin_person = _id("1fd")
    other_staff = _id("0ef")
    other_student = _id("2ff")
    cur.execute(
        """INSERT INTO person (id, tenant_id, first_name, last_name) VALUES
             (%s, %s, 'Чужой', 'Преподаватель'),
             (%s, %s, 'Чужой', 'Ученик'),
             (%s, %s, 'Чужой', 'Администратор')""",
        (other_person, TENANT_OTHER, other_student, TENANT_OTHER,
         other_admin_person, TENANT_OTHER),
    )
    cur.execute(
        """INSERT INTO app_user (id, tenant_id, person_id, login, role)
           VALUES (%s, %s, %s, 'other-admin', 'admin')""",
        (OTHER_USER, TENANT_OTHER, other_admin_person),
    )
    cur.execute(
        "INSERT INTO staff (id, tenant_id, person_id, kind) VALUES (%s, %s, %s, 'teacher')",
        (other_staff, TENANT_OTHER, other_person),
    )
    other_room = _id("0cf")
    cur.execute(
        "INSERT INTO room (id, tenant_id, branch_id, name) VALUES (%s, %s, %s, 'Чужой кабинет')",
        (other_room, TENANT_OTHER, BRANCH_OTHER),
    )
    cur.execute(
        "INSERT INTO student (id, tenant_id, person_id) VALUES (%s, %s, %s)",
        (OTHER_STUDENT, TENANT_OTHER, other_student),
    )
    cur.execute(
        """INSERT INTO lesson (id, tenant_id, branch_id, teacher_id, room_id, student_id,
                               starts_at, ends_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s::timestamp AT TIME ZONE %s,
                   (%s::timestamp AT TIME ZONE %s) + make_interval(mins => 55))""",
        (OTHER_LESSON, TENANT_OTHER, BRANCH_OTHER, other_staff, other_room, OTHER_STUDENT,
         f"{DAY} 11:00", TZ, f"{DAY} 11:00", TZ),
    )

    conn.commit()


def main() -> None:
    target = config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME
    with psycopg.connect(target) as conn:
        seed(conn)
    print(f"демо-данные загружены: {DAY}, тенант {TENANT}")
    print(f"  X-Tenant-Id: {TENANT}")
    print(f"  X-User-Id:   {ADMIN_USER}")
    print(f"  branch_id:   {BRANCH_AF}  (Аль-Фараби 53В)")


if __name__ == "__main__":
    main()
