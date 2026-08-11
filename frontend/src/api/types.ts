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

/**
 * Что действующая отметка **уже** сделала — факт из журнала, а не пересчёт
 * правил. Появилось в этапе 4 (issue #22) и закрывает пробел 5: раньше блок
 * «Отмечено» брал числа из `mark_effects`, то есть из предпросмотра,
 * посчитанного от уже уменьшенного остатка, и `lessons_after` отставал
 * ровно на списанное занятие.
 *
 * `lessons_after` здесь — настоящий текущий остаток абонемента, поэтому
 * он допускает null: у ученика без абонемента остатка нет вовсе.
 */
export interface AppliedMarkEffect {
  mark: AttendanceMark;
  attendance_id: string;
  lessons_delta: number;
  makeups_delta: number;
  lessons_after: number | null;
  makeups_after: number | null;
  teacher_amount: number;
  summary: string;
}

export interface LessonParticipant {
  student_id: string;
  name: string;
  /** Уже проставленная отметка, иначе null. */
  attendance: AttendanceMark | null;
  /**
   * Идентификатор проставленной отметки — единственное, чем можно вызвать
   * `DELETE /attendance/{id}`. Контракт этапа 1 его в карточке не описывал,
   * поэтому отмена и не была сделана; бэкенд поле добавил (задача #10).
   * `null` — участник не отмечен либо сервер поле ещё не отдаёт: в этом
   * случае кнопка отмены не показывается, а не ломает панель.
   */
  attendance_id: string | null;
  /** null — действующего абонемента нет, занятие идёт разовой оплатой. */
  subscription: Subscription | null;
  mark_effects: MarkEffects;
  /**
   * null — участник не отмечен либо сервер поле ещё не отдаёт (старый бэкенд).
   * Во втором случае интерфейс молча обходится без блока фактов, а не падает.
   */
  applied_effect?: AppliedMarkEffect | null;
}

