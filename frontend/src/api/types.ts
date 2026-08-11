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

/* ==========================================================================
   Этап 2 — карточка ученика и жизненный цикл абонемента (docs/contract-v2.md)
   ========================================================================== */

/** Плательщик семьи: тот, кто звонит администратору. */
export interface Payer {
  name: string;
  phone: string;
}

/* ---------- GET /api/v1/students?query= ---------- */

/** Краткий абонемент в строке результата поиска. */
export interface StudentSearchSubscription {
  lessons_balance: number;
  lessons_total: number;
  /** "2026-08-31" */
  valid_until: string;
  status: string;
}

export interface StudentSearchItem {
  id: string;
  name: string;
  age: number;
  discipline: string;
  teacher: string;
  branch: string;
  /** null — действующего абонемента нет. */
  subscription: StudentSearchSubscription | null;
  /**
   * Контракт показывает плательщика всегда, но у взрослого ученика семьи
   * может не быть. Допускаем null: строка поиска без телефона полезнее,
   * чем упавший экран.
   */
  payer: Payer | null;
}

/* ---------- GET /api/v1/students/{id} ---------- */

/**
 * Правила абонемента — копия настроек школы на момент продажи.
 * Ключи совпадают с `tenant.default_rules` (db/001_core.sql).
 */
export interface SubscriptionRules {
  no_show_burns: boolean;
  cancel_notice_hours: number;
  cancel_early_effect: 'makeup' | 'keep' | 'burn';
  teacher_cancel_effect: 'makeup' | 'keep' | 'no_charge';
  makeup_ttl_days: number;
  freeze_days_per_year: number;
  /**
   * Перенос остатка при продлении. В примере карточки контракта ключа нет,
   * но раздел про продажу на него ссылается — держим необязательным,
   * пока бэкенд не начнёт его отдавать.
   */
  carry_over_lessons?: number;
  pay_teacher_on_no_show?: boolean;
}

export interface SubscriptionHold {
  id: string;
  /** "2026-08-14" — первый день заморозки. */
  from: string;
  /** "2026-08-25" */
  to: string;
  days: number;
  reason: string | null;
}

export interface StudentSubscription {
  id: string;
  plan_name: string;
  lessons_total: number;
  lessons_balance: number;
  makeups_balance: number;
  price: number;
  lesson_price: number;
  valid_from: string;
  valid_until: string;
  status: string;
  rules: SubscriptionRules;
  holds: SubscriptionHold[];
  /** За календарный год. */
  freeze_days_used: number;
  freeze_days_left: number;
}

export interface MakeupCredit {
  id: string;
  /** Дата занятия, за которое выдана отработка. */
  granted_for: string;
  expires_on: string;
  days_left: number;
  /** ISO-момент использования или null. */
  used_at: string | null;
}

/** Вид движения по абонементу — значения из CHECK `subscription_entry.kind`. */
export type LedgerKind =
  | 'purchase'
  | 'charge'
  | 'refund'
  | 'makeup_grant'
  | 'makeup_use'
  | 'makeup_expire'
  | 'freeze'
  | 'adjust'
  | 'transfer_in'
  | 'transfer_out'
  | 'expire';

export interface LedgerEntry {
  id: number;
  date: string;
  kind: LedgerKind;
  /** Человеческая формулировка от сервера: «Занятие проведено», «Оплата абонемента». */
  title: string;
  teacher: string | null;
  lessons_delta: number;
  makeups_delta: number;
  /** Деньги движения или null, если движение неденежное. */
  amount: number | null;
}

export interface StudentNote {
  date: string;
  author: string;
  body: string;
  homework: string;
  tags: string[];
}

export interface ChurnRisk {
  level: 'low' | 'medium' | 'high';
  score: number;
  /** Каждая причина — проверяемый факт, а не оценка (требование контракта). */
  reasons: string[];
}

export interface FamilyMember {
  student_id: string;
  name: string;
  age: number;
  discipline: string;
  lessons_balance: number;
}

export interface Family {
  id: string;
  payer: Payer | null;
  discount_pct: number;
  members: FamilyMember[];
  paid_this_month: number;
  debt: number;
}

export interface StudentCard {
  id: string;
  name: string;
  age: number;
  discipline: string;
  teacher: string;
  branch: string;
  started_on: string;
  status: string;
  family: Family | null;
  subscription: StudentSubscription | null;
  makeups: MakeupCredit[];
  /** Новые сверху — так требует контракт. */
  ledger: LedgerEntry[];
  notes: StudentNote[];
  churn_risk: ChurnRisk | null;
}

export const CHURN_LABELS: Record<ChurnRisk['level'], string> = {
  low: 'Низкий',
  medium: 'Средний',
  high: 'Высокий',
};

/* ---------- GET /api/v1/plans ---------- */

export type PlanFormat = 'individual' | 'pair' | 'group' | 'trial';

export interface Plan {
  id: string;
  name: string;
  discipline: string;
  format: PlanFormat;
  duration_min: number;
  lessons_count: number;
  /** Срок жизни абонемента в днях: месячный — 31, полугодовой — 184. */
  valid_days: number;
  price: number;
}

/* ---------- POST /api/v1/students/{id}/subscriptions ---------- */

/** Способы оплаты из CHECK `payment.method`. */
export type PaymentMethod = 'kaspi' | 'card' | 'cash' | 'transfer' | 'other';

export const PAYMENT_METHODS: PaymentMethod[] = ['kaspi', 'card', 'cash', 'transfer', 'other'];

export const PAYMENT_METHOD_LABELS: Record<PaymentMethod, string> = {
  kaspi: 'Kaspi',
  card: 'Карта',
  cash: 'Наличные',
  transfer: 'Перевод',
  other: 'Другое',
};

export interface SellSubscriptionRequest {
  plan_id: string;
  starts_on: string;
  discount_pct?: number;
  promo_code?: string;
  /** Необязателен: абонемент можно оформить в долг, деньги донесут позже. */
  payment?: { amount: number; method: PaymentMethod };
  carry_over?: boolean;
}

export interface SellSubscriptionResponse {
  subscription_id: string;
  lessons_total: number;
  lessons_balance: number;
  valid_from: string;
  valid_until: string;
  price: number;
  discount_pct: number;
  charged: number;
  /** Сколько занятий перенесено с прошлого абонемента; 0 — правило школы запрещает. */
  carried_over: number;
  payment_id: string | null;
  debt: number;
}

/* ---------- POST /api/v1/subscriptions/{id}/holds ---------- */

export interface HoldRequest {
  from: string;
  to: string;
  reason: string;
}

export interface HoldResponse {
  hold_id: string;
  days: number;
  valid_until_before: string;
  valid_until_after: string;
  lessons_cancelled: number;
  freeze_days_left: number;
}

/* ---------- DELETE /api/v1/subscriptions/{id}/holds/{hold_id} ---------- */

/**
 * Контракт описывает смысл ответа словами («сколько занятий было отменено
 * и что их нужно поставить заново»), но не даёт тела. Поля необязательные,
 * как и у отмены отметки в этапе 1.
 */
export interface HoldReleaseResponse {
  hold_id?: string;
  days?: number;
  valid_until_before?: string;
  valid_until_after?: string;
  /** Отменённые занятия не восстанавливаются — их надо поставить заново. */
  lessons_cancelled?: number;
  freeze_days_left?: number;
  message?: string;
}

/* ---------- Ошибки ---------- */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
