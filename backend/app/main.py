"""HTTP-слой RockCRM.

Этап 1: расписание и отметка посещаемости.
Этап 2: поиск и карточка ученика, тарифы, продажа и продление абонемента,
заморозка и её снятие.
Этап 3: воронка заявок, пробный урок, конверсия в ученика, приём по вебхуку.
"""
from __future__ import annotations

import datetime as dt
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
from . import api_keys, billing, config, money, repository as repo, schemas
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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Заголовки тенанта и пользователя
#
# TODO: заглушка до появления входа. Настоящая авторизация придёт следующим
# этапом и будет доставать тенанта и пользователя из токена, но контур изоляции
# обязан работать уже сейчас — иначе он никогда не будет протестирован.
# ---------------------------------------------------------------------------


def _uuid_or_401(value: str | None, header: str) -> str:
    if not value:
        raise ApiError(401, "no_tenant", f"Заголовок {header} обязателен.")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ApiError(400, "bad_header", f"Заголовок {header} должен быть UUID.") from None


class Caller:
    def __init__(self, tenant_id: str, user_id: str) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id


def caller(
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Caller:
    return Caller(
        _uuid_or_401(x_tenant_id, "X-Tenant-Id"),
        _uuid_or_401(x_user_id, "X-User-Id"),
    )


CallerDep = Annotated[Caller, Depends(caller)]


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

# Текст один на все четыре операции: описание даты операции живёт
# в schemas.EFFECTIVE_DATE, а параметры пути и запроса берут его оттуда,
# чтобы в OpenAPI не оказалось двух разных объяснений одного правила.
EFFECTIVE_DATE_DOC = schemas.EFFECTIVE_DATE.description


@app.get(f"{API}/branches", response_model=list[schemas.Branch], tags=["Справочники"])
def branches(who: CallerDep) -> list[dict[str, Any]]:
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
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_teachers(cur, branch_id, discipline_id)


@app.get(f"{API}/rooms", response_model=list[schemas.Room], tags=["Справочники"])
def rooms(
    who: CallerDep,
    branch_id: Annotated[str | None, Query(description="UUID филиала")] = None,
) -> list[dict[str, Any]]:
    """Кабинеты с филиалом и характеристиками (`features`)."""
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_rooms(cur, branch_id)


@app.get(f"{API}/disciplines", response_model=list[schemas.Discipline], tags=["Справочники"])
def disciplines(who: CallerDep) -> list[dict[str, Any]]:
    """Направления с `min_age` и требованиями к кабинету.

    Без них форма заведения заявки не может предложить направление вовсе,
    и заявка уходит в воронку с пустым `discipline_id` — то есть выпадает
    из отчёта по направлениям.
    """
    with tenant_tx(who.tenant_id) as cur:
        return repo.list_disciplines(cur)


@app.get(f"{API}/schedule", response_model=schemas.Schedule, tags=["Расписание"])
def schedule(
    who: CallerDep,
    branch_id: Annotated[str, Query(description="UUID филиала")],
    date: Annotated[dt.date, Query(description="День расписания, YYYY-MM-DD")],
) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
        branch = repo.get_branch(cur, branch_id)
        if branch is None:
            raise not_found("Филиал не найден")
        tz = ZoneInfo(branch["timezone"])

        lessons = repo.lessons_of_day(cur, branch_id, date, tz)
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
    with tenant_tx(who.tenant_id) as cur:
        lesson = repo.get_lesson(cur, lesson_id)
        if lesson is None:
            raise not_found("Занятие не найдено")
        tz = ZoneInfo(lesson["branch_timezone"])
        rate_amount, rate_percent = repo.teacher_rate(cur, lesson)
        marked = repo.attendance_by_student(cur, lesson_id)
        applied = repo.applied_effects(cur, lesson_id)
        on = lesson["starts_at"].date()

        participants = []
        for row in repo.lesson_participants(cur, lesson):
            student_id = str(row["student_id"])
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
                "rate": int(rate_amount) if rate_amount is not None else 0,
            },
            "participants": participants,
            "note": repo.lesson_note(cur, lesson_id),
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

    `effective_date` в теле задаёт дату операции; без него — сегодня в поясе
    филиала, то есть прежнее поведение (ADR-001).
    """
    student_id = _uuid_or_400(body.student_id, "student_id")
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
        attendance_service.require_actor(cur, who.user_id)
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
    with tenant_tx(who.tenant_id) as cur:
        return students_service.search(cur, query, branch_id, limit)


@app.get(
    f"{API}/students/{{student_id}}", response_model=schemas.StudentCard, tags=["Ученики"]
)
def student_card(who: CallerDep, student_id: str) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
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
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
    with tenant_tx(who.tenant_id) as cur:
        card = leads_service.card(cur, lead_id)
        if card is None:
            raise not_found("Заявка не найдена")
        return card


@app.post(f"{API}/leads", response_model=schemas.LeadFull, status_code=201, tags=["Заявки"])
def create_lead(who: CallerDep, body: schemas.LeadCreate) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
        return leads_service.create_lead(cur, who.tenant_id, who.user_id, body)


@app.patch(f"{API}/leads/{{lead_id}}", response_model=schemas.LeadFull, tags=["Заявки"])
def patch_lead(who: CallerDep, lead_id: str, body: schemas.LeadPatch) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
        return leads_service.update_lead(cur, who.tenant_id, who.user_id, lead_id, body)


@app.post(
    f"{API}/leads/{{lead_id}}/trial",
    response_model=schemas.TrialBooked,
    status_code=201,
    tags=["Заявки"],
)
def book_trial(who: CallerDep, lead_id: str, body: schemas.TrialRequest) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
        return leads_service.book_trial(cur, who.tenant_id, who.user_id, lead_id, body)


@app.post(
    f"{API}/leads/{{lead_id}}/convert",
    response_model=schemas.Converted,
    status_code=201,
    tags=["Заявки"],
)
def convert_lead(who: CallerDep, lead_id: str, body: schemas.ConvertRequest) -> dict[str, Any]:
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
    who: Caller,
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
    """
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
    """
    with tenant_tx(who.tenant_id) as cur:
        attendance_service.require_actor(cur, who.user_id)
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
    """Расшифровка ведомости: за что именно начислено, занятие за занятием."""
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
    """
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
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.churn(cur, since, until, grace_days, limit, tz_name)


@app.get(f"{API}/reports/debts", response_model=schemas.DebtsReport, tags=["Деньги"])
def debts_report(
    who: CallerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = money.DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Долги: у кого и сколько. Периода нет — долг всегда на сейчас."""
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
    with tenant_tx(who.tenant_id) as cur:
        since, until, tz_name = _period(who, cur, from_, to)
        return money.summary(cur, since, until, branch_id, tz_name)


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
