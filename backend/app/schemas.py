"""Модели ответов ровно по контракту.

Пишутся отдельно от SQL намеренно: контракт — это граница с фронтендом,
и она не должна меняться от того, что кто-то добавил колонку в запрос.
Заодно эти модели превращаются в OpenAPI, по которому фронтенд сверяет типы.
"""
from __future__ import annotations

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


class Participant(BaseModel):
    student_id: str
    name: str
    attendance: Mark | None = None
    attendance_id: str | None = None
    subscription: SubscriptionBrief | None = None
    mark_effects: dict[str, MarkEffectOut]


class TeacherCard(BaseModel):
    id: str
    name: str
    rate: int


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


class AttendanceRequest(BaseModel):
    student_id: str
    mark: Mark


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
    reverted: Reverted
    lesson_status: LessonStatus


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorBody
