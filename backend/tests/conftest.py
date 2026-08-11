"""Общая обвязка тестов.

Тесты идут против настоящего PostgreSQL, а не против моков базы. Половина
гарантий этой системы живёт в самой схеме — ограничения исключения, триггер
пересчёта остатка, политики изоляции. Заменить базу заглушкой значит выкинуть
из-под теста именно то, что он должен проверять.
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from scripts import seed_demo  # noqa: E402

TENANT = seed_demo.TENANT
TENANT_OTHER = seed_demo.TENANT_OTHER
USER = seed_demo.ADMIN_USER
BRANCH_AF = seed_demo.BRANCH_AF
BRANCH_AB = seed_demo.BRANCH_AB
OTHER_LESSON = seed_demo.OTHER_LESSON
DAY = seed_demo.DAY


def bearer(token: str) -> dict[str, str]:
    """Заголовок сессии для тестового клиента.

    Прежние `X-Tenant-Id` и `X-User-Id` не существуют: тенант приезжает
    из сессии. Сами сессии кладёт в базу `seed_demo` — тем же приёмом, что
    и демо-ключи внешних источников: в базе хеш, открытый токен известен
    заранее только потому, что это демо.

    Служебного пути «войти без проверки» в приложении не появилось: эти строки
    ничем не отличаются от выданных настоящим входом, и живут они только
    на базе, которую посеял seed_demo (а он начинает с удаления обоих
    демо-тенантов). Вход по коду и по паролю проверяется отдельно
    в tests/test_auth.py — через настоящий HTTP.
    """
    return {"Authorization": f"Bearer {token}"}


# По умолчанию тесты ходят владельцем: он видит всё, поэтому проверки этапов
# 1–4 остаются проверками своей предметной области, а не прав доступа.
# Разграничение прав проверяется там, где оно и есть смысл теста —
# в test_authz.py, каждой ролью отдельно.
HEADERS = bearer("rck_sess_demo_owner")
HEADERS_OTHER = bearer("rck_sess_demo_other_owner")

HEADERS_ADMIN = bearer("rck_sess_demo_admin")                 # Асель, без филиалов
HEADERS_BRANCH_ADMIN = bearer("rck_sess_demo_branch_admin")   # Дана, только Абая
HEADERS_TEACHER = bearer("rck_sess_demo_teacher")             # Шарапов, барабаны
HEADERS_TEACHER2 = bearer("rck_sess_demo_teacher2")           # Федько, гитара
HEADERS_GUARDIAN = bearer("rck_sess_demo_guardian")           # Гульнара Сагындык
HEADERS_STUDENT = bearer("rck_sess_demo_student")             # Дмитрий Со, 17 лет

ADMIN_URL = config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME


@pytest.fixture(autouse=True)
def fresh_data() -> None:
    """Перед каждым тестом база возвращается к демо-дню.

    Тесты идут через настоящее API и оставляют за собой отметки, записи
    журнала, платежи и заявки. Журналы неизменяемы по устройству схемы,
    так что «откатить» тест правкой данных нельзя — только пересеять.
    """
    with psycopg.connect(ADMIN_URL) as conn:
        seed_demo.seed(conn)


def has_partial_attendance_index() -> bool:
    """Накатана ли миграция 008 — частичный индекс attendance_active_uniq.

    Переотметка занятия после отмены держится не на коде, а на схеме,
    и проверять её на базе со сплошным UNIQUE (lesson_id, student_id)
    бессмысленно: тест упал бы на отсутствующей миграции, а не на ошибке.
    Проверка спрашивает базу, а не файлы в db/: значение имеет то, что
    накатано, а не то, что лежит в репозитории.
    """
    with psycopg.connect(ADMIN_URL) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'attendance' AND indexname = %s",
            ("attendance_active_uniq",),
        ).fetchone()
    return found is not None


def has_backdating_columns() -> bool:
    """Накатана ли миграция 009 — колонки `backdated` (ADR-001, пункт 6).

    Та же логика, что и у проверки выше: спрашиваем базу, а не файлы в db/.
    Приложение пишет пометку только при наличии колонки, поэтому без миграции
    проверять нечего — и упасть на этом тест не должен: он проверял бы
    отсутствующую схему, а не ошибку в коде.
    """
    with psycopg.connect(ADMIN_URL) as conn:
        found = conn.execute(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_name IN ('attendance', 'subscription_entry')
              AND column_name = 'backdated'
            """
        ).fetchone()
    return int(found[0]) == 2


def recalc_trigger_ignores_the_clock() -> bool:
    """Перестал ли триггер `subscription_recalc` ставить `expired` (миграция 009).

    Смотрим на определение самой функции: `current_date` в нём означает, что
    статус зависит от системных часов, и вставка строки в журнал объявляет
    соседний абонемент истёкшим (ADR-001, пункт 5).
    """
    with psycopg.connect(ADMIN_URL) as conn:
        source = conn.execute(
            "SELECT pg_get_functiondef('subscription_recalc()'::regprocedure)"
        ).fetchone()[0]
    return "current_date" not in source


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sql():
    """Прямой доступ к базе под администратором — для проверки журналов.

    Читаем в обход API намеренно: тест обязан видеть, что реально легло
    в subscription_entry и payroll_entry, а не пересказ этого от самого API.
    """
    with psycopg.connect(ADMIN_URL, row_factory=dict_row) as conn:
        yield conn


def lesson(key: str) -> str:
    return seed_demo.lesson_id(key)


def student(key: str) -> str:
    return seed_demo.student_id(key)


def subscription(key: str) -> str:
    return seed_demo.subscription_id(key)


def lead(key: str) -> str:
    return seed_demo.lead_id(key)


def get_card(client: TestClient, lesson_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(f"/api/v1/lessons/{lesson_id}", headers=headers or HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def sent_code(conn, to_address: str) -> str | None:
    """Код из очереди уведомлений — оттуда же, откуда его возьмёт человек.

    Тест читает не «секретную дырку для тестов», а тот самый исходящий ящик,
    в который приложение кладёт SMS: воркера, который относит их оператору,
    в системе пока нет (см. README). Хеш в `auth_code` при этом остаётся
    хешем — восстановить код из него по-прежнему нечем.
    """
    row = conn.execute(
        """
        SELECT payload FROM notification
        WHERE template = 'auth_code' AND to_address = %s
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (to_address,),
    ).fetchone()
    return None if row is None else row["payload"]["code"]


def participant(card: dict[str, Any], student_id: str) -> dict[str, Any]:
    for p in card["participants"]:
        if p["student_id"] == student_id:
            return p
    raise AssertionError(f"ученик {student_id} не найден в занятии {card['id']}")
