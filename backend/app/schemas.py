"""Модели ответов ровно по контракту.

Пишутся отдельно от SQL намеренно: контракт — это граница с фронтендом,
и она не должна меняться от того, что кто-то добавил колонку в запрос.
Заодно эти модели превращаются в OpenAPI, по которому фронтенд сверяет типы.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Mark = Literal["came", "late", "no_show", "cancelled_early", "cancelled_late", "cancelled_teacher"]
LessonKind = Literal["regular", "trial", "makeup", "extra"]
LessonStatus = Literal["planned", "held", "cancelled"]


class Branch(BaseModel):
    id: str
    name: str
    timezone: str
    opens_at: str = Field(examples=["10:00"])
    closes_at: str = Field(examples=["21:00"])


class BranchBrief(BaseModel):
    id: str
    name: str
    opens_at: str
    closes_at: str


class RoomBrief(BaseModel):
    id: str
    name: str


class TeacherBrief(BaseModel):
    id: str
    name: str
    disciplines: list[str] = []
    color: str | None = None


# Ссылка на объект по имени. Объявлена здесь, а не рядом с заявками, потому
# что ею пользуются и справочники, и карточка заявки: одна форма ссылки
# на всю систему избавляет фронтенд от разбора двух похожих.
class NamedRef(BaseModel):
    id: str
    name: str | None = None


class DisciplineRef(NamedRef):
    # Минимальный возраст едет вместе с направлением везде, где его
    # показывают рядом с возрастом ученика. Порог у каждой школы свой
    # (`discipline.min_age`), и таблица порогов на клиенте сломалась бы
    # на первой же школе, которая берёт на барабаны с четырёх лет.
    min_age: int | None = None


# ---------------------------------------------------------------------------
# Справочники для форм интерфейса
#
# До них интерфейсу было негде взять списки: преподаватели и кабинеты
# выковыривались из расписания дня, то есть из тех, у кого сегодня есть
# занятия, а направление в форме заявки выбрать было нельзя вовсе.
# ---------------------------------------------------------------------------


class Discipline(DisciplineRef):
    name: str
    # Требования к кабинету — для диалога назначения пробного: барабаны
    # требуют установки, и кабинет без неё можно погасить до отправки формы.
    room_reqs: dict[str, Any] = {}


class Teacher(BaseModel):
    id: str
    name: str
    phone: str | None = None
    color: str | None = None
    # Объектами, а не строками: диалогу нужно отфильтровать преподавателей
    # по выбранному направлению, а по названию это делается сравнением
    # строк — то есть ломается на первом же переименовании.
    disciplines: list[NamedRef] = []
    branches: list[NamedRef] = []


class Room(BaseModel):
    id: str
    name: str
    branch: NamedRef
    capacity: int
    features: dict[str, Any] = {}


class Conflict(BaseModel):
    kind: Literal["room", "teacher"]
    with_lesson_id: str
    message: str


class ScheduleLesson(BaseModel):
    id: str
    starts_at: str
    ends_at: str
    duration_min: int
    kind: LessonKind
    status: LessonStatus
    title: str | None = None
    student_id: str | None = None
    room: RoomBrief
    attendance_mark: Mark | None = None
    conflicts: list[Conflict] = []


class Track(BaseModel):
    teacher: TeacherBrief
    lessons: list[ScheduleLesson]


class ScheduleSummary(BaseModel):
    lessons: int
    trials: int
    conflicts: int
    room_utilization_pct: int


class Schedule(BaseModel):
    date: str
    branch: BranchBrief
    tracks: list[Track]
    summary: ScheduleSummary


class SubscriptionBrief(BaseModel):
    id: str
    lessons_total: int
    lessons_balance: int
    makeups_balance: int
    valid_until: str
    status: str


class MarkEffectOut(BaseModel):
    lessons_delta: int
    makeups_delta: int
    teacher_amount: int
    lessons_after: int
    # Сверх контракта: остаток отработок после отметки — интерфейсу нужно
    # показать «станет 2 отработки» так же, как «останется 4 занятия».
    makeups_after: int
    summary: str
    # Сверх контракта: почему отметку нельзя применить (например, нулевой
    # остаток). Позволяет погасить кнопку до нажатия, а не показывать 422.
    blocked_reason: str | None = None


class AppliedEffect(BaseModel):
    """Что действующая отметка уже сделала — из журнала, а не пересчётом правил.

    Сверх контракта. Без этого поля интерфейс, показывая блок «Отмечено»,
    вынужден был брать числа из `mark_effects`, то есть из предпросмотра,
    посчитанного от УЖЕ уменьшенного остатка: дельта совпадала, а `lessons_after`
    отставал ровно на списанное занятие (issue #22).
    """

    mark: Mark
    attendance_id: str
    lessons_delta: int
    makeups_delta: int
    # Настоящий текущий остаток абонемента, а не «баланс плюс дельта»:
    # если после отметки было ещё движение, карточка занятия и карточка
    # ученика обязаны показывать одно число. У ученика без абонемента — null.
    lessons_after: int | None = None
    makeups_after: int | None = None
    teacher_amount: int
    summary: str


class Participant(BaseModel):
    student_id: str
    name: str
    attendance: Mark | None = None
    attendance_id: str | None = None
    subscription: SubscriptionBrief | None = None
    # Предпросмотр «что будет, если нажать». У уже отмеченного участника
    # считается от остатка ДО списания — переотметка начинается с отмены
    # прежней отметки, и она вернёт занятие назад.
    mark_effects: dict[str, MarkEffectOut]
    applied_effect: AppliedEffect | None = None


class TeacherCard(BaseModel):
    id: str
    name: str
    # `null` — не «ставки нет», а «вам её не видно». По §2 чужую ставку
    # знает только владелец: администратор ресепшена ведёт расписание,
    # но сколько получает преподаватель — не его дело. Ноль здесь означал бы
    # «работает бесплатно» и однажды попал бы в разговор.
    rate: int | None = None


class LessonNote(BaseModel):
    body: str
    homework: str | None = None
    tags: list[str] = []


class LessonCard(BaseModel):
    id: str
    starts_at: str
    ends_at: str
    duration_min: int
    kind: LessonKind
    status: LessonStatus
    title: str | None = None
    room: RoomBrief
    teacher: TeacherCard
    participants: list[Participant]
    note: LessonNote | None = None


# Дата операции — общий параметр четырёх пишущих операций (ADR-001, #15).
# Описание одно на все: расхождение в текстах здесь превратилось бы
# в расхождение в понимании, а правило одно.
EFFECTIVE_DATE = Field(
    default=None,
    description=(
        "Дата, которой считается операция. По умолчанию — сегодня в поясе "
        "филиала. Задним числом не глубже правила школы backdating_days "
        "(30 дней по умолчанию, 0 — только сегодня) и не внутрь закрытого "
        "зарплатного периода."
    ),
)


class AttendanceRequest(BaseModel):
    student_id: str
    mark: Mark
    effective_date: date | None = EFFECTIVE_DATE


class Applied(BaseModel):
    lessons_delta: int
    lessons_after: int
    makeups_delta: int
    makeups_after: int
    teacher_amount: int
    teacher_id: str
    subscription_id: str | None = None


class Alert(BaseModel):
    kind: str
    message: str


class AttendanceApplied(BaseModel):
    attendance_id: str
    mark: Mark
    applied: Applied
    lesson_status: LessonStatus
    # Сверх контракта: какой датой записана операция и внесена ли она задним
    # числом. Интерфейс обязан показать это там же, где отметку поставили, —
    # иначе ошибку в дате никто не заметит до разбора с родителем.
    effective_date: str | None = None
    backdated: bool = False
    alerts: list[Alert] = []


class Reverted(BaseModel):
    lessons_delta: int
    lessons_after: int | None = None
    makeups_delta: int
    makeups_after: int | None = None
    teacher_amount: int
    teacher_id: str
    subscription_id: str | None = None


class AttendanceRevoked(BaseModel):
    attendance_id: str
    mark: Mark
    revoked_at: str
    effective_date: str | None = None
    backdated: bool = False
    reverted: Reverted
    lesson_status: LessonStatus


# ---------------------------------------------------------------------------
# Этап 2: ученики, тарифы, абонементы, заморозки
# ---------------------------------------------------------------------------

PlanFormat = Literal["individual", "pair", "group", "trial"]
PaymentMethod = Literal["kaspi", "card", "cash", "transfer", "other"]


class Payer(BaseModel):
    name: str
    phone: str | None = None


class SubscriptionInList(BaseModel):
    lessons_balance: int
    lessons_total: int
    valid_until: str
    status: str


class StudentInList(BaseModel):
    id: str
    name: str
    age: int | None = None
    discipline: str | None = None
    teacher: str | None = None
    branch: str | None = None
    subscription: SubscriptionInList | None = None
    payer: Payer | None = None


class FamilyMember(BaseModel):
    student_id: str
    name: str
    age: int | None = None
    discipline: str | None = None
    lessons_balance: int


class Family(BaseModel):
    id: str
    payer: Payer | None = None
    discount_pct: float
    members: list[FamilyMember]
    paid_this_month: int
    debt: int


class Hold(BaseModel):
    id: str
    # from — ключевое слово Python, поэтому поле объявляется через псевдоним:
    # наружу уходит имя из контракта, внутри живёт from_.
    from_: str = Field(alias="from")
    to: str
    days: int
    reason: str | None = None

    model_config = {"populate_by_name": True}


class SubscriptionCard(BaseModel):
    id: str
    plan_name: str
    lessons_total: int
    lessons_balance: int
    makeups_balance: int
    price: int
    lesson_price: int
    valid_from: str
    valid_until: str
    status: str
    rules: dict[str, Any]
    holds: list[Hold] = []
    freeze_days_used: int
    freeze_days_left: int


class Makeup(BaseModel):
    id: str
    granted_for: str | None = None
    expires_on: str
    days_left: int
    used_at: str | None = None


class LedgerRow(BaseModel):
    id: int
    date: str
    kind: str
    title: str
    teacher: str | None = None
    lessons_delta: int
    makeups_delta: int
    amount: int | None = None
    # Сверх контракта: исходная причина записи как её сохранил журнал.
    # Нужна для спорных случаев, когда человеческого заголовка мало.
    reason: str | None = None


class StudentNote(BaseModel):
    date: str
    author: str | None = None
    body: str
    homework: str | None = None
    tags: list[str] = []


class ChurnRisk(BaseModel):
    level: Literal["low", "medium", "high"]
    score: int
    # Только то, что повышает риск. Смягчающие факты живут отдельно: в списке
    # причин риска «занимается 6 месяцев» читалось бы как довод за отток.
    reasons: list[str]
    # Сверх контракта: факты, снизившие оценку. Интерфейс показывает их рядом,
    # как в прототипе, но не путает с причинами.
    mitigations: list[str] = []


class StudentCard(BaseModel):
    id: str
    name: str
    age: int | None = None
    discipline: str | None = None
    teacher: str | None = None
    branch: str | None = None
    started_on: str
    status: str
    family: Family | None = None
    subscription: SubscriptionCard | None = None
    makeups: list[Makeup] = []
    ledger: list[LedgerRow] = []
    notes: list[StudentNote] = []
    churn_risk: ChurnRisk


class Plan(BaseModel):
    id: str
    name: str
    discipline: str | None = None
    format: PlanFormat
    duration_min: int
    lessons_count: int
    valid_days: int
    price: int


class PaymentIn(BaseModel):
    amount: int = Field(gt=0, description="Сумма в целых тенге")
    method: PaymentMethod


class SellRequest(BaseModel):
    plan_id: str
    starts_on: date | None = Field(
        default=None,
        description="Первый день действия; по умолчанию — дата операции",
    )
    effective_date: date | None = EFFECTIVE_DATE
    discount_pct: float | None = Field(default=None, ge=0, le=100)
    promo_code: str | None = None
    payment: PaymentIn | None = None
    carry_over: bool = False


class SoldSubscription(BaseModel):
    subscription_id: str
    lessons_total: int
    lessons_balance: int
    valid_from: str
    valid_until: str
    price: int
    discount_pct: float
    charged: int
    carried_over: int
    # Сверх контракта: почему перенос не состоялся. Контракт требует показать
    # это явно, а голый ноль ничего не объясняет.
    carry_over_note: str | None = None
    payment_id: str | None = None
    debt: int
    effective_date: str | None = None
    backdated: bool = False


class HoldRequest(BaseModel):
    from_: date = Field(alias="from")
    to: date
    reason: str | None = None
    effective_date: date | None = EFFECTIVE_DATE

    model_config = {"populate_by_name": True}


class HoldCreated(BaseModel):
    hold_id: str
    days: int
    valid_until_before: str
    valid_until_after: str
    lessons_cancelled: int
    freeze_days_left: int
    # Сверх контракта: та же информация одной фразой для тоста в интерфейсе.
    message: str
    # Сверх контракта и необязательно: статус абонемента после операции.
    # У заморозки «с сегодня» это `frozen`, у назначенной на будущее — прежний.
    # Без него интерфейсу пришлось бы перечитывать карточку, чтобы понять,
    # изменилось ли вообще что-нибудь видимое администратору.
    status: str | None = None
    effective_date: str | None = None
    backdated: bool = False


class HoldReleased(BaseModel):
    hold_id: str
    days_returned: int
    valid_until_before: str
    valid_until_after: str
    lessons_cancelled: int
    lessons_restored: int
    freeze_days_left: int
    message: str
    status: str | None = None
    effective_date: str | None = None
    backdated: bool = False


# ---------------------------------------------------------------------------
# Этап 3: воронка заявок
#
# Значения стадий, источников и причин отказа взяты дословно из CHECK-ограничений
# db/004_sales.sql: Literal здесь не украшение, а та же проверка на входе,
# что и в базе, — только с понятным текстом ошибки вместо 23514.
# ---------------------------------------------------------------------------

Stage = Literal["new", "contacting", "trial_booked", "trial_held", "won", "lost"]
LeadSource = Literal[
    "telegram_bot", "site_form", "whatsapp", "instagram",
    "referral", "walk_in", "phone", "other",
]
LostReason = Literal[
    "price", "location", "schedule", "competitor", "no_answer", "not_ready", "other",
]


class TrialBrief(BaseModel):
    lesson_id: str
    starts_at: str
    teacher: str | None = None
    room: str | None = None
    status: LessonStatus
    conflicts: list[Conflict] = []


class LeadCard(BaseModel):
    id: str
    name: str
    phone: str | None = None
    student_name: str | None = None
    student_age: int | None = None
    discipline: str | None = None
    source: LeadSource
    stage: Stage
    created_at: str
    waiting_for: str
    next_action_at: str | None = None
    contact_attempts: int
    assigned_to: NamedRef | None = None
    trial: TrialBrief | None = None
    flags: list[str] = []


class LeadFull(LeadCard):
    # Направление и филиал в карточке — объекты, а в списке хватает названия:
    # диалогу назначения пробного нужен идентификатор, доске — нет.
    # У направления едет и min_age: контракт требует предупредить, если
    # ребёнок младше минимального возраста, а без порога интерфейсу нечем
    # ни сравнить, ни объяснить предупреждение («берут с 7, а ему 5»).
    discipline: DisciplineRef | None = None
    branch: NamedRef | None = None
    lost_reason: LostReason | None = None
    utm: dict[str, Any] = {}
    promo_code: str | None = None
    external_id: str | None = None
    history: list[dict[str, Any]] = []
    converted: dict[str, str | None] = {}
    # Сверх контракта: возраст ниже минимального для направления. Не ошибка,
    # а предупреждение — решает администратор.
    age_warning: str | None = None


class BoardColumn(BaseModel):
    stage: Stage
    title: str
    # Сколько заявок в стадии всего, а не сколько приехало в этом ответе:
    # колонка «Отказ» отдаётся страницей, а счётчик обязан остаться честным.
    count: int
    leads: list[LeadCard]
    # Оба поля сверх контракта и необязательные — старый фронтенд их просто
    # не заметит. next_offset подставляется в ?offset= следующего запроса.
    has_more: bool = False
    next_offset: int | None = None


class BoardSummary(BaseModel):
    total: int
    overdue: int
    conversion_trial_to_won_pct: int
    avg_days_to_won: float | None = None


class Board(BaseModel):
    columns: list[BoardColumn]
    summary: BoardSummary


class LeadCreate(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None
    student_name: str | None = None
    student_age: int | None = Field(default=None, ge=3, le=99)
    discipline_id: str | None = None
    branch_id: str | None = None
    source: LeadSource = "phone"
    promo_code: str | None = None
    comment: str | None = None


class LeadPatch(BaseModel):
    """Все поля необязательны — отличить «не передали» от «передали null»
    позволяет exclude_unset при разборе: сбросить напоминание в null нужно
    уметь так же, как и выставить его."""

    stage: Stage | None = None
    assigned_to: str | None = None
    next_action_at: datetime | None = None
    contact_attempts: int | None = Field(default=None, ge=0)
    lost_reason: LostReason | None = None


class TrialRequest(BaseModel):
    teacher_id: str
    room_id: str
    starts_at: datetime
    duration_min: int = Field(default=45, ge=15, le=180)
    # Цена пробного принимается, но в схеме ей места нет — см. README.
    price: int = Field(default=0, ge=0)
    overbook_ack: bool = False


class TrialBooked(BaseModel):
    lesson_id: str
    stage: Stage
    starts_at: str
    teacher: str | None = None
    room: str | None = None
    notification_queued: bool


class PayerIn(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    phone: str | None = None


class StudentIn(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    birth_date: date | None = None
    discipline_id: str | None = None
    branch_id: str | None = None
    main_teacher_id: str | None = None


class ConvertRequest(BaseModel):
    # Плательщика может не быть: взрослый ученик платит за себя,
    # и семья создаётся из одного человека.
    payer: PayerIn | None = None
    student: StudentIn
    # Абонемент необязателен: ученика заводят сейчас, продают позже.
    # Заявка всё равно переходит в won — ученик-то появился.
    subscription: SellRequest | None = None


class Converted(BaseModel):
    student_id: str
    person_id: str
    family_id: str
    subscription_id: str | None = None
    stage: Stage


class WebhookLead(BaseModel):
    external_id: str | None = None
    name: str = Field(min_length=1)
    phone: str | None = None
    student_name: str | None = None
    student_age: int | None = Field(default=None, ge=3, le=99)
    # Направление приходит строкой от бота и сопоставляется по названию.
    # Не нашли — заявку всё равно принимаем: терять лид из-за опечатки нельзя.
    discipline: str | None = None
    branch_id: str | None = None
    source: LeadSource = "other"
    utm: dict[str, Any] = {}
    promo_code: str | None = None
    comment: str | None = None


class FunnelStage(BaseModel):
    stage: Stage
    title: str
    entered: int
    moved_on: int
    conversion_pct: int


class FunnelSource(BaseModel):
    source: LeadSource
    leads: int
    trials: int
    won: int
    conversion_pct: int
    avg_days_to_won: float | None = None


class FunnelLostReason(BaseModel):
    reason: LostReason
    count: int


class Funnel(BaseModel):
    period: dict[str, str]
    stages: list[FunnelStage]
    sources: list[FunnelSource]
    lost_reasons: list[FunnelLostReason]
    avg_days_to_won: float | None = None


# ---------------------------------------------------------------------------
# Этап 4 — «Деньги и ЗП»
#
# Деньги везде целым числом тенге, даты — ISO в поясе филиала: те же правила,
# что в контрактах этапов 1–3. Периоды в запросе и в ответе задаются парой
# `from`/`to` включительно — «по 31 июля» человек читает как «июль целиком».
# ---------------------------------------------------------------------------


class Period(BaseModel):
    from_: str = Field(alias="from")
    to: str

    model_config = {"populate_by_name": True}


class PayrollTeacher(BaseModel):
    id: str
    name: str
    color: str | None = None


class PayrollRow(BaseModel):
    teacher: PayrollTeacher
    # Занятий и начислений — разные числа: у группы из четырёх человек
    # начисление на каждого участника.
    lessons: int
    entries: int
    # Самая частая ставка. У преподавателя их несколько (55 и 85 минут,
    # индивидуальное и группа), поэтому рядом стоит честный флаг.
    rate: int
    rate_varies: bool
    no_shows: int
    accrued: int
    corrections: int
    bonuses: int
    total: int
    # Сколько из суммы приехало из уже закрытых месяцев корректировкой.
    carried_over: int
    carried_over_entries: int


class PayrollTotals(BaseModel):
    teachers: int
    lessons: int
    entries: int
    no_shows: int
    accrued: int
    corrections: int
    bonuses: int
    total: int
    carried_over: int
    carried_over_entries: int


class PayrollSheet(BaseModel):
    period: Period
    closed: bool
    period_id: str | None = None
    closed_at: str | None = None
    closed_by: str | None = None
    branch_id: str | None = None
    teachers: list[PayrollRow]
    totals: PayrollTotals
    note: str


class PayrollEntry(BaseModel):
    id: int
    date: str
    starts_at: str | None = None
    kind: Literal["lesson", "bonus", "correction", "deduction"]
    lesson_id: str | None = None
    student: str | None = None
    discipline: str | None = None
    branch: str | None = None
    duration_min: int | None = None
    mark: Mark | None = None
    amount: int
    # Снимок расчёта, сохранённый при отметке. Через полгода объяснить сумму
    # больше нечем: ставки к тому времени поменяются.
    calc: dict[str, Any] = {}
    carried_over: bool = False


class PayrollDetail(BaseModel):
    teacher: PayrollTeacher
    period: Period
    closed: bool
    period_id: str | None = None
    closed_at: str | None = None
    closed_by: str | None = None
    totals: PayrollRow
    entries: list[PayrollEntry]


class PayrollPeriod(BaseModel):
    id: str
    period: Period
    closed: bool
    closed_at: str | None = None
    closed_by: str | None = None
    teachers: int
    entries: int
    total: int


class ClosePeriodRequest(BaseModel):
    from_: date = Field(alias="from")
    to: date

    model_config = {"populate_by_name": True}


class PeriodClosed(BaseModel):
    id: str
    period: Period
    closed: bool
    closed_at: str
    entries: int
    teachers: int
    total: int
    message: str


class RevenueSlice(BaseModel):
    id: str | None = None
    name: str
    amount: int
    payments: int
    share_pct: int


class RevenueMonth(BaseModel):
    month: str = Field(examples=["2026-08"])
    amount: int
    payments: int


class RevenueMethod(BaseModel):
    method: Literal["kaspi", "card", "cash", "transfer", "other"]
    amount: int
    payments: int
    share_pct: int


class Revenue(BaseModel):
    period: Period
    branch_id: str | None = None
    total: int
    payments: int
    by_branch: list[RevenueSlice]
    by_discipline: list[RevenueSlice]
    by_month: list[RevenueMonth]
    by_method: list[RevenueMethod]


class RoomLoad(BaseModel):
    room_id: str
    room: str
    branch_id: str
    branch: str
    lessons: int
    busy_minutes: int
    capacity_minutes: int
    utilization_pct: int


class BranchLoad(BaseModel):
    branch_id: str
    branch: str
    rooms: int
    open_days: int
    open_minutes_per_day: int
    busy_minutes: int
    capacity_minutes: int
    utilization_pct: int


class RoomsReport(BaseModel):
    period: Period
    branch_id: str | None = None
    utilization_pct: int
    busy_minutes: int
    capacity_minutes: int
    capacity_note: str
    branches: list[BranchLoad]
    rooms: list[RoomLoad]


class ChurnTeacher(BaseModel):
    teacher_id: str | None = None
    name: str
    # Сколько человек у преподавателя учились в периоде. Без этого числа
    # процент нечитаем: 33% при трёх учениках — это один человек.
    students: int
    ended: int
    churned: int
    churn_pct: int


class ChurnStudent(BaseModel):
    student_id: str
    name: str
    discipline: str | None = None
    branch: str | None = None
    teacher: str | None = None
    teacher_id: str | None = None
    ended_on: str
    # Когда администратор перевёл ученика в архив. Пусто — значит уход
    # виден только по абонементам, и карточку стоит проверить.
    archived_on: str | None = None
    last_lesson_on: str | None = None


class ChurnReport(BaseModel):
    """Отток по людям (issue #25).

    Числа здесь двух родов и намеренно не смешаны. `students_total`,
    `retained`, `frozen`, `pending`, `churned` — про людей: ими отвечают
    на «теряю ли я клиентов». `ended` и `renewed` — про документы: сколько
    абонементов закончилось и сколько продлено. `archived` — вторая,
    независимая оценка ухода, по `student.archived_at`.
    """

    period: Period
    grace_days: int
    # Дата, на которую вынесен вердикт. Не конец периода: об абонементе,
    # который кончится послезавтра, сказать нечего.
    as_of: str
    students_total: int
    retained: int
    frozen: int
    pending: int
    churned: int
    churn_pct: int
    archived: int
    archived_pct: int
    ended: int
    renewed: int
    by_teacher: list[ChurnTeacher]
    students: list[ChurnStudent]
    note: str


class Debtor(BaseModel):
    family_id: str
    payer: str | None = None
    phone: str | None = None
    students: list[str] = []
    charged: int
    paid: int
    debt: int
    since_on: str | None = None
    last_paid_on: str | None = None


class DebtsReport(BaseModel):
    families: int
    total: int
    items: list[Debtor]
    note: str


class MoneySummary(BaseModel):
    period: Period
    branch_id: str | None = None
    revenue: dict[str, Any]
    rooms: dict[str, Any]
    churn: dict[str, Any]
    payroll: dict[str, Any]
    attention: dict[str, Any]


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------

Role = Literal["owner", "admin", "teacher", "guardian", "student"]


class CodeRequest(BaseModel):
    # Слаг школы обязателен: телефон уникален внутри школы, но не между ними,
    # и без названия школы вход по телефону означал бы «покажи все школы,
    # где есть этот номер» — то есть выдачу чужих данных ещё до входа.
    # Фронтенд знает слаг из поддомена, в котором открыт.
    tenant: str = Field(examples=["rockschool-demo"], min_length=1)
    login: str = Field(
        examples=["+7 701 555 24 18"],
        min_length=1,
        description="Телефон в любом формате или логин сотрудника",
    )


class CodeSent(BaseModel):
    # Никогда не «пользователь не найден»: ответ одинаков и для существующего
    # телефона, и для случайного. Иначе форма входа превращается в проверку,
    # ходит ли ребёнок в эту школу.
    sent: bool
    to: str = Field(examples=["+77015 ••• •• 18"], description="Замаскированный адрес")
    expires_in: int = Field(examples=[300], description="Секунд до протухания кода")
    message: str


class LoginRequest(BaseModel):
    tenant: str = Field(examples=["rockschool-demo"], min_length=1)
    login: str = Field(examples=["+7 701 555 24 18"], min_length=1)
    # Ровно одно из двух. Код — основной путь (родители не помнят паролей),
    # пароль оставлен сотрудникам: администратор входит по двадцать раз в день,
    # и SMS на каждый вход — это не безопасность, а счёт от оператора.
    code: str | None = Field(default=None, examples=["123456"])
    password: str | None = None


class Me(BaseModel):
    user_id: str
    name: str
    role: Role
    tenant: NamedRef
    person_id: str
    staff_id: str | None = None
    # Пустой список = без ограничения по филиалам (школа не заполнила
    # staff_branch), а не «доступа никуда нет».
    branch_ids: list[str] = []
    student_ids: list[str] = []


class LoggedIn(BaseModel):
    user: Me
    expires_at: str
    # Тот же токен, что уехал в куке. Отдаётся для не-браузерных клиентов
    # (curl, мобильное приложение, интеграционные тесты); фронтенду он
    # не нужен и хранить его в localStorage не надо — см. backend/README.md.
    token: str


class LoggedOut(BaseModel):
    ok: bool
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorBody
