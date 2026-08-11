"""Настройки бэкенда. Всё берётся из окружения — секретов в репозитории нет."""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
SQL_DIR = REPO_DIR / "db"

# .env лежит рядом с кодом бэкенда и в git не попадает. Значения из настоящего
# окружения приоритетнее файла: в контейнере .env не будет вовсе.
load_dotenv(BACKEND_DIR / ".env", override=False)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


# Приложение ходит под ролью rockcrm_app: она не владелец таблиц и не суперюзер,
# поэтому политики изоляции на неё действуют. Под postgres RLS молча
# отключается, и вся защита превращается в декорацию — см. db/README.md.
DATABASE_URL = _env(
    "DATABASE_URL",
    "postgresql://rockcrm_app:app@localhost:55432/rockcrm_backend",
)

# Отдельная строка с правами администратора нужна только скриптам:
# создать базу, применить миграции, выдать роли пароль.
ADMIN_DATABASE_URL = _env(
    "ADMIN_DATABASE_URL",
    "postgresql://postgres:dev@localhost:55432/postgres",
)

APP_DB_NAME = _env("APP_DB_NAME", "rockcrm_backend")
APP_DB_ROLE = _env("APP_DB_ROLE", "rockcrm_app")
APP_DB_PASSWORD = _env("APP_DB_PASSWORD", "app")

POOL_MIN_SIZE = int(_env("POOL_MIN_SIZE", "1"))
POOL_MAX_SIZE = int(_env("POOL_MAX_SIZE", "10"))

# Фронтенд на Vite поднимается на 5173; в проде список сузится до домена школы.
CORS_ORIGINS = [o.strip() for o in _env("CORS_ORIGINS", "*").split(",") if o.strip()]

# Кука с сессией уезжает на другой порт, а значит запрос кросс-оригинный,
# и без allow_credentials браузер куку не пошлёт и не примет. Но «*» вместе
# с учётными данными запрещён самим стандартом CORS, и включить их при звёздочке
# значит получить молчаливо неработающий вход. Поэтому список источников
# и есть переключатель: точные адреса — кука работает, «*» — остаётся только
# заголовок Authorization (curl, мобильный клиент, тесты).
CORS_ALLOW_CREDENTIALS = CORS_ORIGINS != ["*"]

# Secure-кука не ставится по http, поэтому на localhost флаг выключен.
# В бою обязателен: без него сессию видно любому, кто слушает Wi-Fi школы.
AUTH_COOKIE_SECURE = _env("AUTH_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

# SameSite=Lax — это и есть защита от CSRF: браузер не приложит куку
# к межсайтовому POST, а все операции, что-то меняющие, у нас POST, PATCH
# и DELETE. Работает, пока фронтенд и API живут на одном сайте (домен
# и его поддомены; порт значения не имеет, поэтому 5173 и 8000 на localhost
# считаются одним сайтом). Если однажды они разъедутся по разным доменам,
# сюда встанет `none`, а вместе с ним понадобится отдельный CSRF-токен.
AUTH_COOKIE_SAMESITE = _env("AUTH_COOKIE_SAMESITE", "lax")
