"""HTTP-слой RockCRM.

Этап 1: расписание и отметка посещаемости.
Этап 2: поиск и карточка ученика, тарифы, продажа и продление абонемента,
заморозка и её снятие.
Этап 3: воронка заявок, пробный урок, конверсия в ученика, приём по вебхуку.
Этап 5: вход по телефону, сессии и разграничение прав по ролям,
кабинет родителя отдельными ресурсами `/me/*` и очередь заявок из него.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import attendance as attendance_service
from . import api_keys, auth, authz, billing, config, family, journal, money, phone
from . import repository as repo, schemas
from . import leads as leads_service
from . import students as students_service
from .db import close_pool, get_pool, set_tenant, tenant_tx, untenanted_tx
from .errors import ApiError, not_found, translate_db_error
from .rules import applied_summary, compute_all_effects, rolled_back as compute_rollback


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_pool()
    yield
    close_pool()


app = FastAPI(
    title="RockCRM API",
    version="3.0.0",
    description=(
        "Расписание, отметка посещаемости, жизненный цикл абонемента "
        "и воронка заявок. Контракты этапов 1–3."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    # Кука с сессией не уедет и не приедет без этого флага, а «*» вместе
    # с учётными данными запрещён стандартом — переключатель в config.py.
    allow_credentials=config.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Кто спрашивает
#
# Заголовков X-Tenant-Id и X-User-Id больше нет. Тенант — следствие входа:
# браузер предъявляет непрозрачный токен, приложение находит по нему сессию
# и только из неё узнаёт школу и человека. Подменить школу теперь нельзя,
# не имея чужого токена, а токена в базе нет — есть его хеш.
#
# Ключи внешних источников (X-Api-Key) работают как работали: они для систем,
# а не для людей, и вход по телефону им не нужен.
# ---------------------------------------------------------------------------


def _presented_token(request: Request) -> str | None:
    """Токен из куки или из заголовка Authorization.

    Кука — путь браузера: она HttpOnly, и межсайтовый скрипт её не прочитает.
    Bearer — путь всего остального: curl, мобильный клиент, интеграционные
    тесты. Токен один и тот же, и это осознанно: два разных секрета означали бы
    две разные таблицы и два разных способа их отозвать.
    """
    header = request.headers.get("authorization") or ""
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return request.cookies.get(auth.COOKIE_NAME)


def _client_ip(request: Request) -> str | None:
    """Адрес клиента для журнала попыток. Не адрес — не повод падать.

    В тестах и за прокси без X-Forwarded-For сюда приезжает что угодно вплоть
    до слова «testclient», а колонка объявлена inet: непроверенное значение
    роняло бы вход, то есть ошибка в логировании стоила бы входа в систему.
    """
    host = getattr(request.client, "host", None)
    if not host:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def current_actor(request: Request) -> authz.Actor:
    """Сессия -> тенант -> учётная запись -> роль и филиалы.

    Отдельная короткая транзакция до основной: сессия ищется ДО set_config,
    потому что тенант — это то, что она сообщает (тот же единственный
    обоснованный случай, что и у ключей источников, см. db.untenanted_tx).
    """
    token = _presented_token(request)
    if not token:
        raise ApiError(
            401,
            "no_session",
            "Нужен вход. Запросите код на POST /api/v1/auth/request-code.",
        )
    with untenanted_tx() as cur:
        session = auth.session_by_token(cur, token)
        if session is None:
            # Истёкшая, отозванная и выдуманная сессии отвечают одинаково:
            # разница между ними ничего не даёт человеку и многое — тому,
            # кто подбирает.
            raise ApiError(
                401, "bad_session", "Сессия недействительна или истекла. Войдите заново."
            )
        set_tenant(cur, str(session["tenant_id"]))
        auth.touch_session(cur, str(session["id"]))
        return authz.load_actor(cur, str(session["user_id"]), str(session["id"]))


ActorDep = Annotated[authz.Actor, Depends(current_actor)]
# Прежнее имя оставлено, потому что ниже по коду `who.tenant_id`
# и `who.user_id` читаются два десятка раз и означают ровно то же самое.
CallerDep = ActorDep


# ---------------------------------------------------------------------------
# Обработка ошибок: наружу всегда {"error": {...}}
# ---------------------------------------------------------------------------


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.exception_handler(psycopg.Error)
async def _db_error_handler(request: Request, exc: psycopg.Error) -> JSONResponse:
    # Ограничения базы — это бизнес-правила, а не сбой: 409/422 с текстом,
    # а не 500 «что-то пошло не так».
    api = translate_db_error(exc)
    return JSONResponse(status_code=api.status, content=api.body())


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) or "тело" for e in exc.errors())
    api = ApiError(
        400,
        "bad_request",
        f"Тело или параметры запроса не прошли проверку: {fields}.",
        {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
    )
    return JSONResponse(status_code=api.status, content=api.body())


# ---------------------------------------------------------------------------
# Ресурсы
# ---------------------------------------------------------------------------

API = "/api/v1"


# ---------------------------------------------------------------------------
# Вход
#
# Основной сценарий — телефон и одноразовый код: родители не помнят паролей,
# а кабинет родителя без входа не существует. Пароль оставлен сотрудникам:
# администратор ресепшена входит по двадцать раз в день, и SMS на каждый вход
# была бы не безопасностью, а счётом от оператора.
# ---------------------------------------------------------------------------


def _normalize_login(value: str) -> str:
    """Логин к одному виду. Телефон — в E.164, всё остальное — как есть.

    Телефон приводится ровно тем же правилом, что и везде в системе
    (`phone.normalize`): «8 701 555 24 18», «+7 (701) 555-24-18»
    и «77015552418» — один человек, и вход обязан это знать так же,
    как это знают поиск и вебхук.
    """
    text = (value or "").strip()
    return phone.normalize(text) or text.lower()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        httponly=True,          # межсайтовый скрипт не прочитает
        secure=config.AUTH_COOKIE_SECURE,
        samesite=config.AUTH_COOKIE_SAMESITE,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        path="/",
    )


def _tenant_or_404(cur: Any, slug: str) -> dict[str, Any]:
    row = auth.tenant_by_slug(cur, slug)
    if row is None:
        raise ApiError(
            404,
            "unknown_tenant",
            "Школа не найдена. Проверьте адрес, по которому открыт кабинет.",
        )
    return row


def _me(cur: Any, actor: authz.Actor) -> dict[str, Any]:
    cur.execute("SELECT id, name FROM tenant WHERE id = %s", (actor.tenant_id,))
    tenant = cur.fetchone()
    visible = authz.visible_student_ids(cur, actor)
    return {
        "user_id": actor.user_id,
        "name": actor.name,
        "role": actor.role,
        "tenant": {"id": str(tenant["id"]), "name": tenant["name"]},
        "person_id": actor.person_id,
        "staff_id": actor.staff_id,
        "branch_ids": sorted(actor.branch_ids),
        # У владельца и администратора список пуст: ограничения по ученикам
        # у них нет, и перечислять всю школу здесь незачем. У родителя это
        # его дети — кабинету они нужны сразу, без второго запроса.
        "student_ids": sorted(visible) if visible is not None else [],
    }


@app.post(
    f"{API}/auth/request-code",
    response_model=schemas.CodeSent,
    status_code=202,
    tags=["Вход"],
)
def request_code(request: Request, body: schemas.CodeRequest) -> dict[str, Any]:
    """Выслать одноразовый код на телефон.

    Ответ одинаков для существующего и несуществующего телефона — включая
    маску адреса, которая собирается из того, что прислали, а не из того, что
    нашлось в базе. Иначе форма входа отвечала бы на вопрос «ходит ли этот
    ребёнок в эту школу», а это чужие персональные данные.

    Код кладётся в очередь уведомлений (`notification`) — ту же, в которой
    живут напоминания об уроке. Воркера, который относит их в SMS, пока нет.
    """
    login = _normalize_login(body.login)
    ip = _client_ip(request)
    failure: ApiError | None = None

    with untenanted_tx() as cur:
        tenant = _tenant_or_404(cur, body.tenant)
        set_tenant(cur, str(tenant["id"]))
        try:
            # Лимит считается ДО поиска пользователя: перебор телефонов школы
            # не должен обходиться тем, что таких учётных записей ещё нет.
            auth.guard_code_request(cur, login, ip)
        except ApiError as exc:
            failure = exc
        else:
            user = auth.user_by_login(cur, login)
            ok = user is not None and user["is_active"]
            # Попытка пишется в ЛЮБОМ случае и до всякого возможного отказа:
            # запись, потерянная откатом, означала бы лимит, который
            # не накапливается, то есть лимита нет.
            auth.record_attempt(cur, str(tenant["id"]), "code_request", login, ip, ok)
            if ok:
                auth.issue_code(cur, str(tenant["id"]), user, ip)

    if failure is not None:
        raise failure
    return {
        "sent": True,
        "to": auth.mask_phone(login),
        "expires_in": int(auth.CODE_TTL.total_seconds()),
        "message": "Если такая учётная запись есть, код придёт в течение минуты.",
    }


@app.post(f"{API}/auth/login", response_model=schemas.LoggedIn, tags=["Вход"])
def login(
    request: Request, response: Response, body: schemas.LoginRequest
) -> dict[str, Any]:
    """Вход по коду или по паролю. В ответ — сессия в куке и она же токеном."""
    if bool(body.code) == bool(body.password):
        raise ApiError(
            400,
            "bad_credentials_form",
            "Пришлите либо одноразовый код, либо пароль — что-то одно.",
        )

    login_value = _normalize_login(body.login)
    ip = _client_ip(request)
    kind = "code_verify" if body.code else "password"
    result: dict[str, Any] | None = None
    failure: ApiError | None = None

    # Вся работа идёт внутри транзакции, а отказ возвращается наружу
    # значением: исключение откатило бы и счётчик попыток по коду,
    # и строку в журнале попыток — то есть перебор снова стал бы бесплатным.
    with untenanted_tx() as cur:
        tenant = _tenant_or_404(cur, body.tenant)
        set_tenant(cur, str(tenant["id"]))
        try:
            if body.code:
                auth.guard_code_verify(cur, login_value)
            else:
                auth.guard_password(cur, login_value)
        except ApiError as exc:
            failure = exc
        else:
            user = auth.user_by_login(cur, login_value)
            ok = False
            if user is not None and user["is_active"]:
                ok = (
                    auth.consume_code(cur, str(user["id"]), body.code)
                    if body.code
                    else auth.verify_secret(body.password, user["password_hash"])
                )
            auth.record_attempt(cur, str(tenant["id"]), kind, login_value, ip, ok)

            if not ok:
                # Неверный код, неверный пароль, выключенная учётная запись
                # и несуществующий телефон отвечают одинаково.
                failure = ApiError(
                    401,
                    "bad_credentials",
                    "Не подошло. Проверьте номер и код — или запросите новый код.",
                )
            else:
                token, session = auth.create_session(
                    cur,
                    str(tenant["id"]),
                    str(user["id"]),
                    ip,
                    request.headers.get("user-agent"),
                )
                actor = authz.load_actor(cur, str(user["id"]), str(session["id"]))
                journal.audit(
                    cur,
                    str(tenant["id"]),
                    str(user["id"]),
                    "auth.login",
                    "user_session",
                    str(session["id"]),
                    {"method": "code" if body.code else "password", "ip": ip},
                )
                result = {
                    "user": _me(cur, actor),
                    "expires_at": session["expires_at"].isoformat(),
                    "token": token,
                }

    if failure is not None:
        raise failure
    _set_session_cookie(response, result["token"])
    return result


@app.post(f"{API}/auth/logout", response_model=schemas.LoggedOut, tags=["Вход"])
def logout(
    who: ActorDep,
    response: Response,
    everywhere: Annotated[
        bool, Query(description="Погасить все сессии, а не только текущую")
    ] = False,
) -> dict[str, Any]:
    """Выход. Сессия гасится в базе, а не только забывается браузером.

    Строка `user_session` остаётся: «когда вышли» — такой же факт, как «когда
    вошли». Удаление куки без гашения строки означало бы, что украденный токен
    продолжает работать ещё месяц.
    """
    with tenant_tx(who.tenant_id) as cur:
        if everywhere:
            count = auth.revoke_user_sessions(cur, who.user_id)
        else:
            auth.revoke_session(cur, who.session_id)
            count = 1
        journal.audit(
            cur, who.tenant_id, who.user_id, "auth.logout", "user_session",
            who.session_id, {"everywhere": everywhere, "sessions": count},
        )
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {
        "ok": True,
        "message": "Вы вышли из всех сеансов." if everywhere else "Вы вышли.",
    }


@app.get(f"{API}/auth/me", response_model=schemas.Me, tags=["Вход"])
def whoami(who: ActorDep) -> dict[str, Any]:
    """Кто вошёл, с какой ролью и что ему видно.

    Интерфейс рисует разные экраны разным ролям, и спрашивать об этом
    он обязан сервер: роль, вычисленная на клиенте, — это роль, которую
    клиент себе назначил.
    """
    with tenant_tx(who.tenant_id) as cur:
        return _me(cur, who)

# Текст один на все четыре операции: описание даты операции живёт
# в schemas.EFFECTIVE_DATE, а параметры пути и запроса берут его оттуда,
# чтобы в OpenAPI не оказалось двух разных объяснений одного правила.
EFFECTIVE_DATE_DOC = schemas.EFFECTIVE_DATE.description


@app.get(f"{API}/branches", response_model=list[schemas.Branch], tags=["Справочники"])
def branches(who: CallerDep) -> list[dict[str, Any]]:
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_branches(cur)


@app.get(f"{API}/teachers", response_model=list[schemas.Teacher], tags=["Справочники"])
def teachers(
    who: CallerDep,
    branch_id: Annotated[str | None, Query(description="Только работающие в филиале")] = None,
    discipline_id: Annotated[
        str | None, Query(description="Только ведущие это направление")
    ] = None,
) -> list[dict[str, Any]]:
    """Преподаватели школы — справочник для диалога назначения пробного.

    Не срез дня из расписания: преподаватель с выходным обязан быть в списке,
    иначе назначить ему пробный на завтра нельзя.
    """
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_teachers(cur, branch_id, discipline_id)


@app.get(f"{API}/rooms", response_model=list[schemas.Room], tags=["Справочники"])
def rooms(
    who: CallerDep,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> list[dict[str, Any]]:
    """Кабинеты с филиалом и характеристиками (`features`)."""
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_rooms(cur, branch_id)


@app.get(f"{API}/disciplines", response_model=list[schemas.Discipline], tags=["Справочники"])
def disciplines(who: CallerDep) -> list[dict[str, Any]]:
    """Направления с `min_age` и требованиями к кабинету.

    Без них форма заведения заявки не может предложить направление вовсе,
    и заявка уходит в воронку с пустым `discipline_id` — то есть выпадает
    из отчёта по направлениям.
    """
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_disciplines(cur)


@app.get(f"{API}/schedule", response_model=schemas.Schedule, tags=["Расписание"])
def schedule(
    who: CallerDep,
    branch_id: Annotated[str, Query(description="UUID филиала")],
    date: Annotated[dt.date, Query(description="День расписания, YYYY-MM-DD")],
) -> dict[str, Any]:
    """День филиала. Преподаватель видит в нём только свои занятия (§2).

    Родитель сюда не ходит: это сетка филиала со всеми чужими детьми
    в дорожках. Расписание своего ребёнка — отдельный ресурс кабинета
    родителя (issue #5).
    """
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        branch = repo.get_branch(cur, branch_id)
        if branch is None:
            raise not_found("Филиал не найден")
        tz = ZoneInfo(branch["timezone"])

        lessons = repo.lessons_of_day(cur, branch_id, date, tz)
        if who.is_teacher:
            # Фильтр стоит до сборки дорожек и до сводки: посчитать конфликты
            # и загрузку по чужим занятиям, а показать свои — значит выдать
            # преподавателю числа, которые он не может проверить.
            lessons = [
                row for row in lessons if str(row["teacher_id"]) == (who.staff_id or "")
            ]
        lesson_ids = [str(row["id"]) for row in lessons]
        marks = repo.marks_by_lesson(cur, lesson_ids)
        conflicts = repo.find_conflicts(lessons)
        teacher_ids = list({str(row["teacher_id"]) for row in lessons})
        disciplines = repo.disciplines_by_teacher(cur, teacher_ids)
        rooms_total = repo.branch_room_count(cur, branch_id)

        # Дорожки — только преподаватели с занятиями в этот день, в порядке
        # первого занятия. Пустые дорожки съедали бы высоту экрана впустую.
        tracks: dict[str, dict[str, Any]] = {}
        for row in lessons:
            teacher_id = str(row["teacher_id"])
            track = tracks.setdefault(
                teacher_id,
                {
                    "teacher": {
                        "id": teacher_id,
                        "name": row["teacher_name"],
                        "disciplines": disciplines.get(teacher_id, []),
                        "color": row["teacher_color"],
                    },
                    "lessons": [],
                },
            )
            lesson_id = str(row["id"])
            track["lessons"].append(
                {
                    "id": lesson_id,
                    "starts_at": repo.iso(row["starts_at"], tz),
                    "ends_at": repo.iso(row["ends_at"], tz),
                    "duration_min": row["duration_min"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "title": row["title"],
                    "student_id": str(row["student_id"]) if row["student_id"] else None,
                    "room": {"id": str(row["room_id"]), "name": row["room_name"]},
                    "attendance_mark": marks.get(lesson_id),
                    "conflicts": conflicts.get(lesson_id, []),
                }
            )

        open_minutes = _minutes_between(branch["opens_at"], branch["closes_at"])
        busy_minutes = sum(row["duration_min"] for row in lessons)
        capacity = rooms_total * open_minutes
        conflict_pairs = {
            frozenset((lesson_id, c["with_lesson_id"]))
            for lesson_id, items in conflicts.items()
            for c in items
        }

        return {
            "date": date.isoformat(),
            "branch": {
                "id": str(branch["id"]),
                "name": branch["name"],
                "opens_at": branch["opens_at_txt"],
                "closes_at": branch["closes_at_txt"],
            },
            "tracks": list(tracks.values()),
            "summary": {
                "lessons": len(lessons),
                "trials": sum(1 for row in lessons if row["kind"] == "trial"),
                "conflicts": len(conflict_pairs),
                "room_utilization_pct": round(busy_minutes / capacity * 100) if capacity else 0,
            },
        }


@app.get(f"{API}/lessons/{{lesson_id}}", response_model=schemas.LessonCard, tags=["Расписание"])
def lesson_card(who: CallerDep, lesson_id: str) -> dict[str, Any]:
    """Карточка занятия с предпросмотром последствий отметки.

    Кто что видит: преподаватель — только свои занятия, родитель — только те,
    где стоит его ребёнок, и в них только своего ребёнка. Ставка
    преподавателя — только владельцу и самому преподавателю (§2).
    """
    with tenant_tx(who.tenant_id) as cur:
        lesson = repo.get_lesson(cur, lesson_id)
        if lesson is None:
            raise not_found("Занятие не найдено")
        # 404, а не 403: «есть, но не ваше» подтверждало бы, что занятие
        # существует, и по перебору идентификаторов читалась бы вся школа.
        if not authz.may_see_lesson(cur, who, lesson):
            raise not_found("Занятие не найдено")

        tz = ZoneInfo(lesson["branch_timezone"])
        rate_amount, rate_percent = repo.teacher_rate(cur, lesson)
        marked = repo.attendance_by_student(cur, lesson_id)
        applied = repo.applied_effects(cur, lesson_id)
        on = lesson["starts_at"].date()
        # Родителю в групповом занятии видно только своего ребёнка: имя, остаток
        # и абонемент соседа по ансамблю — чужие персональные данные, и утечь
        # они могут именно здесь, а не в списке учеников.
        visible = authz.visible_student_ids(cur, who)

        participants = []
        for row in repo.lesson_participants(cur, lesson):
            student_id = str(row["student_id"])
            if visible is not None and student_id not in visible:
                continue
            sub = repo.active_subscription(cur, student_id, on)
            att = marked.get(student_id)
            fact = applied.get(student_id)

            # У отмеченного участника остаток в базе УЖЕ уменьшен, и предпросмотр
            # от него вычел бы занятие второй раз (issue #22). Считаем от остатка
            # до списания: переотметка всё равно начинается с отмены прежней
            # отметки, а она вернёт занятие назад — предпросмотр обязан обещать
            # именно тот остаток, который получится.
            base = (
                sub
                if fact is None
                else compute_rollback(
                    sub, int(fact["lessons_delta"]), int(fact["makeups_delta"])
                )
            )
            effects = compute_all_effects(base, rate_amount, rate_percent)

            participants.append(
                {
                    "student_id": student_id,
                    "name": row["name"],
                    "attendance": att["mark"] if att else None,
                    "attendance_id": str(att["id"]) if att else None,
                    "subscription": None
                    if sub is None
                    else {
                        "id": sub.id,
                        "lessons_total": sub.lessons_total,
                        "lessons_balance": sub.lessons_balance,
                        "makeups_balance": sub.makeups_balance,
                        "valid_until": sub.valid_until.isoformat(),
                        "status": sub.status,
                    },
                    "mark_effects": {m: e.api_dict() for m, e in effects.items()},
                    "applied_effect": _applied_effect(fact, sub),
                }
            )

        return {
            "id": str(lesson["id"]),
            "starts_at": repo.iso(lesson["starts_at"], tz),
            "ends_at": repo.iso(lesson["ends_at"], tz),
            "duration_min": lesson["duration_min"],
            "kind": lesson["kind"],
            "status": lesson["status"],
            "title": lesson["title"],
            "room": {"id": str(lesson["room_id"]), "name": lesson["room_name"]},
            "teacher": {
                "id": str(lesson["teacher_id"]),
                "name": lesson["teacher_name"],
                # None — «вам не видно», а не «ставки нет»: ноль читался бы
                # как «работает бесплатно» и однажды попал бы в разговор.
                "rate": (int(rate_amount or 0))
                if authz.may_see_teacher_rate(who, str(lesson["teacher_id"]))
                else None,
            },
            "participants": participants,
            "note": repo.lesson_note(
                cur, lesson_id, None if visible is None else sorted(visible)
            ),
        }


@app.post(
    f"{API}/lessons/{{lesson_id}}/attendance",
    response_model=schemas.AttendanceApplied,
    status_code=201,
    tags=["Посещаемость"],
)
def mark_attendance(
    who: CallerDep, lesson_id: str, body: schemas.AttendanceRequest
) -> dict[str, Any]:
    """Отметка посещаемости.

    Отмечать может владелец, администратор своего филиала и преподаватель —
    только СВОИ занятия. Чужая отметка двигает чужой абонемент и чужую
    зарплату, а объяснить родителю списанное занятие, которого не было,
    потом нечем.

    `effective_date` в теле задаёт дату операции; без него — сегодня в поясе
    филиала, то есть прежнее поведение (ADR-001).
    """
    student_id = _uuid_or_400(body.student_id, "student_id")
    with tenant_tx(who.tenant_id) as cur:
        lesson = repo.get_lesson(cur, lesson_id)
        if lesson is None:
            raise not_found("Занятие не найдено")
        authz.require_lesson_write(who, lesson)
        return attendance_service.apply_mark(
            cur,
            who.tenant_id,
            who.user_id,
            lesson_id,
            student_id,
            body.mark,
            body.effective_date,
        )


@app.delete(
    f"{API}/attendance/{{attendance_id}}",
    response_model=schemas.AttendanceRevoked,
    tags=["Посещаемость"],
)
def revoke_attendance(
    who: CallerDep,
    attendance_id: str,
    effective_date: Annotated[dt.date | None, Query(description=EFFECTIVE_DATE_DOC)] = None,
) -> dict[str, Any]:
    """Отмена отметки.

    Дата операции приезжает параметром запроса, а не телом: у DELETE тела нет,
    и заводить его ради одного поля значило бы сделать метод неотличимым
    от POST для всякого прокси на пути.
    """
    with tenant_tx(who.tenant_id) as cur:
        # Право проверяется по ЗАНЯТИЮ, а не по отметке: отменяют не строку
        # в таблице, а чужой урок. Ненайденная отметка до проверки не доходит
        # и получает свой 404 от самой операции — чужой тенант обязан
        # отвечать так же, как несуществующий идентификатор.
        lesson = repo.lesson_of_attendance(cur, attendance_id)
        if lesson is not None:
            authz.require_lesson_write(who, lesson)
        return attendance_service.revoke_mark(
            cur, who.tenant_id, who.user_id, attendance_id, effective_date
        )


# ---------------------------------------------------------------------------
# Ученики, тарифы, абонементы — этап 2
# ---------------------------------------------------------------------------


@app.get(f"{API}/students", response_model=list[schemas.StudentInList], tags=["Ученики"])
def find_students(
    who: CallerDep,
    query: Annotated[str, Query(description="Имя ученика, имя или телефон плательщика")] = "",
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    """Поиск ученика.

    Выдача урезается по роли: преподаватель находит только своих учеников,
    родитель — только своих детей. Ограничение уезжает в тот же запрос,
    а не накладывается на ответ: `limit` иначе отрезал бы детей родителя
    раньше, чем до них дошла бы очередь, и кабинет показал бы пустой список.
    Второй, «родительской», версии запроса при этом не появилось — она
    разошлась бы с первой при первой же правке поиска по телефону.
    """
    with tenant_tx(who.tenant_id) as cur:
        visible = authz.visible_student_ids(cur, who)
        return students_service.search(
            cur, query, branch_id, limit, None if visible is None else sorted(visible)
        )


@app.get(
    f"{API}/students/{{student_id}}", response_model=schemas.StudentCard, tags=["Ученики"]
)
def student_card(who: CallerDep, student_id: str) -> dict[str, Any]:
    """Карточка ученика: семья, абонемент, журнал движений, отработки, заметки.

    Чужой ребёнок отвечает 404, а не 403: 403 подтверждал бы, что такой ученик
    в школе есть, и перебором идентификаторов можно было бы пересчитать всех.
    """
    with tenant_tx(who.tenant_id) as cur:
        authz.require_student(cur, who, student_id)
        card = students_service.card(cur, student_id)
        if card is None:
            raise not_found("Ученик не найден")
        return card


@app.get(f"{API}/plans", response_model=list[schemas.Plan], tags=["Справочники"])
def plans(
    who: CallerDep,
    discipline_id: Annotated[str | None, Query(description="UUID направления")] = None,
    format: Annotated[str | None, Query(description="individual | pair | group | trial")] = None,
) -> list[dict[str, Any]]:
    authz.require_staff(who)
    with tenant_tx(who.tenant_id) as cur:
        return [
            {
                "id": str(row["id"]),
                "name": row["name"],
                "discipline": row["discipline"],
                "format": row["format"],
                "duration_min": int(row["duration_min"]),
                "lessons_count": int(row["lessons_count"]),
                "valid_days": int(row["valid_days"]),
                "price": int(row["price"]),
            }
            for row in repo.list_plans(cur, discipline_id, format)
        ]


@app.post(
    f"{API}/students/{{student_id}}/subscriptions",
    response_model=schemas.SoldSubscription,
    status_code=201,
    tags=["Абонементы"],
)
def sell_subscription(
    who: CallerDep, student_id: str, body: schemas.SellRequest
) -> dict[str, Any]:
    """Продажа и продление. Деньги принимает ресепшен, а не преподаватель (§2)."""
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        student = repo.get_student(cur, student_id)
        if student is None:
            raise not_found("Ученик не найден")
        authz.require_branch(who, str(student["branch_id"]) if student["branch_id"] else None)
        return billing.sell_subscription(cur, who.tenant_id, who.user_id, student_id, body)


@app.post(
    f"{API}/subscriptions/{{subscription_id}}/holds",
    response_model=schemas.HoldCreated,
    status_code=201,
    tags=["Абонементы"],
)
def freeze_subscription(
    who: CallerDep, subscription_id: str, body: schemas.HoldRequest
) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return billing.create_hold(cur, who.tenant_id, who.user_id, subscription_id, body)


@app.delete(
    f"{API}/subscriptions/{{subscription_id}}/holds/{{hold_id}}",
    response_model=schemas.HoldReleased,
    tags=["Абонементы"],
)
def unfreeze_subscription(
    who: CallerDep,
    subscription_id: str,
    hold_id: str,
    effective_date: Annotated[dt.date | None, Query(description=EFFECTIVE_DATE_DOC)] = None,
) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return billing.release_hold(
            cur, who.tenant_id, who.user_id, subscription_id, hold_id, effective_date
        )


# ---------------------------------------------------------------------------
# Воронка заявок — этап 3
# ---------------------------------------------------------------------------


@app.get(f"{API}/leads", response_model=schemas.Board, tags=["Заявки"])
def leads_board(
    who: CallerDep,
    stage: Annotated[str | None, Query(description="Одна стадия вместо всей доски")] = None,
    source: Annotated[str | None, Query(description="Источник заявки")] = None,
    assigned_to: Annotated[str | None, Query(description="UUID сотрудника")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
    # Постраничная подгрузка колонки. Ограничение действует на КАЖДУЮ колонку:
    # общее съела бы одна «Отказ», в которой на живой школе пятьсот заявок.
    limit: Annotated[
        int,
        Query(ge=1, le=leads_service.MAX_PAGE, description="Карточек в одной колонке"),
    ] = leads_service.DEFAULT_PAGE,
    offset: Annotated[
        int, Query(ge=0, description="Сколько карточек колонки пропустить")
    ] = 0,
) -> dict[str, Any]:
    """Доска воронки. Заявками занимается ресепшен, а не преподаватель (§2)."""
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return leads_service.board(
            cur, stage, source, assigned_to, branch_id, limit, offset
        )


# Отчёт объявлен ДО /leads/{lead_id}: иначе FastAPI сопоставит «funnel»
# с параметром пути, и отчёт будет вечно отвечать «заявка не найдена».
@app.get(f"{API}/leads/funnel", response_model=schemas.Funnel, tags=["Заявки"])
def leads_funnel(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from", description="Начало периода")] = None,
    to: Annotated[dt.date | None, Query(description="Конец периода, включительно")] = None,
) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        # Пояс школы, а не сервера: «за август» у школы в Алматы начинается
        # раньше, чем в UTC, и заявка первого числа иначе выпала бы из отчёта.
        tz_name = repo.tenant_timezone(cur, who.tenant_id)
        today = dt.datetime.now(ZoneInfo(tz_name)).date()
        until = to or today
        since = from_ or (until - dt.timedelta(days=30))
        if since > until:
            raise ApiError(
                400, "bad_period", "Начало периода позже его конца — проверьте from и to."
            )
        return leads_service.funnel(cur, since, until, tz_name)


@app.get(f"{API}/leads/{{lead_id}}", response_model=schemas.LeadFull, tags=["Заявки"])
def lead_card(who: CallerDep, lead_id: str) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        card = leads_service.card(cur, lead_id)
        if card is None:
            raise not_found("Заявка не найдена")
        return card


@app.post(f"{API}/leads", response_model=schemas.LeadFull, status_code=201, tags=["Заявки"])
def create_lead(who: CallerDep, body: schemas.LeadCreate) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return leads_service.create_lead(cur, who.tenant_id, who.user_id, body)


@app.patch(f"{API}/leads/{{lead_id}}", response_model=schemas.LeadFull, tags=["Заявки"])
def patch_lead(who: CallerDep, lead_id: str, body: schemas.LeadPatch) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return leads_service.update_lead(cur, who.tenant_id, who.user_id, lead_id, body)


@app.post(
    f"{API}/leads/{{lead_id}}/trial",
    response_model=schemas.TrialBooked,
    status_code=201,
    tags=["Заявки"],
)
def book_trial(who: CallerDep, lead_id: str, body: schemas.TrialRequest) -> dict[str, Any]:
    """Пробный урок. Это запись в расписание — то есть тот самый случай,
    где §2 запрещает администратору одного филиала трогать другой."""
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        room = repo.get_room(cur, body.room_id) if body.room_id else None
        if room is not None:
            authz.require_branch(who, str(room["branch_id"]))
        return leads_service.book_trial(cur, who.tenant_id, who.user_id, lead_id, body)


@app.post(
    f"{API}/leads/{{lead_id}}/convert",
    response_model=schemas.Converted,
    status_code=201,
    tags=["Заявки"],
)
def convert_lead(who: CallerDep, lead_id: str, body: schemas.ConvertRequest) -> dict[str, Any]:
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return leads_service.convert(cur, who.tenant_id, who.user_id, lead_id, body)


@app.post(f"{API}/hooks/leads", response_model=schemas.LeadFull, tags=["Заявки"])
def leads_webhook(
    body: schemas.WebhookLead,
    response: Response,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Приём заявок от Telegram-бота, LeadHub и форм сайта.

    Заголовки X-Tenant-Id и X-User-Id здесь намеренно не читаются: тенант
    определяется ключом. Ответ 201 на созданную заявку и 200 на повторную
    доставку того же external_id — ретрай обязан быть безопасным.
    """
    with untenanted_tx() as cur:
        key = api_keys.authenticate(cur, x_api_key)
        api_keys.require_scope(key, api_keys.WRITE_LEADS)
        # Тенант становится известен только сейчас — и только теперь имеет
        # смысл включать изоляцию строк.
        set_tenant(cur, str(key["tenant_id"]))
        api_keys.mark_used(cur, str(key["id"]))
        card, created = leads_service.accept_webhook(cur, str(key["tenant_id"]), body)
        response.status_code = 201 if created else 200
        return card


# ---------------------------------------------------------------------------
# Деньги и ЗП — этап 4
#
# Всё читается из того, что уже посчитано отметкой посещаемости: зарплата
# лежит в payroll_entry, деньги в payment. Второго расчёта здесь нет
# намеренно — он разошёлся бы с первым при первой смене ставки.
# ---------------------------------------------------------------------------


def _period(
    who: authz.Actor,
    cur: Any,
    from_: dt.date | None,
    to: dt.date | None,
) -> tuple[dt.date, dt.date, str]:
    """Границы отчёта и пояс школы. По умолчанию — текущий месяц."""
    tz_name = repo.tenant_timezone(cur, who.tenant_id)
    since, until = money.resolve_period(from_, to, tz_name)
    return since, until, tz_name


@app.get(f"{API}/payroll", response_model=schemas.PayrollSheet, tags=["Деньги"])
def payroll_sheet(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from", description="Начало периода")] = None,
    to: Annotated[dt.date | None, Query(description="Конец периода, включительно")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> dict[str, Any]:
    """Ведомость по преподавателям за период.

    Если период закрыт, суммы берутся по штампу `period_id` и больше
    не меняются. Открытый период собирается по датам и тянет в себя правки
    за уже закрытые месяцы — это и есть «корректировка в следующем».

    Только владелец. §2 разрешает администратору видеть всё, КРОМЕ ставок
    ЗП других сотрудников, а ведомость целиком из них и состоит; преподаватель
    смотрит свою расшифровку через `/payroll/teachers/{его staff_id}`.
    """
    authz.require_owner(who, "Ведомость")
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.sheet(cur, since, until, branch_id, tz_name)


# Объявлено ДО /payroll/teachers/{staff_id}: иначе FastAPI сопоставил бы
# «periods» с параметром пути и список периодов вечно отвечал бы 404.
@app.get(
    f"{API}/payroll/periods", response_model=list[schemas.PayrollPeriod], tags=["Деньги"]
)
def payroll_periods(
    who: CallerDep,
    limit: Annotated[int, Query(ge=1, le=120)] = 24,
) -> list[dict[str, Any]]:
    """Закрытые периоды с итогами. Итоги — те же деньги людей, что и ведомость."""
    authz.require_owner(who, "Список периодов")
    with tenant_tx(who.tenant_id) as cur:
        tz_name = repo.tenant_timezone(cur, who.tenant_id)
        return money.periods(cur, tz_name, limit)


@app.post(
    f"{API}/payroll/periods",
    response_model=schemas.PeriodClosed,
    status_code=201,
    tags=["Деньги"],
)
def close_payroll_period(
    who: CallerDep, body: schemas.ClosePeriodRequest
) -> dict[str, Any]:
    """Закрыть период начисления.

    Открытого периода в системе не существует: строка в `payroll_period`
    появляется ровно тогда, когда деньги посчитаны и отданы. Закрытие
    штампует все непроштампованные начисления с датой до конца периода;
    всё, что появится позже, штампа не получит и уйдёт в следующую
    ведомость (spec.md §6.2).

    Закрывает период владелец: это подпись под тем, что деньги посчитаны
    и отданы, и открыть период обратно нельзя ничем.
    """
    authz.require_owner(who, "Закрытие периода")
    with tenant_tx(who.tenant_id) as cur:
        tz_name = repo.tenant_timezone(cur, who.tenant_id)
        return money.close_period(
            cur, who.tenant_id, who.user_id, body.from_, body.to, tz_name
        )


@app.get(
    f"{API}/payroll/teachers/{{staff_id}}",
    response_model=schemas.PayrollDetail,
    tags=["Деньги"],
)
def payroll_teacher(
    who: CallerDep,
    staff_id: str,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> dict[str, Any]:
    """Расшифровка ведомости: за что именно начислено, занятие за занятием.

    Свою расшифровку преподаватель видит — §2 прямо это разрешает: «видит
    своё расписание, своих учеников, свою ЗП». Чужую не видит никто, кроме
    владельца, включая администратора ресепшена.
    """
    if not authz.may_see_teacher_rate(who, staff_id):
        raise authz.forbidden(
            "Чужую ведомость видит только владелец школы. Свою — сам преподаватель.",
            "owner_only",
        )
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        card = money.teacher_sheet(cur, staff_id, since, until, branch_id, tz_name)
        if card is None:
            raise not_found("Преподаватель не найден")
        return card


@app.get(f"{API}/reports/revenue", response_model=schemas.Revenue, tags=["Деньги"])
def revenue_report(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> dict[str, Any]:
    """Выручка по филиалам, направлениям и месяцам.

    Считается по поступившим деньгам, а не по проданным абонементам: продажа
    с долгом — обычное дело, и выручка, показывающая невыплаченное, отвечает
    не на тот вопрос.

    Отчёты — владельцу и администратору: §2 закрывает от администратора
    ставки ЗП, а не кассу школы, которую он же и принимает.
    """
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.revenue(cur, since, until, branch_id, tz_name)


@app.get(f"{API}/reports/rooms", response_model=schemas.RoomsReport, tags=["Деньги"])
def rooms_report(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> dict[str, Any]:
    """Загрузка кабинетов в процентах — ответ на «пора ли открывать филиал»."""
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.rooms(cur, since, until, branch_id, tz_name)


@app.get(f"{API}/reports/churn", response_model=schemas.ChurnReport, tags=["Деньги"])
def churn_report(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
    grace_days: Annotated[
        int, Query(ge=0, le=90, description="Сколько дней ждём продления")
    ] = money.DEFAULT_GRACE_DAYS,
    limit: Annotated[int, Query(ge=0, le=200)] = money.DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Сколько человек ушло из школы и у кого из преподавателей это чаще.

    Считается по людям, а не по абонементам: ученик с двумя направлениями —
    один человек, пауза между абонементами — не уход, а вердикт не выносится
    раньше, чем истекла отсрочка продления (issue #25).
    """
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.churn(cur, since, until, grace_days, limit, tz_name)


@app.get(f"{API}/reports/debts", response_model=schemas.DebtsReport, tags=["Деньги"])
def debts_report(
    who: CallerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = money.DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Долги: у кого и сколько. Периода нет — долг всегда на сейчас."""
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return money.debts(cur, limit)


@app.get(f"{API}/reports/summary", response_model=schemas.MoneySummary, tags=["Деньги"])
def money_summary(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> dict[str, Any]:
    """Шапка экрана: выручка, загрузка, отток и блок «Требует внимания».

    Одним запросом, потому что это четыре числа наверху экрана, а каждый
    отчёт под ними тяжелее, чем нужно ради одной цифры.
    """
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        head = money.summary(cur, since, until, branch_id, tz_name)
        if not who.is_owner:
            # Фонд оплаты труда — это те же деньги людей, что и ведомость,
            # просто одной строкой. Число занятий и признак закрытия периода
            # остаются: они говорят о работе школы, а не о чужой зарплате.
            head["payroll"] = dict(head["payroll"], total=None)
        return head


# ---------------------------------------------------------------------------
# Кабинет родителя — этап 5, issue #5
#
# Отдельные ресурсы `/me/*`, а НЕ урезанная карточка ученика. Урезание —
# операция вычитания, и она ошибается молча: стоит кому-нибудь добавить поле
# в общую карточку, и оно уедет родителю, если про него забыли. В карточке
# лежат риск оттока, долг семьи, внутренние заметки и ставка преподавателя —
# ни одно из этого показывать нельзя, а половина ещё и обидна.
#
# Отдельный ресурс собирается сложением: в него попадает только то, что
# положили осознанно, и ошибка выглядит как отсутствующее поле, а не утечка.
#
# Состав детей во всех пяти ресурсах берётся ИЗ СЕССИИ. Идентификатор в пути
# только сверяется со списком; чужой — 404, а не 403.
# ---------------------------------------------------------------------------


@app.get(f"{API}/me/children", response_model=list[schemas.Child], tags=["Кабинет"])
def my_children(who: CallerDep) -> list[dict[str, Any]]:
    """Свои дети с остатком абонемента и ближайшим занятием.

    Главный экран кабинета: «когда вести» и «сколько осталось» — оба вопроса
    в одном ответе, без второго запроса на каждого ребёнка.
    """
    authz.require_family(who)
    with tenant_tx(who.tenant_id) as cur:
        return family.children(cur, authz.family_student_ids(cur, who))


@app.get(f"{API}/me/schedule", response_model=schemas.FamilySchedule, tags=["Кабинет"])
def my_schedule(
    who: CallerDep,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: Annotated[dt.date | None, Query(description="Включительно")] = None,
) -> dict[str, Any]:
    """Расписание всех своих детей сразу.

    Не «расписание ребёнка»: родителю нужно знать, когда вести кого, а листать
    детей по одному он не будет. Без `from`/`to` — неделя вперёд от сегодня.
    """
    authz.require_family(who)
    with tenant_tx(who.tenant_id) as cur:
        return family.schedule(
            cur,
            authz.family_student_ids(cur, who),
            repo.tenant_timezone(cur, who.tenant_id),
            from_,
            to,
        )


@app.get(
    f"{API}/me/children/{{student_id}}", response_model=schemas.ChildCard, tags=["Кабинет"]
)
def my_child(who: CallerDep, student_id: str) -> dict[str, Any]:
    """История одного ребёнка: за что списано, что задали, что играем.

    Чужой ребёнок отвечает 404: идентификатор в пути сверяется со списком
    из сессии, и «есть, но не ваш» подтверждало бы, что такой ученик в школе
    существует.
    """
    authz.require_family(who)
    with tenant_tx(who.tenant_id) as cur:
        if student_id not in authz.family_student_ids(cur, who):
            raise not_found("Ученик не найден")
        card = family.child(cur, student_id)
        if card is None:
            raise not_found("Ученик не найден")
        return card


@app.post(
    f"{API}/me/lessons/{{lesson_id}}/reschedule-request",
    response_model=schemas.RescheduleCreated,
    status_code=201,
    tags=["Кабинет"],
)
def request_reschedule(
    who: CallerDep, lesson_id: str, body: schemas.RescheduleRequest
) -> dict[str, Any]:
    """Заявка на перенос — заявка, а не перенос.

    Родитель не двигает расписание сам: слот может быть занят, преподаватель
    может быть занят, и решение принимает администратор.
    """
    authz.require_family(who)
    with tenant_tx(who.tenant_id) as cur:
        return family.request_reschedule(
            cur,
            who.tenant_id,
            who.user_id,
            who.person_id,
            authz.family_student_ids(cur, who),
            lesson_id,
            body.reason,
            list(body.preferred),
        )


@app.post(
    f"{API}/me/children/{{student_id}}/renew-request",
    response_model=schemas.RenewCreated,
    status_code=201,
    tags=["Кабинет"],
)
def request_renew(
    who: CallerDep, student_id: str, body: schemas.RenewRequest
) -> dict[str, Any]:
    """Заявка на продление абонемента.

    Оплаты в кабинете нет: приём платежей через провайдера — отдельная
    интеграция, а обещать оплату, которой нет, хуже, чем её отсутствие.
    """
    authz.require_family(who)
    with tenant_tx(who.tenant_id) as cur:
        if student_id not in authz.family_student_ids(cur, who):
            raise not_found("Ученик не найден")
        return family.request_renew(
            cur, who.tenant_id, who.user_id, who.person_id, student_id, body.comment
        )


# ---------------------------------------------------------------------------
# Заявки из кабинета — сторона администратора
#
# Заявка, которую некому увидеть и нечем закрыть, ничем не отличается
# от несделанной: кабинет обещает родителю ответ, и обещание надо кому-то
# выполнять.
# ---------------------------------------------------------------------------


@app.get(f"{API}/requests", response_model=schemas.FamilyRequests, tags=["Кабинет"])
def family_requests(
    who: CallerDep,
    status: Annotated[
        str, Query(description="pending | accepted | declined | all")
    ] = "pending",
    kind: Annotated[str | None, Query(description="reschedule | renew")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Очередь заявок. По умолчанию — нерассмотренные, старые сверху:
    заявка, которую не заметили три дня, важнее пришедшей минуту назад."""
    authz.require_admin(who)
    if status not in ("pending", "accepted", "declined", "all"):
        raise ApiError(400, "bad_status", "status: pending, accepted, declined или all.")
    if kind is not None and kind not in ("reschedule", "renew"):
        raise ApiError(400, "bad_kind", "kind: reschedule или renew.")
    with tenant_tx(who.tenant_id) as cur:
        return family.list_requests(cur, None if status == "all" else status, kind, limit)


@app.patch(
    f"{API}/requests/{{request_id}}", response_model=schemas.FamilyRequest, tags=["Кабинет"]
)
def answer_family_request(
    who: CallerDep, request_id: str, body: schemas.RequestDecision
) -> dict[str, Any]:
    """Рассмотрение заявки: принять или отклонить с ответом родителю.

    Сам перенос занятия эта операция НЕ делает: редактирования расписания
    в системе пока нет вовсе (см. «Известные ограничения»). `moved_to`
    проставляется, когда занятие переставили руками, — чтобы в заявке было
    видно, чем всё кончилось.
    """
    authz.require_admin(who)
    with tenant_tx(who.tenant_id) as cur:
        return family.answer_request(
            cur, who.tenant_id, who.user_id, request_id, body.status, body.answer,
            body.moved_to,
        )


@app.get(f"{API}/health", tags=["Служебное"])
def health() -> dict[str, str]:
    with get_pool().connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


# ---------------------------------------------------------------------------


def _applied_effect(
    fact: dict[str, Any] | None, sub: Any
) -> dict[str, Any] | None:
    """Факт применённой отметки — то, что уже списалось, а не то, что списалось бы.

    Остаток «после» здесь — настоящий текущий остаток абонемента, а не сумма
    правил: если после этой отметки было ещё движение (продление, отработка),
    интерфейс обязан показать то же число, что и карточка ученика.
    """
    if fact is None:
        return None
    lessons_after = sub.lessons_balance if sub is not None else None
    makeups_after = sub.makeups_balance if sub is not None else None
    amount = int(fact["teacher_amount"] or 0)
    return {
        "mark": fact["mark"],
        "attendance_id": str(fact["attendance_id"]),
        "lessons_delta": int(fact["lessons_delta"]),
        "makeups_delta": int(fact["makeups_delta"]),
        "lessons_after": lessons_after,
        "makeups_after": makeups_after,
        "teacher_amount": amount,
        "summary": applied_summary(
            fact["mark"],
            int(fact["lessons_delta"]),
            int(fact["makeups_delta"]),
            lessons_after,
            amount,
        ),
    }


def _uuid_or_400(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(400, "bad_request", f"Поле {field} должно быть UUID.") from None


def _minutes_between(start: dt.time, end: dt.time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
