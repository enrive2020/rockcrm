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
OTHER_LESSON = seed_demo.OTHER_LESSON
DAY = seed_demo.DAY

HEADERS = {"X-Tenant-Id": TENANT, "X-User-Id": USER}
HEADERS_OTHER = {"X-Tenant-Id": TENANT_OTHER, "X-User-Id": seed_demo.OTHER_USER}

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


def participant(card: dict[str, Any], student_id: str) -> dict[str, Any]:
    for p in card["participants"]:
        if p["student_id"] == student_id:
            return p
    raise AssertionError(f"ученик {student_id} не найден в занятии {card['id']}")
