"""Пул соединений и контур изоляции тенантов.

Главное здесь — одно правило: любой запрос к базе идёт внутри транзакции,
в которой первым делом выставлен app.tenant_id. Забыть его нельзя, потому что
единственный способ получить курсор — пройти через tenant_tx().
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.DATABASE_URL,
            min_size=config.POOL_MIN_SIZE,
            max_size=config.POOL_MAX_SIZE,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
            # Соединение, простоявшее в пуле, могло быть убито сервером.
            # Дешевле проверить его, чем отдать запросу мёртвое.
            check=ConnectionPool.check_connection,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tenant_tx(tenant_id: str) -> Iterator[psycopg.Cursor[dict[str, Any]]]:
    """Транзакция с выставленным тенантом.

    set_config, а не SET LOCAL: SET не принимает параметры драйвера, а склейка
    строкой в самом чувствительном месте системы — прямая дорога к инъекции,
    подменяющей тенанта. Третий аргумент true = значение живёт до конца
    транзакции и не утечёт в следующий запрос, которому достанется то же
    соединение из пула.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                yield cur
