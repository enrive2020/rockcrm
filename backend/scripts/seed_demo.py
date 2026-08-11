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

from app import api_keys, config  # noqa: E402

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
#
# Новых учеников добавлять ТОЛЬКО в конец: student_id() и subscription_id()
# собираются из позиции в этом списке, и вставка в середину переименовала бы
# половину демо-данных, на которые ссылаются README, тесты и фронтенд.
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
    # Брат Амины. Ради него в демо есть семья со скидкой за второго ребёнка —
    # без второго ребёнка эту скидку негде показать.
    ("sagyndyk_t", "Тимур", "Сагындык", "guitar", 8, 5),
]

# Даты рождения. Возраст виден в карточке и в поиске, поэтому «всем по 12»
# из прежней версии не годится: прототип показывает Амину 9 лет и Тимура 12.
BIRTH_DEFAULT = "2014-05-12"
BIRTHDAYS = {
    "sagyndyk": "2016-11-03",     # 9 лет на демо-дне
    "sagyndyk_t": "2014-03-15",   # 12 лет
    "kim_d": "2013-07-21",
    "zhanat": "2017-01-30",
    "so": "2008-09-09",
}

# Основной преподаватель по направлению. Карточка ученика обязана показать,
# к кому он ходит, а не пустую строку.
MAIN_TEACHER = {
    "drums": "sharapov",
    "guitar": "fedko",
    "vocal": "isenova",
}

# Семья Сагындык — герой экрана «Ученик»: плательщик, двое детей,
# скидка за второго ребёнка.
FAMILY_SAGYNDYK = _id("06a")
PAYER_SAGYNDYK = _id("1fa")
FAMILY_MEMBERS = ["sagyndyk", "sagyndyk_t"]
FAMILY_DISCOUNT = 10

# Тарифы для формы продажи. Названия по прототипу: администратор выбирает
# из списка глазами, и «8 занятий, 55 минут» без направления ему не помогает.
# (ключ, название, направление, формат, минут, занятий, дней, цена)
PLANS = [
    ("drums8",  "Барабаны, 2 раза в неделю, 55 мин", "drums",  "individual", 55, 8, 31, 54000),
    ("drums4",  "Барабаны, 1 раз в неделю, 55 мин",  "drums",  "individual", 55, 4, 31, 29000),
    ("drums85", "Барабаны, 2 раза в неделю, 85 мин", "drums",  "individual", 85, 8, 31, 78000),
    ("guitar8", "Гитара, 2 раза в неделю, 55 мин",   "guitar", "individual", 55, 8, 31, 54000),
    ("guitar4", "Гитара, 1 раз в неделю, 55 мин",    "guitar", "individual", 55, 4, 31, 29000),
    ("vocal8",  "Вокал, 2 раза в неделю, 55 мин",    "vocal",  "individual", 55, 8, 31, 52000),
    ("ens8",    "Ансамбль, группа, 55 мин",          "guitar", "group",      55, 8, 31, 36000),
    ("trial1",  "Пробное занятие, 45 мин",           "drums",  "trial",      45, 1, 14,     0),
]

# Каким тарифом продан абонемент ученика — по его направлению.
PLAN_BY_DISCIPLINE = {"drums": "drums8", "guitar": "guitar8", "vocal": "vocal8"}

GROUP_ENSEMBLE = _id("070")
ENSEMBLE_MEMBERS = ["seit", "kim_zh", "bek_n", "murat"]

MANAGER_USER = "0189b0de-0000-7000-8000-0000000000u3".replace("u", "9")

# Ключи внешних источников. Открытый текст здесь известен заранее только
# потому, что это демо: тестам и curl-примерам нужен ключ, переживающий
# пересев. В базу и здесь уходит один хеш — восстановить ключ из неё нельзя.
# (открытый ключ, тенант, имя, области, отозван)
DEMO_API_KEYS = [
    ("rck_demo_rockschool_leads_key", None, "Telegram-бот", ["leads:write"], False),
    ("rck_demo_leadhub_key", None, "LeadHub", ["leads:write"], False),
    ("rck_demo_revoked_key", None, "Старая форма сайта", ["leads:write"], True),
    ("rck_demo_readonly_key", None, "Дашборд (только чтение)", ["leads:read"], False),
    ("rck_demo_other_school_key", "other", "Бот соседней школы", ["leads:write"], False),
]

