"""Создаёт базу бэкенда и накатывает схему из db/.

Отдельная база (по умолчанию rockcrm_backend) нужна потому, что tests/test_schema.py
пересоздаёт схему public в базе rockcrm целиком. Работать в одной базе с ним —
терять данные ровно тогда, когда кто-то прогонит тесты схемы.

    python -m scripts.init_db                            # создать, если нет
    python -m scripts.init_db --recreate                 # снести и создать заново
    python -m scripts.init_db --apply 006_api_keys.sql   # накатить одну миграцию
"""
from __future__ import annotations

import sys

import psycopg
from psycopg import sql

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402


def _target_url() -> str:
    return config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME


def apply_one(filename: str) -> None:
    """Накатывает одну миграцию на уже существующую базу.

    Полноценного механизма версий у нас нет, а --recreate сносит данные вместе
    со схемой и выгоняет всех, кто в этот момент работает. Когда в db/ приезжает
    новый файл, а база уже поднята, нужен способ применить только его —
    иначе единственный вариант «поднять заново» стоит чужого рабочего сеанса.
    """
    path = config.SQL_DIR / filename
    if not path.exists():
        raise SystemExit(f"нет такого файла: {path}")
    with psycopg.connect(_target_url(), autocommit=True) as conn:
        conn.execute(path.read_text(encoding="utf-8"))
    print(f"применён {path.name}")


def main(recreate: bool = False) -> None:
    admin = psycopg.connect(config.ADMIN_DATABASE_URL, autocommit=True)
    with admin:
        if recreate:
            # Активные соединения не дают удалить базу — сначала выгоняем их,
            # иначе скрипт падает после первого же запущенного сервера.
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (config.APP_DB_NAME,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(config.APP_DB_NAME)))
            print(f"база {config.APP_DB_NAME} удалена")

        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (config.APP_DB_NAME,)
        ).fetchone()
        if exists:
            print(f"база {config.APP_DB_NAME} уже есть — схему не трогаю")
            _ensure_role(admin)
            return

        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.APP_DB_NAME)))
        print(f"база {config.APP_DB_NAME} создана")

    with psycopg.connect(_target_url(), autocommit=True) as conn:
        for path in sorted(config.SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            print(f"  применён {path.name}")

    with psycopg.connect(config.ADMIN_DATABASE_URL, autocommit=True) as conn:
        _ensure_role(conn)


def _ensure_role(conn: psycopg.Connection) -> None:
    """Роль приложения создаётся миграцией без пароля.

    По TCP это означает «войти невозможно»: образ postgres требует
    scram-sha-256. Пароль — вопрос развёртывания, а не схемы, поэтому он
    выдаётся здесь, а не правкой db/005_rls.sql.
    """
    conn.execute(
        sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(config.APP_DB_ROLE), sql.Literal(config.APP_DB_PASSWORD)
        )
    )
    conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(config.APP_DB_NAME), sql.Identifier(config.APP_DB_ROLE)
        )
    )
    print(f"роль {config.APP_DB_ROLE} готова")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_one(sys.argv[sys.argv.index("--apply") + 1])
    else:
        main(recreate="--recreate" in sys.argv)
