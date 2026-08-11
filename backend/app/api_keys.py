"""Аутентификация внешних источников заявок по ключу.

Заголовки `X-Tenant-Id` и `X-User-Id` для вебхука не годятся: это заглушка
внутренней авторизации, её значение знает любой, кто открыл фронтенд,
и подставить чужую школу было бы тривиально. Telegram-бот и LeadHub —
это системы, а не люди, и приходят они со своим ключом.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

import psycopg

from .errors import ApiError

# Префикс отличает ключ RockCRM от всего остального в чужих конфигах
# и позволяет опознать утечку поиском по репозиториям.
KEY_PREFIX = "rck"
# 32 байта энтропии: подобрать такой ключ перебором невозможно, поэтому
# и медленный хеш ему не нужен (см. hash_key ниже).
_SECRET_BYTES = 32
# Сколько символов показываем человеку в списке ключей, чтобы он понял,
# какой именно отзывает. Секрета это не раскрывает.
_VISIBLE = 12

WRITE_LEADS = "leads:write"


def generate() -> tuple[str, str, str]:
    """Новый ключ: (открытый текст, хеш, видимый префикс).

    Открытый текст возвращается один раз и нигде не сохраняется — восстановить
    его потом невозможно, ровно как пароль.
    """
    raw = f"{KEY_PREFIX}_{secrets.token_urlsafe(_SECRET_BYTES)}"
    return raw, hash_key(raw), raw[:_VISIBLE]


def hash_key(raw: str) -> str:
    """SHA-256, а не bcrypt — и это осознанно.

    Медленный хеш защищает пароли, потому что человек выбирает слабый пароль
    и его можно перебрать по словарю. Здесь секрет — 32 случайных байта:
    перебирать нечего. Зато ключ проверяется на каждом запросе вебхука
    и ищется по индексу, а bcrypt не позволил бы ни того, ни другого —
    пришлось бы перебирать все ключи школы, которая ещё не известна.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def authenticate(cur: psycopg.Cursor, raw: str | None) -> dict[str, Any]:
    """Находит ключ по хешу и возвращает строку api_key.

    Вызывается ДО того, как известен тенант: тенант и есть то, что ключ
    сообщает. Политик уровня строк на api_key поэтому нет — см. комментарий
    в db/006_api_keys.sql.
    """
    if not raw:
        raise ApiError(401, "no_api_key", "Заголовок X-Api-Key обязателен.")

    cur.execute(
        """
        SELECT id, tenant_id, name, scopes, revoked_at
        FROM api_key
        WHERE key_hash = %s
        """,
        (hash_key(raw),),
    )
    key = cur.fetchone()
    if key is None:
        # Неизвестный и отозванный ключ отвечают одинаково по сути, но
        # отозванный получает свой текст: интеграцию чинит человек, и ему
        # важно знать, что ключ был, а не что он опечатался.
        raise ApiError(401, "bad_api_key", "Ключ не найден. Проверьте X-Api-Key.")
    if key["revoked_at"] is not None:
        raise ApiError(
            401,
            "revoked_api_key",
            f"Ключ «{key['name']}» отозван. Выпустите новый и обновите интеграцию.",
        )
    return key


def require_scope(key: dict[str, Any], scope: str) -> None:
    """Область доступа ключа. Ключ бота не должен уметь ничего, кроме заявок."""
    scopes = list(key.get("scopes") or [])
    if not any(hmac.compare_digest(s, scope) for s in scopes):
        raise ApiError(
            403,
            "scope_required",
            f"Ключу «{key['name']}» не разрешена область «{scope}».",
            {"scopes": scopes, "required": scope},
        )


def mark_used(cur: psycopg.Cursor, key_id: str) -> None:
    """Отметка использования. Единственное, что приложению позволено менять
    в api_key: выпуск и отзыв идут административным каналом (db/006)."""
    cur.execute("UPDATE api_key SET last_used_at = now() WHERE id = %s", (key_id,))
