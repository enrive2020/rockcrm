"""Ночная сверка статусов абонементов с календарём.

    python -m scripts.sync_statuses                 # все школы
    python -m scripts.sync_statuses <tenant_id>     # одна школа
    python -m scripts.sync_statuses --dry-run       # только показать

Зачем это отдельное задание, а не часть API. Заморозка — единственное
состояние абонемента, которое включается и выключается **датой**, а не
действием человека. `create_hold` переводит абонемент в `frozen` в день
начала каникул, но день окончания не наступает ни в одном HTTP-запросе:
если ничего не запускать, карточка будет показывать каникулы, кончившиеся
две недели назад. Триггер пересчёта остатка при этом бережёт `frozen`
(db/003_billing.sql:143), поэтому застрявший статус не даст абонементу
стать ни `exhausted`, ни `expired`.

Тем же проходом включается заморозка, назначенная на будущее: в день своего
начала она становится действующей.

Задание идемпотентно — оно приводит статус к тому, что следует из фактов,
а не переключает его. Второй прогон подряд не меняет ничего, поэтому
повторный запуск после сбоя безопасен.

Ходит под ролью приложения через db.tenant_tx(): статус абонемента —
обычные данные школы, и обходить RLS ради него незачем.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import billing, config, db  # noqa: E402


def _admin_dsn() -> str:
    return config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME


def _tenants(tenant_id: str | None) -> list[tuple[str, str, str]]:
    """Школы вместе с их поясом: «сегодня» у школы в Алматы своё."""
    with psycopg.connect(_admin_dsn()) as conn:
        rows = conn.execute(
            "SELECT id, slug, timezone FROM tenant"
            + (" WHERE id = %s" if tenant_id else "")
            + " ORDER BY slug",
            (tenant_id,) if tenant_id else (),
        ).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def run(tenant_id: str | None = None, dry_run: bool = False) -> int:
    changed_total = 0
    for tid, slug, tz_name in _tenants(tenant_id):
        today = dt.datetime.now(ZoneInfo(tz_name or "Asia/Almaty")).date()
        changed: list = []
        with db.tenant_tx(tid) as cur:
            changed = billing.sync_statuses(cur, today)
            if dry_run:
                # psycopg.Rollback откатывает транзакцию и гасится самим
                # блоком: посмотреть, что задание собирается сделать, надо
                # уметь до того, как оно это сделает.
                raise psycopg.Rollback
        for row in changed:
            print(f"  {row['id']} -> {row['status']}")
        print(f"{slug}: {len(changed)} абонементов" + (" (dry-run)" if dry_run else ""))
        changed_total += len(changed)
    return changed_total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id", nargs="?", help="UUID школы; по умолчанию все")
    parser.add_argument("--dry-run", action="store_true", help="показать и откатить")
    args = parser.parse_args()

    try:
        total = run(args.tenant_id, args.dry_run)
    finally:
        db.close_pool()
    print(f"итого изменено: {total}")


if __name__ == "__main__":
    main()
