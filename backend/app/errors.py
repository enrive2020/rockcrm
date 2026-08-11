"""Ошибки API и перевод ограничений базы в осмысленные ответы.

База — последняя линия обороны: двойную бронь кабинета и повторное списание
она ловит сама, в том числе в гонке двух администраторов. Но наружу такая
ошибка обязана выйти как 409 с человеческим текстом, а не как 500 «Internal
Server Error»: администратор на ресепшене должен понять, что делать.
"""
from __future__ import annotations

from typing import Any

import psycopg


class ApiError(Exception):
    """Ошибка, которую можно показать пользователю."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def not_found(what: str = "Объект не найден") -> ApiError:
    """Чужой тенант получает ровно тот же ответ, что и несуществующий объект.

    Разница между «нет такого» и «есть, но не ваше» — уже утечка: по ней видно,
    что объект существует. Аргумент — целая фраза, а не подлежащее: «занятие
    не найдено» и «филиал не найден» согласуются по-разному.
    """
    return ApiError(404, "not_found", f"{what}. Проверьте идентификатор и школу.")


# Имя ограничения базы -> как это выглядит для пользователя.
# Ключи берутся из psycopg.Error.diag.constraint_name, а не из текста ошибки:
# текст зависит от локали сервера и однажды поменяется.
_CONSTRAINT_ERRORS: dict[str, tuple[int, str, str]] = {
    "lesson_room_no_overlap": (
        409,
        "room_busy",
        "Кабинет занят в это время. Выберите другой кабинет или подтвердите овербукинг.",
    ),
    "lesson_teacher_no_overlap": (
        409,
        "teacher_busy",
        "У преподавателя в это время уже есть занятие.",
    ),
    "attendance_lesson_id_student_id_key": (
        409,
        "already_marked",
        "Ученик уже отмечен на этом занятии. Сначала отмените прежнюю отметку.",
    ),
    "subscription_entry_once": (
        409,
        "already_charged",
        "По этой отметке уже есть движение по абонементу. Повторное списание невозможно.",
    ),
    "payroll_entry_once": (
        409,
        "already_paid",
        "По этой отметке уже начислена зарплата.",
    ),
    "subscription_hold_subscription_id_period_excl": (
        409,
        "hold_overlap",
        "Заморозки одного абонемента не могут перекрываться.",
    ),
}

# Коды SQLSTATE, у которых нет имени ограничения, но смысл известен.
_SQLSTATE_ERRORS: dict[str, tuple[int, str, str]] = {
    "2F004": (  # restrict_violation — журналы только на добавление
        409,
        "journal_immutable",
        "Записи журнала не правятся и не удаляются. Ошибку гасят компенсирующей записью.",
    ),
    "23505": (409, "conflict", "Такая запись уже существует."),
    "23514": (400, "check_violation", "Данные не проходят проверку базы."),
    "23503": (400, "fk_violation", "Ссылка указывает на несуществующий объект."),
}


def translate_db_error(exc: psycopg.Error) -> ApiError:
    """Превращает ошибку PostgreSQL в ApiError.

    Если ограничение неизвестно — отдаём 500, но с кодом ограничения в details:
    это единственный способ потом починить перевод, не разгребая логи вслепую.
    """
    constraint = exc.diag.constraint_name
    sqlstate = exc.sqlstate or ""

    if constraint and constraint in _CONSTRAINT_ERRORS:
        status, code, message = _CONSTRAINT_ERRORS[constraint]
        return ApiError(status, code, message, {"constraint": constraint})

    if sqlstate in _SQLSTATE_ERRORS:
        status, code, message = _SQLSTATE_ERRORS[sqlstate]
        return ApiError(status, code, message, {"constraint": constraint, "sqlstate": sqlstate})

    return ApiError(
        500,
        "db_error",
        "Операция отклонена базой данных. Сообщите администратору код ограничения.",
        {"constraint": constraint, "sqlstate": sqlstate},
    )