export interface LessonTeacher {
  id: string;
  name: string;
  /**
   * `null` — «вам не видно», а не «ставка ноль» (этап 5). Свою ставку видит
   * сам преподаватель, чужую — только владелец; администратору ресепшена
   * сервер присылает `null`. Ноль на экране прочитался бы как «работает
   * бесплатно» и однажды попал бы в разговор, поэтому здесь только прочерк.
   */
  rate: number | null;
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

/**
 * Что именно вернулось при отмене отметки. Знаки зеркальны применению:
 * списанное занятие приходит как `lessons_delta: +1`, начисление
 * преподавателю — как отрицательная корректировка.
 *
 * `*_after` допускают null: у ученика без абонемента возвращать некуда,
 * и сервер честно отдаёт «остатка нет», а не ноль.
 */
export interface RevertedEffect {
  lessons_delta: number;
  lessons_after: number | null;
  makeups_delta: number;
  makeups_after: number | null;
  teacher_amount: number;
  teacher_id: string;
  subscription_id: string | null;
}

/**
 * Контракт этапа 1 описывает «200 с описанием компенсации», но тела не даёт.
 * Форма списана с реализации бэкенда (`schemas.AttendanceRevoked`): держать
 * здесь выдуманную по аналогии структуру означало бы молча разойтись
 * с сервером на первом же вызове.
 */
export interface AttendanceRevokeResponse {
  attendance_id: string;
  mark: AttendanceMark;
  revoked_at: string;
  reverted: RevertedEffect;
  /** `planned`, если действующих отметок на занятии не осталось. */
  lesson_status: LessonStatus;
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

/* ==========================================================================
   Этап 3 — воронка заявок (docs/contract-v3.md)
   ========================================================================== */

/** Стадии из CHECK `lead.stage`. Порядок — порядок колонок на доске. */
export type LeadStage = 'new' | 'contacting' | 'trial_booked' | 'trial_held' | 'won' | 'lost';

/** `lost` живёт отдельной колонкой: это не следующий шаг, а выход из воронки. */
export const STAGE_ORDER: LeadStage[] = ['new', 'contacting', 'trial_booked', 'trial_held', 'won'];

export const STAGE_LABELS: Record<LeadStage, string> = {
  new: 'Новая',
  contacting: 'Дозвон',
  trial_booked: 'Пробный назначен',
  trial_held: 'Пробный проведён',
  won: 'Абонемент куплен',
  lost: 'Отказ',
};

/** Источники из CHECK `lead.source`. */
export type LeadSource =
  | 'telegram_bot'
  | 'site_form'
  | 'whatsapp'
  | 'instagram'
  | 'referral'
  | 'walk_in'
  | 'phone'
  | 'other';

export const SOURCES: LeadSource[] = [
  'telegram_bot',
  'site_form',
  'whatsapp',
  'instagram',
  'referral',
  'walk_in',
  'phone',
  'other',
];

export const SOURCE_LABELS: Record<LeadSource, string> = {
  telegram_bot: 'TG-бот',
  site_form: 'Сайт',
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
  referral: 'Сарафан',
  walk_in: 'Пришёл сам',
  phone: 'Звонок',
  other: 'Другое',
};

/** Причины отказа из CHECK `lead.lost_reason`. */
export type LostReason = 'price' | 'location' | 'schedule' | 'competitor' | 'no_answer' | 'not_ready' | 'other';

export const LOST_REASONS: LostReason[] = [
  'price',
  'location',
  'schedule',
  'competitor',
  'no_answer',
  'not_ready',
  'other',
];

export const LOST_REASON_LABELS: Record<LostReason, string> = {
  price: 'Дорого',
  location: 'Далеко ехать',
  schedule: 'Не подошло расписание',
  competitor: 'Ушли к конкуренту',
  no_answer: 'Не дозвонились',
  not_ready: 'Пока не готовы',
  other: 'Другое',
};

/** Флаги — то, на что администратор обязан среагировать. */
export type LeadFlag = 'no_answer' | 'overdue' | 'trial_today' | 'trial_conflict';

export const FLAG_LABELS: Record<LeadFlag, string> = {
  no_answer: 'Не дозвонились',
  overdue: 'Просрочено',
  trial_today: 'Пробный сегодня',
  trial_conflict: 'Кабинет занят',
};

/** Флаг требует немедленного вмешательства — красный, а не латунный. */
export const FLAG_IS_BAD: Record<LeadFlag, boolean> = {
  no_answer: true,
  overdue: true,
  trial_today: false,
  trial_conflict: true,
};

export interface LeadUser {
  id: string;
  name: string;
}

/**
 * Назначенный пробный. В карточке контракт описывает его полностью,
 * на доске называет «кратким описанием» без формы — держим одну структуру
 * с необязательными полями, чтобы доска и карточка не разошлись.
 */
export interface LeadTrial {
  lesson_id: string;
  starts_at: string;
  teacher: string;
  room: string;
  status?: LessonStatus;
  conflicts?: LessonConflict[];
}

/* ---------- GET /api/v1/leads ---------- */

export interface BoardLead {
  id: string;
  name: string;
  student_name: string | null;
  student_age: number | null;
  discipline: string | null;
  source: LeadSource;
  phone: string | null;
  created_at: string;
  /** Человеческая длительность с последней смены стадии: «2 часа», «3 дня». */
  waiting_for: string;
  next_action_at: string | null;
  contact_attempts: number;
  assigned_to: LeadUser | null;
  trial: LeadTrial | null;
  flags: LeadFlag[];
}

export interface BoardColumn {
  stage: LeadStage;
  title: string;
  count: number;
  leads: BoardLead[];
}

export interface BoardSummary {
  total: number;
  overdue: number;
  conversion_trial_to_won_pct: number;
  avg_days_to_won: number;
}

export interface LeadsBoard {
  columns: BoardColumn[];
  summary: BoardSummary;
}

/* ---------- GET /api/v1/leads/{id} ---------- */

/**
 * Направление заявки. `min_age` контракт в ответе не показывает, но требует
 * сверять с ним возраст — держим необязательным полем: если бэкенд его
 * не пришлёт, предупреждение просто не появится.
 */
export interface LeadDiscipline {
  id: string;
  name: string;
  min_age?: number;
}

export interface LeadStageChange {
  at: string;
  from: LeadStage | null;
  to: LeadStage;
  by: string | null;
}

export interface LeadCard {
  id: string;
  name: string;
  phone: string | null;
  student_name: string | null;
  student_age: number | null;
  discipline: LeadDiscipline | null;
  branch: { id: string; name: string } | null;
  stage: LeadStage;
  lost_reason: LostReason | null;
  source: LeadSource;
  utm: Record<string, string>;
  promo_code: string | null;
  assigned_to: LeadUser | null;
  next_action_at: string | null;
  contact_attempts: number;
  created_at: string;
  /** Комментарий из заявки: контракт принимает его в POST, но в ответе не показывает. */
  comment?: string | null;
  trial: LeadTrial | null;
  history: LeadStageChange[];
  converted: { student_id: string | null; person_id: string | null };
}

/* ---------- POST /api/v1/leads ---------- */

export interface CreateLeadRequest {
  name: string;
  phone: string;
  student_name?: string;
  student_age?: number;
  discipline_id?: string | null;
  branch_id?: string | null;
  source: LeadSource;
  comment?: string;
}

/* ---------- PATCH /api/v1/leads/{id} ---------- */

export interface PatchLeadRequest {
  stage?: LeadStage;
  assigned_to?: string | null;
  next_action_at?: string | null;
  contact_attempts?: number;
  lost_reason?: LostReason | null;
}

/* ---------- POST /api/v1/leads/{id}/trial ---------- */

export interface TrialRequest {
  teacher_id: string;
  room_id: string;
  starts_at: string;
  duration_min: number;
  price: number;
  /** Подтверждение овербукинга после 409 от сервера. */
  overbook_ack: boolean;
}

export interface TrialResponse {
  lesson_id: string;
  stage: LeadStage;
  starts_at: string;
  teacher: string;
  room: string;
  notification_queued: boolean;
}

/* ---------- POST /api/v1/leads/{id}/convert ---------- */

export interface ConvertPayer {
  first_name: string;
  last_name: string;
  phone: string;
}

export interface ConvertStudent {
  first_name: string;
  last_name: string;
  birth_date?: string;
  discipline_id?: string | null;
  branch_id?: string | null;
  main_teacher_id?: string | null;
}

export interface ConvertRequest {
  /** Необязателен: у взрослого ученика плательщик — он сам. */
  payer?: ConvertPayer;
  student: ConvertStudent;
  /** Необязателен: ученика можно завести, а абонемент продать позже. */
  subscription?: {
    plan_id: string;
    starts_on: string;
    discount_pct?: number;
    promo_code?: string;
    payment?: { amount: number; method: PaymentMethod };
  };
}

export interface ConvertResponse {
  student_id: string;
  person_id: string;
  family_id: string;
  subscription_id: string | null;
  stage: LeadStage;
}

/* ---------- GET /api/v1/leads/funnel ---------- */

export interface FunnelStageRow {
  stage: LeadStage;
  entered: number;
  moved_on: number;
  conversion_pct: number;
}

export interface FunnelSourceRow {
  source: LeadSource;
  leads: number;
  trials: number;
  won: number;
  conversion_pct: number;
  avg_days_to_won: number;
}

export interface FunnelReport {
  period: { from: string; to: string };
  stages: FunnelStageRow[];
  sources: FunnelSourceRow[];
  lost_reasons: { reason: LostReason; count: number }[];
  avg_days_to_won: number;
}

/* ==========================================================================
   Справочники: GET /teachers, /rooms, /disciplines (задача #1)

   В контрактах этих эндпоинтов нет — формы описаны по задаче #1 и по схеме
   базы (`staff`, `staff_discipline`, `staff_branch`, `room`, `discipline`).
   Всё, чего задача не гарантирует явно, объявлено необязательным: пока
   бэкенд их не отдаёт, интерфейс должен работать на запасном пути,
   а не падать на отсутствующем ключе.
   ========================================================================== */

export interface DirectoryTeacher {
  id: string;
  name: string;
  /** Направления, которые преподаватель ведёт: «Барабаны», «Гитара». */
  disciplines?: string[];
  /** Филиалы, где он работает. Пусто — считаем, что во всех. */
  branch_ids?: string[];
  /** Цвет дорожки, тот же, что в расписании. */
  color?: string;
  rate?: number;
}

export interface DirectoryRoom {
  id: string;
  name: string;
  branch_id: string;
  /** `room.features`: барабанную установку под барабаны не подменишь. */
  features?: Record<string, boolean>;
}

export interface Discipline {
  id: string;
  name: string;
  /** Минимальный возраст: барабаны с 5 лет, скрипка с 7. null — порога нет. */
  min_age?: number | null;
  /** Требования к кабинету, сверяются с `room.features`. */
  room_reqs?: Record<string, boolean>;
}

/* ==========================================================================
   Этап 4 — деньги и зарплаты (docs/contract-v4.md)

   Формы сняты с работающего API: серверная часть была написана раньше
   фронтенда, и контракт показывает уже реализованные ответы. Поля, которых
   в контракте нет, а в ответе есть (`branch_id` в ведомости и отчётах),
   отмечены комментарием — их стоит внести в контракт словами.

   Период всюду — пара `from`/`to`, **включительно с обеих сторон**.
   Деньги — целое число тенге.
   ========================================================================== */

/** Период отчёта. `from` и `to` включительно — так во всех ответах этапа. */
export interface ReportPeriod {
  from: string;
  to: string;
}

/* ---------- GET /api/v1/payroll ---------- */

export interface PayrollTeacherRef {
  id: string;
  name: string;
  /** Цвет дорожки, тот же, что в расписании. */
  color: string | null;
}

export interface PayrollRow {
  teacher: PayrollTeacherRef;
  /** Уникальные занятия: у группы из четырёх человек занятие одно… */
  lessons: number;
  /** …а начислений четыре. Числа разные и путать их нельзя. */
  entries: number;
  /** Самая частая сумма начисления. */
  rate: number;
  /** Ставок несколько (55 и 85 минут, индивидуальное и группа). */
  rate_varies: boolean;
  no_shows: number;
  accrued: number;
  corrections: number;
  bonuses: number;
  total: number;
  /** Сколько из суммы приехало правками за уже закрытые месяцы. */
  carried_over: number;
  carried_over_entries: number;
}

export type PayrollTotals = Omit<PayrollRow, 'teacher' | 'rate' | 'rate_varies'> & { teachers: number };

export interface PayrollSheet {
  period: ReportPeriod;
  /** Открытого периода не существует: `false` значит «месяц ещё не закрыт». */
  closed: boolean;
  period_id: string | null;
  closed_at: string | null;
  closed_by: string | null;
  /** В контракте не показан, в ответе есть. */
  branch_id?: string | null;
  teachers: PayrollRow[];
  totals: PayrollTotals;
  /** Человеческая формулировка от сервера — показывается дословно. */
  note: string;
}

/* ---------- GET /api/v1/payroll/teachers/{staff_id} ---------- */

/** Снимок расчёта, сохранённый при отметке: через полгода объяснить сумму больше нечем. */
export interface PayrollCalc {
  kind?: string;
  mark?: AttendanceMark | null;
  rate?: number;
  share?: number;
  amount?: number;
  [key: string]: unknown;
}

export type PayrollEntryKind = 'lesson' | 'bonus' | 'correction' | 'deduction';

export const PAYROLL_ENTRY_KIND_LABELS: Record<PayrollEntryKind, string> = {
  lesson: 'Занятие',
  bonus: 'Премия',
  correction: 'Корректировка',
  deduction: 'Удержание',
};

export interface PayrollEntry {
  id: number;
  date: string;
  starts_at: string | null;
  kind: PayrollEntryKind;
  lesson_id: string | null;
  student: string | null;
  discipline: string | null;
  branch: string | null;
  duration_min: number | null;
  mark: AttendanceMark | null;
  amount: number;
  calc: PayrollCalc;
  /** Начисление за день из уже закрытого месяца — приехало корректировкой. */
  carried_over: boolean;
}

export interface PayrollDetail {
  teacher: PayrollTeacherRef;
  period: ReportPeriod;
  closed: boolean;
  period_id: string | null;
  closed_at: string | null;
  closed_by: string | null;
  /** Та же строка, что в ведомости. */
  totals: PayrollRow;
  entries: PayrollEntry[];
}

/* ---------- GET /api/v1/payroll/periods ---------- */

export interface PayrollPeriodRow {
  id: string;
  period: ReportPeriod;
  closed: boolean;
  closed_at: string | null;
  closed_by: string | null;
  teachers: number;
  entries: number;
  total: number;
}

/* ---------- POST /api/v1/payroll/periods ---------- */

export interface ClosePeriodRequest {
  from: string;
  to: string;
}

/**
 * Что именно закрыто. `message` — формулировка сервера для показа
 * администратору: числа в ней уже посчитаны, пересказывать их своими
 * словами значило бы завести второй источник правды.
 */
export interface PeriodClosed {
  id: string;
  period: ReportPeriod;
  closed: boolean;
  closed_at: string;
  entries: number;
  teachers: number;
  total: number;
  message: string;
}

/* ---------- GET /api/v1/reports/revenue ---------- */

export interface RevenueSlice {
  /** null — платёж не привязан к абонементу, строка «Не распределено». */
  id: string | null;
  name: string;
  amount: number;
  payments: number;
  share_pct: number;
}

export interface RevenueMonth {
  /** "2026-08" */
  month: string;
  amount: number;
  payments: number;
}

export interface RevenueMethod {
  method: PaymentMethod;
  amount: number;
  payments: number;
  share_pct: number;
}

export interface RevenueReport {
  period: ReportPeriod;
  branch_id?: string | null;
  total: number;
  payments: number;
  by_branch: RevenueSlice[];
  by_discipline: RevenueSlice[];
  by_month: RevenueMonth[];
  by_method: RevenueMethod[];
}

/* ---------- GET /api/v1/reports/rooms ---------- */

export interface RoomLoad {
  room_id: string;
  room: string;
  branch_id: string;
  branch: string;
  lessons: number;
  busy_minutes: number;
  capacity_minutes: number;
  utilization_pct: number;
}

export interface BranchLoad {
  branch_id: string;
  branch: string;
  rooms: number;
  open_days: number;
  open_minutes_per_day: number;
  busy_minutes: number;
  capacity_minutes: number;
  utilization_pct: number;
}

export interface RoomsReport {
  period: ReportPeriod;
  branch_id?: string | null;
  utilization_pct: number;
  busy_minutes: number;
  capacity_minutes: number;
  /** Формула ёмкости словами: процент загрузки без указания базы — число, которому нельзя верить. */
  capacity_note: string;
  branches: BranchLoad[];
  /** Отсортированы по загрузке: отчёт отвечает на вопрос «где кончилось место». */
  rooms: RoomLoad[];
}

/* ---------- GET /api/v1/reports/debts ---------- */

export interface Debtor {
  family_id: string;
  payer: string | null;
  phone: string | null;
  students: string[];
  charged: number;
  paid: number;
  debt: number;
  since_on: string | null;
  last_paid_on: string | null;
}

export interface DebtsReport {
  families: number;
  total: number;
  items: Debtor[];
  note: string;
}

/* ---------- GET /api/v1/reports/summary ---------- */

export interface SummaryRevenue {
  amount: number;
  previous: number;
  previous_period: ReportPeriod | null;
  /**
   * null, когда прошлого периода нет: «+100%» от нуля читается как рост,
   * хотя означает лишь появление первых данных.
   */
  change_pct: number | null;
}

export interface SummaryRooms {
  utilization_pct: number;
  /** Самый загруженный кабинет — тот, где кончилось место. */
  busiest: RoomLoad | null;
}

export interface SummaryPayroll {
  /**
   * `null` — фонд оплаты труда скрыт от роли (администратору его не видно,
   * §2). Это те же деньги людей, что и ведомость, только одной строкой,
   * и показывать вместо них ноль значило бы соврать.
   */
  total: number | null;
  lessons: number;
  closed: boolean;
}

/** Блок «требует внимания»: всё, на что администратор обязан среагировать. */
export interface SummaryAttention {
  debt_families: number;
  debt_amount: number;
  subscriptions_running_low: number;
  makeups_open: number;
  frozen_now: number;
}

/**
 * Отток приходит в сводке, но в интерфейс не выводится: текущая реализация
 * считает его по абонементам, а не по ученикам, и завышает в разы
 * (issue #25). Поле оставлено в типе — прятать его из формы ответа значило бы
 * забыть о нём, когда бэкенд починится.
 */
export interface SummaryChurn {
  ended: number;
  churned: number;
  churn_pct: number;
  worst_teacher: { teacher_id: string | null; name: string; ended: number; churned: number; churn_pct: number } | null;
}

export interface MoneySummary {
  period: ReportPeriod;
  branch_id?: string | null;
  revenue: SummaryRevenue;
  rooms: SummaryRooms;
  churn: SummaryChurn;
  payroll: SummaryPayroll;
  attention: SummaryAttention;
}

/* ==========================================================================
   Этап 5: вход, сессия и роли.

   Контракта у этапа не было — формы сняты со схем бэкенда
   (`schemas.CodeSent`, `LoginRequest`, `Me`, `LoggedIn`, `LoggedOut`).
   Токен сессии в этих типах не появляется намеренно: он приезжает в куке
   `HttpOnly`, и прочитать его интерфейс не может — в этом весь смысл.
   Поле `token` в ответе входа существует для curl и мобильного клиента,
   и класть его в `localStorage` нельзя: оттуда его унесёт первый же XSS.
   ========================================================================== */

export type Role = 'owner' | 'admin' | 'teacher' | 'guardian' | 'student';

/** Подписи ролей. Рядом с типом: новая роль в контракте обязана ломать сборку. */
export const ROLE_LABELS: Record<Role, string> = {
  owner: 'Владелец',
  admin: 'Администратор',
  teacher: 'Преподаватель',
  guardian: 'Родитель',
  student: 'Ученик',
};

export interface TenantRef {
  id: string;
  name: string;
}

/** `GET /api/v1/auth/me` — единственный источник правды о роли. */
export interface Me {
  user_id: string;
  name: string;
  role: Role;
  tenant: TenantRef;
  person_id: string;
  /** Есть у сотрудника: владельца, администратора, преподавателя. */
  staff_id: string | null;
  /**
   * ПУСТОЙ МАССИВ ОЗНАЧАЕТ «ВСЕ ФИЛИАЛЫ», а не «ни одного». Школа из одного
   * филиала связь `staff_branch` не заполняет вовсе, и прочтение пустоты
   * как запрета выключило бы владельцу всю школу.
   */
  branch_ids: string[];
  /** Дети родителя или ученики преподавателя. У владельца и админа пуст. */
  student_ids: string[];
}

/** `POST /api/v1/auth/request-code` — всегда 202, даже на чужой номер. */
export interface CodeSent {
  sent: boolean;
  /**
   * Замаскированный адрес. Собран из того, что прислали, а не из того, что
   * нашлось в базе: иначе форма входа отвечала бы на вопрос «ходит ли этот
   * ребёнок в эту школу».
   */
  to: string;
  /** Секунд до протухания кода — из него считается обратный отсчёт. */
  expires_in: number;
  message: string;
}

/** `POST /api/v1/auth/login`. Код и пароль вместе — 400, поэтому ровно одно. */
export interface LoginRequest {
  tenant: string;
  login: string;
  code?: string;
  password?: string;
}

export interface LoggedIn {
  user: Me;
  expires_at: string;
  /** Для curl и мобильного клиента. Браузеру не нужен: он получил куку. */
  token?: string;
}

export interface LoggedOut {
  ok: boolean;
  /** «Вы вышли» и «Вы вышли из всех сеансов» — разные действия, разный текст. */
  message: string;
}

/* ---------- Ошибки ---------- */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
