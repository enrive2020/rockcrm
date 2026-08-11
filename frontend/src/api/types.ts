/**
 * Граница с бэкендом. Единственный файл, где описаны формы ответов API.
 *
 * Типы написаны вручную по `docs/contract-v1.md` и не генерируются из схемы:
 * контракт первичен, а генератор скрыл бы расхождение между документом
 * и реальностью — здесь оно должно быть заметно при первом же несовпадении.
 *
 * Все моменты времени — ISO 8601 со смещением филиала ("2026-08-12T11:00:00+06:00").
 * Деньги — целое число тенге.
 */

/** Вид занятия. Контракт, `GET /schedule`. */
export type LessonKind = 'regular' | 'trial' | 'makeup' | 'extra';

/** Статус занятия. `cancelled` в расписании не приходит, но встречается в ответе POST. */
export type LessonStatus = 'planned' | 'held' | 'cancelled';

/** Шесть значений отметки — ключи `mark_effects` и тело POST. */
export type AttendanceMark =
  | 'came'
  | 'late'
  | 'no_show'
  | 'cancelled_early'
  | 'cancelled_late'
  | 'cancelled_teacher';

/** Порядок и подписи отметок для интерфейса. Держим рядом с типом, чтобы
 *  добавление отметки в контракт ломало сборку, а не молча терялось в UI. */
export const MARK_ORDER: AttendanceMark[] = [
  'came',
  'late',
  'no_show',
  'cancelled_early',
  'cancelled_late',
  'cancelled_teacher',
];

export const MARK_LABELS: Record<AttendanceMark, string> = {
  came: 'Пришёл',
  late: 'Опоздал',
  no_show: 'Прогул без предупреждения',
  cancelled_early: 'Отменил заранее (>24 ч)',
  cancelled_late: 'Отменил поздно (<24 ч)',
  cancelled_teacher: 'Отменил преподаватель',
};

/* ---------- GET /api/v1/branches ---------- */

export interface Branch {
  id: string;
  name: string;
  timezone: string;
  /** "10:00" — местное время открытия филиала. */
  opens_at: string;
  /** "21:00" */
  closes_at: string;
}

/* ---------- GET /api/v1/schedule ---------- */

export interface Room {
  id: string;
  name: string;
}

export interface ScheduleTeacher {
  id: string;
  name: string;
  disciplines: string[];
  /** Цвет дорожки, приходит от сервера (#RRGGBB). */
  color: string;
}

export interface LessonConflict {
  kind: 'room' | 'teacher';
  with_lesson_id: string;
  message: string;
}

export interface ScheduleLesson {
  id: string;
  starts_at: string;
  ends_at: string;
  duration_min: number;
  kind: LessonKind;
  status: LessonStatus;
  /** Имя ученика, название группы или имя из заявки для пробного. */
  title: string;
  student_id: string | null;
  room: Room;
  /** null — пока не отмечено. */
  attendance_mark: AttendanceMark | null;
  conflicts: LessonConflict[];
}

export interface ScheduleTrack {
  teacher: ScheduleTeacher;
  lessons: ScheduleLesson[];
}

export interface ScheduleSummary {
  lessons: number;
  trials: number;
  conflicts: number;
  room_utilization_pct: number;
}

/** Филиал внутри ответа расписания приходит без `timezone` — так в контракте. */
export interface ScheduleBranch {
  id: string;
  name: string;
  opens_at: string;
  closes_at: string;
}

export interface ScheduleResponse {
  date: string;
  branch: ScheduleBranch;
  tracks: ScheduleTrack[];
  summary: ScheduleSummary;
}

/* ---------- GET /api/v1/lessons/{id} ---------- */

export interface Subscription {
  id: string;
  lessons_total: number;
  lessons_balance: number;
  makeups_balance: number;
  /** "2026-08-31" */
  valid_until: string;
  status: string;
}

/**
 * Последствия одной отметки, посчитанные сервером из правил конкретного
 * абонемента. Клиент их только показывает — считать здесь нельзя,
 * иначе предпросмотр разойдётся с тем, что применит бэкенд.
 */
export interface MarkEffect {
  lessons_delta: number;
  makeups_delta: number;
  teacher_amount: number;
  lessons_after: number;
  summary: string;
}

export type MarkEffects = Record<AttendanceMark, MarkEffect>;

export interface LessonParticipant {
  student_id: string;
  name: string;
  /** Уже проставленная отметка, иначе null. */
  attendance: AttendanceMark | null;
  /** null — действующего абонемента нет, занятие идёт разовой оплатой. */
  subscription: Subscription | null;
  mark_effects: MarkEffects;
}

export interface LessonTeacher {
  id: string;
  name: string;
  rate: number;
}

export interface LessonNote {
  body: string;
  homework: string;
  tags: string[];
}

export interface LessonCard {
  id: string;
  starts_at: string;
  ends_at: string;
  duration_min: number;
  kind: LessonKind;
  status: LessonStatus;
  room: Room;
  teacher: LessonTeacher;
  participants: LessonParticipant[];
  note: LessonNote | null;
}

/* ---------- POST /api/v1/lessons/{id}/attendance ---------- */

export interface AttendanceRequest {
  student_id: string;
  mark: AttendanceMark;
}

export interface AppliedEffect {
  lessons_delta: number;
  lessons_after: number;
  makeups_delta: number;
  teacher_amount: number;
  teacher_id: string;
  subscription_id: string | null;
}

export interface AttendanceAlert {
  kind: string;
  message: string;
}

export interface AttendanceResponse {
  attendance_id: string;
  mark: AttendanceMark;
  applied: AppliedEffect;
  lesson_status: LessonStatus;
  alerts: AttendanceAlert[];
}

/* ---------- DELETE /api/v1/attendance/{id} ---------- */

/** Контракт описывает «200 с описанием компенсации», но не даёт тела.
 *  Держим форму по аналогии с POST и помечаем поля необязательными. */
export interface AttendanceRevokeResponse {
  attendance_id?: string;
  compensation?: Partial<AppliedEffect>;
  lesson_status?: LessonStatus;
}

/* ---------- Ошибки ---------- */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