# Сколько часов занимает путь от заявки до оплаты у тех, кто дошёл.
# Ровно 4 дня — та цифра, что стоит в прототипе под доской.
WON_PATH_HOURS = 96

# Путь заявки по стадиям. Отказ ставится с той стадии, на которой человек
# передумал: отказ после проведённого пробного и отказ после первого звонка —
# это разные проблемы школы, и отчёт обязан их различать.
LEAD_PATHS = {
    "new": ["new"],
    "contacting": ["new", "contacting"],
    "trial_booked": ["new", "contacting", "trial_booked"],
    "trial_held": ["new", "contacting", "trial_booked", "trial_held"],
    "won": ["new", "contacting", "trial_booked", "trial_held", "won"],
    "lost": ["new", "contacting", "lost"],
}

# Воронка демо-школы: доска из прототипа плюс отказы с причинами, без которых
# отчёт не показывает, что чинить.
# (ключ, имя, телефон, ученик, возраст, направление, филиал, источник,
#  стадия, причина отказа, попыток дозвона, ответственный, часов назад,
#  через сколько часов перезвонить)
LEADS = [
    # Пробный назначен — у Алисы он стоит в занятой Барабанной A (см. les05).
    ("alisa",   "Алиса Ким",       "+77013330001", "Алиса",   7, "drums",  "AF", "telegram_bot", "trial_booked", None, 1, "manager", 26, None),
    ("ilyas",   "Ильяс Абен",      "+77013330002", "Ильяс",  11, "drums",  "AB", "instagram",    "trial_booked", None, 1, "admin",   30, None),
    # Новые
    ("yerzhan", "Ержан Тулеу",     "+77013330003", "Ержан",  28, "guitar", "AF", "instagram",    "new",          None, 0, "manager",  4, None),
    ("madina",  "Мадина Абишева",  "+77013330004", "Мадина", 10, "vocal",  "AF", "site_form",    "new",          None, 2, "manager", 20, None),
    # Возраст ниже минимального для барабан (5 лет) — предупреждение, не отказ.
    ("aruzhan", "Аружан Сапар",    "+77013330005", "Аружан",  4, "drums",  "AF", "telegram_bot", "new",          None, 0, None,       2, None),
    # Дозвон. У Ольги напоминание в прошлом — карточка обязана загореться overdue.
    ("sanzhar", "Санжар Тлеу",     "+77013330006", "Санжар", 13, "drums",  "AF", "whatsapp",     "contacting",   None, 1, "admin",   34, 6),
    ("olga",    "Ольга Ким",       "+77013330007", "Ольга",  34, "piano",  "AF", "referral",     "contacting",   None, 1, "manager", 50, -2),
    # Пробный проведён — думают
    ("damir",   "Дамир Ералы",     "+77013330008", "Дамир",   9, "drums",  "AF", "site_form",    "trial_held",   None, 1, "admin",   72, 20),
    ("aisulu",  "Айсулу Бек",      "+77013330009", "Айсулу",  8, "vocal",  "AF", "referral",     "trial_held",   None, 1, "manager", 96, None),
    # Купили
    ("mark",    "Марк Ли",         "+77013330010", "Марк",   12, "drums",  "AF", "instagram",    "won",          None, 1, "admin",  240, None),
    ("danial",  "Даниал Ким",      "+77013330011", "Даниал", 11, "guitar", "AF", "telegram_bot", "won",          None, 1, "manager", 216, None),
    ("kamila",  "Камила Ер",       "+77013330012", "Камила", 14, "vocal",  "AF", "referral",     "won",          None, 1, "admin",  190, None),
    # Отказы с причинами
    ("nurbek",  "Нурбек Асан",     "+77013330013", "Нурбек",  9, "guitar", "AF", "telegram_bot", "lost", "price",     2, "admin",  150, None),
    ("dinara",  "Динара Ким",      "+77013330014", "Динара",  7, "vocal",  "AF", "site_form",    "lost", "schedule",  1, "manager", 130, None),
    ("ruslan",  "Руслан Ким",      "+77013330015", "Руслан", 15, "drums",  "AB", "whatsapp",     "lost", "no_answer", 3, "admin",  110, None),
    ("aliya",   "Алия Нур",        "+77013330016", "Алия",    6, "drums",  "AF", "instagram",    "lost", "price",     1, "manager", 100, None),
]


# Пробные уроки в расписании ссылаются на эти две заявки — отсюда и порядок
# в LEADS: первые две строки должны оставаться первыми.
LEAD_ALISA = _id("600")
LEAD_ILYAS = _id("601")

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

# Прошедшие занятия Амины Сагындык — те самые пять строк «Движения
# по абонементу» из прототипа. Журнал должен объяснять каждое занятие
# датой, преподавателем и причиной, а одна свёрнутая запись «списано 3»
# не объясняет ничего.
# (суффикс id, дата, время, отметка, статус занятия)
AMINA_PAST = [
    ("4a0", "2026-08-02", "11:00", "cancelled_early", "cancelled"),  # → отработка
    ("4a1", "2026-08-04", "11:00", "came", "held"),
    ("4a2", "2026-08-05", "11:00", "no_show", "held"),
    ("4a3", "2026-08-07", "11:00", "came", "held"),
    # Июльское занятие: нужно только как якорь для третьей заметки.
    # Отметки и движения по нему нет — оно относится к июльскому абонементу,
    # которого в демо нет.
    ("4a4", "2026-07-31", "11:00", None, "held"),
]

# Будущие занятия Амины. Нужны, чтобы заморозке было что отменять: без них
# «занятия внутри интервала отменяются» осталось бы непроверенным обещанием.
AMINA_UPCOMING = [("4b0", "2026-08-14"), ("4b1", "2026-08-19"), ("4b2", "2026-08-21")]

# Заметки к урокам с репертуаром — то, ради чего родитель заходит в кабинет.
# Тексты и теги взяты из прототипа.
AMINA_NOTES = [
    (
        "4a3",
        "Разобрали сбивку на 16-х. Правая рука зажимается на скорости выше "
        "90 bpm — держим метроном на 80.",
        "Метроном 80 bpm, восьмые и шестнадцатые по 10 минут в день.",
        ["Nirvana — Smells Like Teen Spirit", "Рудимент: Single Paradiddle", "80 bpm"],
    ),
    (
        "4a1",
        "Впервые сыграла припев целиком под минус. Готовим к отчётному "
        "концерту 27 сентября.",
        "Играть припев под минус, 3 прохода подряд без остановки.",
        ["Отчётный концерт", "Игра под минус"],
    ),
    (
        "4a4",
        "Постановка правой ноги на кике. Дома — 10 минут в день на восьмые.",
        "Восьмые правой ногой, 10 минут в день.",
        ["Техника: bass drum", "ДЗ: 10 мин/день"],
    ),
]


def teacher_id(key: str) -> str:
    return _id("0e" + str(1 + [t[0] for t in TEACHERS].index(key)))


def person_id(key: str) -> str:
    keys = [t[0] for t in TEACHERS] + [s[0] for s in STUDENTS]
    return _id(f"1{keys.index(key):02d}")


def student_id(key: str) -> str:
    return _id(f"2{[s[0] for s in STUDENTS].index(key):02d}")


def subscription_id(key: str) -> str:
    return _id(f"3{[s[0] for s in STUDENTS].index(key):02d}")


def lead_id(key: str) -> str:
    return _id(f"6{[l[0] for l in LEADS].index(key):02d}")


def plan_id(key: str) -> str:
    return _id(f"5{[p[0] for p in PLANS].index(key):02d}")


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


def _seed_amina_history(cur: psycopg.Cursor) -> None:
    """История Амины Сагындык: занятия, отметки, журнал, отработка, заметки.

    Итог обязан совпасть с прототипом и с этапом 1: остаток 5 из 8, одна
    отработка. Считается он не здесь, а триггером базы по журналу — здесь
    только события, которые реально произошли.
    """
    sid = student_id("sagyndyk")
    sub = subscription_id("sagyndyk")
    teacher = teacher_id("sharapov")

    for suffix, day, start, mark, status in AMINA_PAST:
        lid = _id(suffix)
        cur.execute(
            """INSERT INTO lesson (id, tenant_id, branch_id, teacher_id, room_id,
                                   discipline_id, student_id, kind, starts_at, ends_at, status)
               VALUES (%(id)s, %(t)s, %(b)s, %(tc)s, %(rm)s, %(disc)s, %(st)s, 'regular',
                       %(start)s::timestamp AT TIME ZONE %(tz)s,
                       (%(start)s::timestamp AT TIME ZONE %(tz)s) + make_interval(mins => 55),
                       %(status)s)""",
            {
                "id": lid, "t": TENANT, "b": BRANCH_AF, "tc": teacher,
                "rm": ROOMS["drum_a"], "disc": DISC["drums"], "st": sid,
                "start": f"{day} {start}", "tz": TZ, "status": status,
            },
        )
        if mark is None:
            continue

        cur.execute(
            """INSERT INTO attendance (tenant_id, lesson_id, student_id, mark, marked_by, marked_at)
               VALUES (%s, %s, %s, %s, %s, %s::timestamp AT TIME ZONE %s) RETURNING id""",
            (TENANT, lid, sid, mark, ADMIN_USER, f"{day} 12:00", TZ),
        )
        att_id = cur.fetchone()[0]

        # Отмена заранее даёт отработку, всё остальное списывает занятие —
        # ровно то, что посчитал бы rules.compute_effect на правилах школы.
        if mark == "cancelled_early":
            cur.execute(
                """INSERT INTO subscription_entry
                     (tenant_id, subscription_id, kind, makeups_delta, attendance_id,
                      lesson_id, reason, created_by, created_at)
                   VALUES (%s, %s, 'makeup_grant', 1, %s, %s, %s, %s,
                           %s::timestamp AT TIME ZONE %s)""",
                (TENANT, sub, att_id, lid, "Отмена за 2 дня до занятия", ADMIN_USER,
                 f"{day} 12:00", TZ),
            )
            cur.execute(
                """INSERT INTO makeup_credit
                     (tenant_id, subscription_id, student_id, granted_for, expires_on)
                   VALUES (%s, %s, %s, %s, %s::date + 30)""",
                (TENANT, sub, sid, lid, day),
            )
            continue

        cur.execute(
            """INSERT INTO subscription_entry
                 (tenant_id, subscription_id, kind, lessons_delta, attendance_id, lesson_id,
                  reason, created_by, created_at)
               VALUES (%s, %s, 'charge', -1, %s, %s, %s, %s,
                       %s::timestamp AT TIME ZONE %s)""",
            (TENANT, sub, att_id, lid, f"Отметка «{mark}»", ADMIN_USER, f"{day} 12:00", TZ),
        )
        # Прогул оплачивается преподавателю: pay_teacher_on_no_show = true.
        cur.execute(
            """INSERT INTO payroll_entry
                 (tenant_id, staff_id, lesson_id, attendance_id, kind, amount, calc)
               VALUES (%s, %s, %s, %s, 'lesson', 4500,
                       '{"kind":"fixed","share":1.0,"seeded":true}')""",
            (TENANT, teacher, lid, att_id),
        )

    for suffix, day in AMINA_UPCOMING:
        cur.execute(
            """INSERT INTO lesson (id, tenant_id, branch_id, teacher_id, room_id,
                                   discipline_id, student_id, kind, starts_at, ends_at, status)
               VALUES (%(id)s, %(t)s, %(b)s, %(tc)s, %(rm)s, %(disc)s, %(st)s, 'regular',
                       %(start)s::timestamp AT TIME ZONE %(tz)s,
                       (%(start)s::timestamp AT TIME ZONE %(tz)s) + make_interval(mins => 55),
                       'planned')""",
            {
                "id": _id(suffix), "t": TENANT, "b": BRANCH_AF, "tc": teacher,
                "rm": ROOMS["drum_a"], "disc": DISC["drums"], "st": sid,
                "start": f"{day} 11:00", "tz": TZ,
            },
        )

    for suffix, body, homework, tags in AMINA_NOTES:
        cur.execute(
            """INSERT INTO lesson_note (tenant_id, lesson_id, student_id, author_id,
                                        body, homework, tags)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (TENANT, _id(suffix), sid, teacher, body, homework, tags),
        )


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

    # --- тарифы ------------------------------------------------------------
    for key, name, disc, fmt, minutes, lessons, days, price in PLANS:
        cur.execute(
            """INSERT INTO subscription_plan
                 (id, tenant_id, name, discipline_id, format, duration_min,
                  lessons_count, valid_days, price)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (plan_id(key), TENANT, name, DISC[disc], fmt, minutes, lessons, days, price),
        )

    # --- семья Сагындык ----------------------------------------------------
    # Телефон плательщика взят из прототипа. Диапазон +7701555000X уже занят
    # преподавателями, а person_phone_uniq не пропустил бы дубль.
    cur.execute(
        """INSERT INTO person (id, tenant_id, first_name, last_name, phone, pd_consent_at)
           VALUES (%s, %s, 'Гульнара', 'Сагындык', '+77015552418', now())""",
        (PAYER_SAGYNDYK, TENANT),
    )
    cur.execute(
        """INSERT INTO family (id, tenant_id, name, payer_id, discount_pct)
           VALUES (%s, %s, 'Сагындык', %s, %s)""",
        (FAMILY_SAGYNDYK, TENANT, PAYER_SAGYNDYK, FAMILY_DISCOUNT),
    )
    cur.execute(
        "INSERT INTO family_member (family_id, person_id, relation) VALUES (%s, %s, 'payer')",
        (FAMILY_SAGYNDYK, PAYER_SAGYNDYK),
    )

    # --- ученики -----------------------------------------------------------
    for idx, (key, first, last, disc, bought, used) in enumerate(STUDENTS, start=1):
        pid, sid = person_id(key), student_id(key)
        family = FAMILY_SAGYNDYK if key in FAMILY_MEMBERS else None
        cur.execute(
            """INSERT INTO person (id, tenant_id, first_name, last_name, phone, birth_date, pd_consent_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())""",
            (pid, TENANT, first, last, f"+7702555{idx:04d}", BIRTHDAYS.get(key, BIRTH_DEFAULT)),
        )
        if family is not None:
            cur.execute(
                """INSERT INTO family_member (family_id, person_id, relation)
                   VALUES (%s, %s, 'student')""",
                (family, pid),
            )
        cur.execute(
            """INSERT INTO student (id, tenant_id, person_id, family_id, branch_id,
                                    discipline_id, main_teacher_id, started_on)
               VALUES (%s, %s, %s, %s, %s, %s, %s, '2026-02-04')""",
            (sid, TENANT, pid, family, BRANCH_AF, DISC[disc], teacher_id(MAIN_TEACHER[disc])),
        )
        if bought == 0:
            continue
        plan_key = PLAN_BY_DISCIPLINE[disc]
        price = dict((p[0], p[7]) for p in PLANS)[plan_key]
        sub = subscription_id(key)
        # rules копируются из настроек школы на момент продажи — дальше
        # абонемент живёт по ним, что бы школа ни поменяла у себя (spec §4.2).
        cur.execute(
            """INSERT INTO subscription
                 (id, tenant_id, student_id, family_id, plan_id, lessons_total, price,
                  discount_pct, rules, valid_from, valid_until, sold_by)
               SELECT %s, %s, %s, %s, %s, %s, %s, %s, default_rules,
                      '2026-08-01', '2026-08-31', %s
               FROM tenant WHERE id = %s""",
            (sub, TENANT, sid, family, plan_id(plan_key), bought, price,
             FAMILY_DISCOUNT if family else 0, ADMIN_USER, TENANT),
        )
        # created_at задаётся явно: в журнале это дата операции, и «продажа
        # сегодня» вместо «продажа 1 августа» ломала бы весь экран движений.
        cur.execute(
            """INSERT INTO subscription_entry
                 (tenant_id, subscription_id, kind, lessons_delta, reason, created_by, created_at)
               VALUES (%s, %s, 'purchase', %s, 'Продажа абонемента', %s,
                       '2026-08-01 10:00'::timestamp AT TIME ZONE %s)""",
            (TENANT, sub, bought, ADMIN_USER, TZ),
        )
        # У Амины движения расписаны поштучно настоящими занятиями — её
        # журнал и есть тот экран, ради которого делался этап.
        if used and key != "sagyndyk":
            cur.execute(
                """INSERT INTO subscription_entry
                     (tenant_id, subscription_id, kind, lessons_delta, reason, created_by)
                   VALUES (%s, %s, 'charge', %s, 'Занятия до 12 августа', %s)""",
                (TENANT, sub, -used, ADMIN_USER),
            )

    # --- платежи семьи Сагындык -------------------------------------------
    # 54 000 ₸ со скидкой 10% = 48 600 ₸ на ребёнка, двое детей → 97 200 ₸
    # за август. Ровно эта сумма стоит в карточке прототипа.
    for key in FAMILY_MEMBERS:
        cur.execute(
            """INSERT INTO payment (tenant_id, family_id, subscription_id, amount, method,
                                    accepted_by, paid_at, note)
               VALUES (%s, %s, %s, 48600, 'kaspi', %s,
                       '2026-08-01 10:05'::timestamp AT TIME ZONE %s, 'Оплата абонемента')""",
            (TENANT, FAMILY_SAGYNDYK, subscription_id(key), ADMIN_USER, TZ),
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

    # --- второй администратор ---------------------------------------------
    # Заявки надо на кого-то назначать, а один администратор на всю школу
    # не показывает ни назначения, ни фильтра «мои».
    manager_person = _id("1fb")
    cur.execute(
        """INSERT INTO person (id, tenant_id, first_name, last_name, phone, pd_consent_at)
           VALUES (%s, %s, 'Айгерим', 'Дюсенова', '+77015550101', now())""",
        (manager_person, TENANT),
    )
    cur.execute(
        """INSERT INTO app_user (id, tenant_id, person_id, login, role)
           VALUES (%s, %s, %s, '+77015550101', 'admin')""",
        (MANAGER_USER, TENANT, manager_person),
    )

    # --- ключи внешних источников ------------------------------------------
    # В базу уходит только хеш — как и в бою. Открытый текст здесь известен
    # заранее лишь потому, что это демо: тестам и curl-примерам нужен ключ,
    # который переживёт пересев. Настоящие выпускаются scripts/make_api_key.py.
    for raw, tenant, name, scopes, revoked in DEMO_API_KEYS:
        cur.execute(
            """INSERT INTO api_key (tenant_id, name, key_hash, prefix, scopes, revoked_at)
               VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END)""",
            (TENANT_OTHER if tenant == "other" else TENANT, name,
             api_keys.hash_key(raw), raw[:12], scopes, revoked),
        )

    # --- воронка заявок ----------------------------------------------------
    for key, name, phone, student, age, disc, branch, source, stage, lost, \
            attempts, assigned, hours_ago, next_action_hours in LEADS:
        cur.execute(
            """INSERT INTO lead (id, tenant_id, name, phone, student_name, student_age,
                                 discipline_id, branch_id, stage, lost_reason, source,
                                 assigned_to, contact_attempts, next_action_at,
                                 created_at, updated_at)
               VALUES (%(id)s, %(t)s, %(name)s, %(phone)s, %(student)s, %(age)s,
                       %(disc)s, %(branch)s, %(stage)s, %(lost)s, %(source)s,
                       %(assigned)s, %(attempts)s,
                       CASE WHEN %(next)s::int IS NULL THEN NULL
                            ELSE now() + make_interval(hours => %(next)s::int) END,
                       now() - make_interval(hours => %(ago)s),
                       now() - make_interval(hours => %(ago)s))""",
            {
                "id": lead_id(key), "t": TENANT, "name": name, "phone": phone,
                "student": student, "age": age,
                "disc": DISC[disc] if disc else None,
                "branch": {"AF": BRANCH_AF, "AB": BRANCH_AB}[branch], "stage": stage, "lost": lost, "source": source,
                "assigned": {"admin": ADMIN_USER, "manager": MANAGER_USER}.get(assigned),
                "attempts": attempts, "ago": hours_ago, "next": next_action_hours,
            },
        )

        # История стадий. Без неё отчёт по воронке считать не из чего:
        # заявка, дошедшая до покупки, в колонке «пробный проведён» уже
        # не лежит, и конверсия по текущим стадиям вышла бы заниженной.
        path = LEAD_PATHS[stage]
        # Купившие проходят путь ровно за WON_PATH_HOURS — это и есть «средний
        # путь от заявки до оплаты» на доске. Остальные растягивают свой путь
        # по прожитому времени, и последний шаг остаётся свежим: иначе каждая
        # открытая заявка выглядела бы застрявшей с самого посева.
        span = WON_PATH_HOURS if stage == "won" else hours_ago * 0.7
        previous = None
        for index, to_stage in enumerate(path):
            at = hours_ago - (span * index / (len(path) - 1) if len(path) > 1 else 0)
            cur.execute(
                """INSERT INTO lead_stage_history
                     (tenant_id, lead_id, from_stage, to_stage, changed_by, changed_at)
                   VALUES (%s, %s, %s, %s, %s, now() - make_interval(secs => %s))""",
                (TENANT, lead_id(key), previous, to_stage, ADMIN_USER, int(at * 3600)),
            )
            previous = to_stage

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

    _seed_amina_history(cur)

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
    print(f"  student_id:  {student_id('sagyndyk')}  (Амина Сагындык)")
    print(f"  subscription:{subscription_id('sagyndyk')}  (её августовский абонемент)")
    print(f"  plan_id:     {plan_id('drums8')}  (Барабаны, 2 раза в неделю, 55 мин)")


if __name__ == "__main__":
    main()
