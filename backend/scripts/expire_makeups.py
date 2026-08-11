"""Ночное сгорание отработок по сроку.

    python -m scripts.expire_makeups                 # все школы
    python -m scripts.expire_makeups <tenant_id>     # одна школа
    python -m scripts.expire_makeups --dry-run       # только показать

Зачем это отдельное задание, а не часть API. У каждой отработки есть
`expires_on`, но ни один HTTP-запрос до неё не доходит: срок наступает
сам по себе, как и конец заморозки. Без задания просроченные отработки
висят в балансе вечно, и школа продолжает быть должна занятия, право
на которые истекло полгода назад.

Тем же проходом уходит предупреждение родителю за пять дней до сгорания
(spec.md §8). Оно ставится в очередь `notification`, а не отправляется:
относит сообщения отдельный воркер, и его отсутствие не должно мешать
отработкам сгорать.

Задание идемпотентно, и это его главное свойство: ключ — сама отработка,
у неё либо проставлен `expired_at`, либо нет. Два прогона подряд не спишут
её дважды, потому что второй её просто не найдёт. У предупреждения тот же
принцип, только ключ снаружи — `dedup_key` в очереди уведомлений.

Ходит под ролью приложения через db.tenant_tx(): отработки — обычные данные
школы, и обходить RLS ради них незачем.
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
    """Школы вместе с их поясом: «сегодня» у школы в Алматы своё.

    Дата здесь решает, сгорела отработка или ещё нет, поэтому брать её
    в поясе сервера нельзя: у школы в другом городе задание, запущенное
    в полночь, срезало бы отработкам последний день.
    """
    with psycopg.connect(_admin_dsn()) as conn:
        rows = conn.execute(
            "SELECT id, slug, timezone FROM tenant"
            + (" WHERE id = %s" if tenant_id else "")
            + " ORDER BY slug",
            (tenant_id,) if tenant_id else (),
        ).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


def run(tenant_id: str | None = None, dry_run: bool = False) -> dict[str, int]:
    """Возвращает {'expired': сгорело, 'warned': поставлено предупреждений}."""
    totals = {"expired": 0, "warned": 0}
    for tid, slug, tz_name in _tenants(tenant_id):
        today = dt.datetime.now(ZoneInfo(tz_name or "Asia/Almaty")).date()
        burned: list = []
        warned: list = []
        with db.tenant_tx(tid) as cur:
            burned = billing.expire_makeups(cur, tid, today)
            warned = billing.warn_expiring_makeups(cur, tid, today)
            if dry_run:
                # psycopg.Rollback откатывает транзакцию и гасится самим
                # блоком: посмотреть, что задание собирается списать, надо
                # уметь до того, как оно это спишет.
                raise psycopg.Rollback
        for row in burned:
            print(f"  {row['id']} · срок истёк {row['expires_on']:%d.%m.%Y}")
        print(
            f"{slug}: сгорело {len(burned)}, предупреждений {len(warned)}"
            + (" (dry-run)" if dry_run else "")
        )
        totals["expired"] += len(burned)
        totals["warned"] += len(warned)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id", nargs="?", help="UUID школы; по умолчанию все")
    parser.add_argument("--dry-run", action="store_true", help="показать и откатить")
    args = parser.parse_args()

    try:
        totals = run(args.tenant_id, args.dry_run)
    finally:
        db.close_pool()
    print(f"итого сгорело: {totals['expired']}, предупреждений: {totals['warned']}")


if __name__ == "__main__":
    main()
