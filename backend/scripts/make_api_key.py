"""Выпуск ключа для внешнего источника заявок.

    python -m scripts.make_api_key <tenant_id> <name>
    python -m scripts.make_api_key <tenant_id> "LeadHub" --scopes leads:write
    python -m scripts.make_api_key --list <tenant_id>
    python -m scripts.make_api_key --revoke <key_id>

Открытый ключ печатается ОДИН РАЗ и нигде не сохраняется — в базу уходит
только хеш. Потерянный ключ не восстанавливается, а выпускается заново:
это то же свойство, что у пароля, и ровно оно делает утечку базы
бесполезной для отправки заявок от имени школы.

Скрипт ходит под административной ролью намеренно: роли приложения
INSERT и DELETE на api_key не выданы (db/006_api_keys.sql), выпуск и отзыв
ключей — административный канал, а не функция API.
"""
from __future__ import annotations

import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import api_keys, config  # noqa: E402


def _connect() -> psycopg.Connection:
    target = config.ADMIN_DATABASE_URL.rsplit("/", 1)[0] + "/" + config.APP_DB_NAME
    return psycopg.connect(target, autocommit=True)


def issue(tenant_id: str, name: str, scopes: list[str]) -> None:
    raw, key_hash, prefix = api_keys.generate()
    with _connect() as conn:
        exists = conn.execute("SELECT slug FROM tenant WHERE id = %s", (tenant_id,)).fetchone()
        if exists is None:
            raise SystemExit(f"школа {tenant_id} не найдена")
        row = conn.execute(
            """
            INSERT INTO api_key (tenant_id, name, key_hash, prefix, scopes)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, name, key_hash, prefix, scopes),
        ).fetchone()

    print(f"ключ «{name}» для школы {exists[0]} выпущен")
    print(f"  id:     {row[0]}")
    print(f"  scopes: {', '.join(scopes)}")
    print()
    print("  X-Api-Key: " + raw)
    print()
    print("Сохраните его сейчас: в базе лежит только хеш, показать повторно нечем.")


def show(tenant_id: str) -> None:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, name, prefix, scopes, last_used_at, revoked_at
            FROM api_key WHERE tenant_id = %s ORDER BY created_at
            """,
            (tenant_id,),
        ).fetchall()
    if not rows:
        print("ключей нет")
        return
    for key_id, name, prefix, scopes, used, revoked in rows:
        state = "отозван" if revoked else "активен"
        print(f"{key_id}  {prefix}…  {name:<20} [{', '.join(scopes)}]  {state}"
              f"  последний вызов: {used or '—'}")


def revoke(key_id: str) -> None:
    with _connect() as conn:
        row = conn.execute(
            "UPDATE api_key SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL "
            "RETURNING name",
            (key_id,),
        ).fetchone()
    print(f"ключ «{row[0]}» отозван" if row else "ключ не найден или уже отозван")


def main(argv: list[str]) -> None:
    if "--list" in argv:
        show(argv[argv.index("--list") + 1])
        return
    if "--revoke" in argv:
        revoke(argv[argv.index("--revoke") + 1])
        return

    scopes = [api_keys.WRITE_LEADS]
    if "--scopes" in argv:
        at = argv.index("--scopes")
        scopes = [s.strip() for s in argv[at + 1].split(",") if s.strip()]
        argv = argv[:at] + argv[at + 2:]

    positional = [a for a in argv if not a.startswith("--")]
    if len(positional) < 2:
        raise SystemExit(__doc__)
    issue(positional[0], positional[1], scopes)


if __name__ == "__main__":
    main(sys.argv[1:])
