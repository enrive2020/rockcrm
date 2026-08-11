"""Кто это и что ему можно.

Спецификация §2 описывает права одной таблицей, но в коде они разбиты на два
разных вопроса, и путать их дорого:

* **можно ли вызывать эту операцию вообще** — «ведомость видит только
  владелец», «родитель не продаёт абонементы». Это проверка роли, и она
  отвечает 403;
* **какие строки видно внутри операции** — «преподаватель видит своих
  учеников», «родитель — своих детей». Это фильтр выборки, и он отвечает 404
  на чужой объект, а не 403: разница между «нет такого» и «есть, но не ваше»
  сама по себе утечка (см. errors.not_found).

Изоляция тенантов здесь ни при чём и остаётся там, где была — в политиках
уровня строк. Этот модуль делит доступ ВНУТРИ одной школы.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from . import repository as repo
from .errors import ApiError, not_found

OWNER = "owner"
ADMIN = "admin"
TEACHER = "teacher"
GUARDIAN = "guardian"
STUDENT = "student"

STAFF_ROLES = frozenset({OWNER, ADMIN, TEACHER})
# Роль сотрудника -> вид записи в staff. Совпадение имён не случайно, но
# отображение выписано явно: у guardian и student записи в staff нет вовсе,
# и «взять kind = role» молча завело бы поиск не туда.
_STAFF_KIND = {OWNER: "owner", ADMIN: "admin", TEACHER: "teacher"}


@dataclass(frozen=True)
class Actor:
    """Тот, кто делает запрос. Собирается из сессии, а не из заголовков.

    `tenant_id` и `user_id` названы так же, как поля прежней заглушки `Caller`:
    ниже по коду ими пользуются два десятка мест, и переименование ради
    переименования дало бы диff, в котором не видно сути изменения.
    """

    tenant_id: str
    user_id: str
    session_id: str
    role: str
    person_id: str
    name: str
    # Запись сотрудника. У родителя и ученика её нет — и это не «ошибка данных»,
    # а нормальное состояние: в школе работают не все, у кого есть вход.
    staff_id: str | None
    # Филиалы из staff_branch. ПУСТОЕ множество означает «без ограничения»,
    # а не «ни одного»: школа из одного филиала не заполняет staff_branch
    # вовсе, и трактовать пустоту как запрет значило бы выключить вход
    # администраторам у всех, кто эту связь не завёл.
    branch_ids: frozenset[str]

    @property
    def is_owner(self) -> bool:
        return self.role == OWNER

    @property
    def is_teacher(self) -> bool:
        return self.role == TEACHER

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_family(self) -> bool:
        """Родитель или взрослый ученик — тот, кто смотрит на себя, а не на школу."""
        return self.role in (GUARDIAN, STUDENT)


def forbidden(message: str, code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)


def load_actor(cur: psycopg.Cursor, user_id: str, session_id: str) -> Actor:
    """Собирает Actor по учётной записи. Вызывается внутри транзакции с тенантом."""
    row = repo.actor_row(cur, user_id)
    if row is None or not row["is_active"]:
        # Учётную запись могли выключить, пока сессия жила: сессия при этом
        # формально цела, но входить по ней больше нельзя.
        raise ApiError(401, "user_disabled", "Учётная запись отключена. Войдите заново.")

    staff_id = row["staff_id"]
    branch_ids = (
        frozenset(repo.staff_branch_ids(cur, str(staff_id))) if staff_id else frozenset()
    )
    return Actor(
        tenant_id=str(row["tenant_id"]),
        user_id=str(row["id"]),
        session_id=session_id,
        role=row["role"],
        person_id=str(row["person_id"]),
        name=row["name"],
        staff_id=str(staff_id) if staff_id else None,
        branch_ids=branch_ids,
    )


# ---------------------------------------------------------------------------
# Проверки роли: можно ли вызывать операцию
# ---------------------------------------------------------------------------


def require_staff(actor: Actor) -> None:
    """Экраны школы: расписание, справочники, воронка.

    Родитель сюда не ходит не потому, что ему нельзя знать расписание своего
    ребёнка, а потому, что это другой экран: `GET /schedule` отдаёт день
    филиала целиком, со всеми чужими детьми в дорожках. Кабинет родителя —
    отдельные ресурсы (issue #5).
    """
    if not actor.is_staff:
        raise forbidden("Этот раздел доступен сотрудникам школы.")


def require_family(actor: Actor) -> None:
    """Кабинет родителя: ресурсы `/me/*`.

    Сотрудника сюда не пускаем не из вредности, а потому что «мои дети»
    у него не определены: `visible_student_ids` отвечает владельцу и
    администратору `None` — «ограничения нет», — и `/me/children` показал бы
    им всю школу как свою семью. Роль `student` входит наравне с `guardian`:
    взрослый ученик платит за себя сам и должен видеть про себя ровно то же,
    что родитель видит про ребёнка (§2).
    """
    if not actor.is_family:
        raise forbidden(
            "Кабинет — для родителей и взрослых учеников. "
            "Экраны школы лежат в остальных разделах.",
            "family_only",
        )


def family_student_ids(cur: psycopg.Cursor, actor: Actor) -> list[str]:
    """Состав детей ИЗ СЕССИИ, а не из параметров запроса.

    Отдельная функция, потому что у `/me/*` `None` («ограничения нет») —
    не ответ, а ошибка: пустой список безопаснее и означает «детей нет».
    """
    allowed = visible_student_ids(cur, actor)
    return sorted(allowed) if allowed is not None else []


def require_admin(actor: Actor) -> None:
    """Операции ресепшена: продажа, заморозка, воронка, отчёты.

    Преподаватель не продаёт абонементы и не ведёт воронку — по §2 он делает
    ровно две вещи: отмечает свои занятия и пишет заметки.
    """
    if actor.role not in (OWNER, ADMIN):
        raise forbidden("Операция доступна администратору или владельцу школы.")


def require_owner(actor: Actor, what: str = "Раздел") -> None:
    """Деньги людей. §2: администратор видит всё, КРОМЕ ставок ЗП других
    сотрудников — а ведомость целиком из них и состоит."""
    if not actor.is_owner:
        raise forbidden(f"{what} доступен только владельцу школы.", "owner_only")


def require_branch(actor: Actor, branch_id: str | None) -> None:
    """Права по филиалам через staff_branch (§2: «администратор филиала Абая
    не правит расписание Аль-Фараби»).

    Владелец видит все филиалы по определению. У сотрудника без единой строки
    в staff_branch ограничения нет — см. комментарий к Actor.branch_ids.
    """
    if actor.is_owner or not actor.branch_ids or branch_id is None:
        return
    if str(branch_id) not in actor.branch_ids:
        raise forbidden(
            "Это другой филиал. Вы работаете в другом — обратитесь к владельцу школы.",
            "other_branch",
        )


def require_lesson_write(actor: Actor, lesson: dict[str, Any]) -> None:
    """Право отметить занятие. Самое дорогое место во всём модуле.

    Преподаватель отмечает ТОЛЬКО свои занятия: чужая отметка двигает чужой
    абонемент и чужую зарплату, а объяснить родителю списанное занятие,
    которого не было, потом нечем. Проверка идёт по `lesson.teacher_id`,
    то есть по факту в расписании, а не по совпадению направления или филиала.
    """
    if actor.is_family:
        raise forbidden("Посещаемость отмечает школа, а не родитель.")
    if actor.is_owner:
        return
    if actor.is_teacher:
        if actor.staff_id is None or str(lesson["teacher_id"]) != actor.staff_id:
            raise forbidden(
                "Это занятие ведёт другой преподаватель. Отмечать можно только свои.",
                "not_your_lesson",
            )
        return
    require_branch(actor, str(lesson["branch_id"]) if lesson.get("branch_id") else None)


def may_see_teacher_rate(actor: Actor, staff_id: str | None) -> bool:
    """Свою ставку видит каждый, чужую — только владелец (§2).

    Администратор ресепшена ведёт расписание и продаёт абонементы, но сколько
    получает преподаватель — не его дело: это первое, что утекает в чат школы.
    """
    if actor.is_owner:
        return True
    return staff_id is not None and actor.staff_id == str(staff_id)


# ---------------------------------------------------------------------------
# Фильтры видимости: какие строки видно внутри операции
# ---------------------------------------------------------------------------


def visible_student_ids(cur: psycopg.Cursor, actor: Actor) -> frozenset[str] | None:
    """Ученики, которых актору позволено видеть. None = ограничения нет.

    None, а не «все идентификаторы школы»: у школы их тысячи, и подставлять
    такой список в каждый запрос значило бы платить за ограничение,
    которого нет.
    """
    if actor.is_owner or actor.role == ADMIN:
        return None
    if actor.is_teacher:
        return frozenset(repo.teacher_student_ids(cur, actor.staff_id or ""))
    if actor.role == GUARDIAN:
        return frozenset(repo.guardian_student_ids(cur, actor.person_id))
    if actor.role == STUDENT:
        # Взрослый ученик видит себя — и своих детей, если он же за них платит.
        return frozenset(
            repo.own_student_ids(cur, actor.person_id)
            + repo.guardian_student_ids(cur, actor.person_id)
        )
    return frozenset()


def require_student(cur: psycopg.Cursor, actor: Actor, student_id: str) -> None:
    """Доступ к карточке ученика. Чужой ребёнок отвечает 404, а не 403.

    404 здесь не вежливость, а требование: 403 подтверждал бы, что ученик
    с таким идентификатором существует, и перебором чужого списка можно было
    бы пересчитать всю школу.
    """
    allowed = visible_student_ids(cur, actor)
    if allowed is not None and str(student_id) not in allowed:
        raise not_found("Ученик не найден")


def may_see_lesson(cur: psycopg.Cursor, actor: Actor, lesson: dict[str, Any]) -> bool:
    """Видно ли занятие целиком. Родителю — только если там его ребёнок."""
    if actor.is_owner or actor.role == ADMIN:
        return True
    if actor.is_teacher:
        return actor.staff_id is not None and str(lesson["teacher_id"]) == actor.staff_id
    allowed = visible_student_ids(cur, actor)
    participants = {str(p["student_id"]) for p in repo.lesson_participants(cur, lesson)}
    return bool(participants & allowed)
