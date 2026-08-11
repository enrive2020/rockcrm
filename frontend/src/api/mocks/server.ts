import { ApiError } from '../http';
import type {
  AppliedMarkEffect,
  AttendanceMark,
  AttendanceRequest,
  AttendanceResponse,
  AttendanceRevokeResponse,
  BoardColumn,
  BoardLead,
  Branch,
  BranchLoad,
  ChurnRisk,
  ConvertRequest,
  ConvertResponse,
  CreateLeadRequest,
  DebtsReport,
  Debtor,
  DirectoryRoom,
  DirectoryTeacher,
  Discipline,
  Family,
  FunnelReport,
  FunnelSourceRow,
  HoldReleaseResponse,
  HoldRequest,
  HoldResponse,
  LeadCard,
  LeadFlag,
  LeadStage,
  LeadTrial,
  LeadsBoard,
  LessonCard,
  LessonConflict,
  LessonParticipant,
  LessonStatus,
  LostReason,
  MarkEffect,
  MarkEffects,
  MeChild,
  MeChildCard,
  MeHistoryEntry,
  MeMakeup,
  MeNote,
  MeProgress,
  MeSchedule,
  MeScheduleLesson,
  MeSubscription,
  MoneySummary,
  PatchLeadRequest,
  PaymentMethod,
  PayrollDetail,
  PayrollEntry,
  PayrollPeriodRow,
  PayrollRow,
  PayrollSheet,
  PayrollTotals,
  PeriodClosed,
  Plan,
  RenewCreated,
  RenewRequest,
  RescheduleCreated,
  RescheduleRequest,
  RevenueReport,
  RevenueSlice,
  RoomLoad,
  RoomsReport,
  ScheduleLesson,
  ScheduleResponse,
  ScheduleTrack,
  SellSubscriptionRequest,
  SellSubscriptionResponse,
  StudentCard,
  StudentSearchItem,
  StudentSubscription,
  TrialRequest,
  TrialResponse,
  CodeSent,
  LoggedIn,
  LoggedOut,
  LoginRequest,
  Me,
  Role,
  TenantRef,
} from '../types';
import { MARK_ORDER, PAYMENT_METHOD_LABELS, SOURCES, STAGE_LABELS, STAGE_ORDER } from '../types';
import {
  ACCRUALS,
  BRANCHES,
  BRANCH_AF,
  CURRENT_USER,
  DEFAULT_RULES,
  DISCIPLINES,
  FAMILIES,
  LEADS,
  LEDGER,
  LESSONS,
  MAKEUPS,
  MOCK_TODAY,
  NOTES,
  PAYMENTS,
  PAYROLL_PERIODS,
  PLANS,
  ROOMS,
  ROOM_DAILY_BUSY_MIN,
  STUDENTS,
  TEACHERS,
  TZ_OFFSET,
  addDays,
  daysBetween,
  disciplineByName,
  findDiscipline,
  findFamily,
  findLead,
  findPlan,
  findRoom,
  findStudent,
  findTeacher,
  findUser,
  nextAccrualId,
  nextEntryId,
  nextLeadId,
  nextPeriodId,
  type MockAccrual,
  type MockHold,
  type MockLead,
  type MockLesson,
  type MockPayment,
  type MockRules,
  type MockStudent,
  type MockSubscription,
  type MockTeacher,
} from './data';

/**
 * Мок-сервер: держит состояние дня в памяти вкладки и отвечает ровно теми
 * структурами, что описаны в контракте. Живёт отдельно от компонентов —
 * при появлении настоящего бэкенда файл выключается переменной окружения
 * и удаляется целиком, не задев остальной код.
 */

/* ---------- состояние ---------- */

/** lesson_id → student_id → отметка. Заполняется из фикстур при первом обращении. */
const marks = new Map<string, Map<string, AttendanceMark>>();
/** `lesson_id:student_id` → attendance_id, чтобы отдать идентификатор отметки. */
const attendanceIds = new Map<string, string>();
/** attendance_id → чья это отметка. Обратный путь нужен отмене: DELETE знает только id. */
const attendanceOwners = new Map<string, { lessonId: string; studentId: string }>();
/** Уже отменённые отметки: повторный DELETE обязан отвечать 409, а не гасить второй раз. */
const revokedAttendance = new Set<string>();
/**
 * Факт применённой отметки: что она списала и начислила на самом деле.
 * Это журнал, а не предпросмотр: `mark_effects` отвечает на «что будет»,
 * а карточке занятия нужен ответ на «что уже случилось» (issue #22).
 * Пересчитывать правила заново нельзя — правила абонемента могли смениться
 * после отметки, а начисленное этим не отменяется.
 */
const appliedFacts = new Map<string, { mark: AttendanceMark; lessons_delta: number; makeups_delta: number; teacher_amount: number }>();
let attendanceSeq = 0;

const newAttendanceId = (lessonId: string, studentId: string): string => {
  const id = `77777777-0000-4000-8000-${String(++attendanceSeq).padStart(12, '0')}`;
  attendanceIds.set(`${lessonId}:${studentId}`, id);
  attendanceOwners.set(id, { lessonId, studentId });
  return id;
};

for (const lesson of LESSONS) {
  if (!lesson.initial_marks) continue;
  const perLesson = new Map<string, AttendanceMark>();
  for (const [studentId, mark] of Object.entries(lesson.initial_marks)) {
    perLesson.set(studentId, mark);
    // Отметки из фикстур тоже получают идентификатор: иначе на уже отмеченном
    // дне (11 августа) кнопка отмены не появилась бы — а это ровно тот день,
    // на котором её и проверяют.
    newAttendanceId(lesson.id, studentId);
  }
  marks.set(lesson.id, perLesson);
}

const markOf = (lessonId: string, studentId: string): AttendanceMark | null =>
  marks.get(lessonId)?.get(studentId) ?? null;

/* ---------- время ---------- */

const toMinutes = (hhmm: string): number => {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
};

const toHhMm = (minutes: number): string =>
  `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

/** ISO со смещением филиала — как требует контракт. */
const iso = (date: string, minutes: number): string => `${date}T${toHhMm(minutes)}:00${TZ_OFFSET}`;

/* ---------- деньги и склонения (для серверных текстов) ---------- */

const money = (n: number): string => `${n.toLocaleString('ru-RU').replace(/ |,/g, ' ')} ₸`;

const plural = (n: number, one: string, few: string, many: string): string => {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
};

const lessonsWord = (n: number) => `${n} ${plural(n, 'занятие', 'занятия', 'занятий')}`;

/* ---------- правила отметки ---------- */

/**
 * Единственное место, где считаются последствия отметки. Им пользуются
 * и предпросмотр в GET /lessons/{id}, и применение в POST — расхождение
 * между обещанным и сделанным было бы худшим багом этапа.
 */
function computeEffect(
  mark: AttendanceMark,
  subscription: MockSubscription | null,
  teacherRate: number,
): MarkEffect {
  const rules: MockRules | null = subscription?.rules ?? null;

  let lessonsDelta = 0;
  let makeupsDelta = 0;
  let teacherAmount = 0;

  switch (mark) {
    case 'came':
    case 'late':
      lessonsDelta = -1;
      teacherAmount = teacherRate;
      break;
    case 'no_show':
      lessonsDelta = rules?.no_show_burns === false ? 0 : -1;
      teacherAmount = rules?.pay_teacher_on_no_show === false ? 0 : teacherRate;
      break;
    case 'cancelled_early':
      if (rules?.cancel_early_effect === 'burn') lessonsDelta = -1;
      else if (rules?.cancel_early_effect === 'keep') lessonsDelta = 0;
      else makeupsDelta = 1; // 'makeup' — значение по умолчанию
      break;
    case 'cancelled_late':
      // Контракт вводит отметку, но не задаёт её правило. Приравниваем к прогулу:
      // предупреждение позже порога отмены не спасает занятие (spec 4.2).
      lessonsDelta = -1;
      teacherAmount = teacherRate;
      break;
    case 'cancelled_teacher':
      if (rules && rules.teacher_cancel_effect !== 'makeup') lessonsDelta = 0;
      else makeupsDelta = 1;
      break;
  }

  // Без абонемента списывать нечего — занятие идёт разовой оплатой.
  if (!subscription) {
    lessonsDelta = 0;
    makeupsDelta = 0;
  }

  const balance = subscription?.lessons_balance ?? 0;
  const lessonsAfter = Math.max(0, balance + lessonsDelta);

  return {
    lessons_delta: lessonsDelta,
    makeups_delta: makeupsDelta,
    teacher_amount: teacherAmount,
    lessons_after: lessonsAfter,
    summary: summarize(subscription, lessonsDelta, makeupsDelta, teacherAmount, lessonsAfter, rules),
  };
}

function summarize(
  subscription: MockSubscription | null,
  lessonsDelta: number,
  makeupsDelta: number,
  teacherAmount: number,
  lessonsAfter: number,
  rules: MockRules | null,
): string {
  const pay = teacherAmount > 0 ? `Преподавателю ${money(teacherAmount)}.` : 'Преподавателю не начисляется.';

  if (!subscription) {
    return `Действующего абонемента нет — занятие пойдёт разовой оплатой. ${pay}`;
  }
  if (lessonsDelta < 0 && subscription.lessons_balance <= 0) {
    return `На абонементе не осталось занятий — списать нечего. Продлите абонемент, иначе отметку сохранить не получится.`;
  }
  if (lessonsDelta < 0) {
    return `Спишется ${lessonsWord(-lessonsDelta)}, останется ${lessonsAfter}. ${pay}`;
  }
  if (makeupsDelta > 0) {
    const ttl = rules?.makeup_ttl_days ?? 30;
    return `Занятие не списывается, добавится отработка на ${ttl} дней. Остаток ${lessonsWord(lessonsAfter)}. ${pay}`;
  }
  return `Занятие не списывается, остаток ${lessonsWord(lessonsAfter)}. ${pay}`;
}

const allEffects = (subscription: MockSubscription | null, rate: number): MarkEffects =>
  MARK_ORDER.reduce((acc, mark) => {
    acc[mark] = computeEffect(mark, subscription, rate);
    return acc;
  }, {} as MarkEffects);

/* ---------- конфликты ---------- */

/** Пересечение по кабинету внутри одного дня и филиала. Считает сервер. */
function conflictsFor(lesson: MockLesson, sameDay: MockLesson[]): LessonConflict[] {
  const start = toMinutes(lesson.start);
  const end = start + lesson.duration_min;
  const result: LessonConflict[] = [];

  for (const other of sameDay) {
    if (other.id === lesson.id) continue;
    const oStart = toMinutes(other.start);
    const oEnd = oStart + other.duration_min;
    if (start >= oEnd || oStart >= end) continue;

    if (other.room_id === lesson.room_id) {
      result.push({
        kind: 'room',
        with_lesson_id: other.id,
        message: `Кабинет «${findRoom(lesson.room_id).name}» занят с ${other.start} до ${toHhMm(oEnd)}`,
      });
    } else if (other.teacher_id === lesson.teacher_id) {
      result.push({
        kind: 'teacher',
        with_lesson_id: other.id,
        message: `${findTeacher(lesson.teacher_id).name} уже занят с ${other.start} до ${toHhMm(oEnd)}`,
      });
    }
  }
  return result;
}

/* ---------- справочники ---------- */

/**
 * Филиалы преподавателя. В фикстурах нет отдельной таблицы `staff_branch`,
 * поэтому берём их из занятий — но, в отличие от прежнего сбора списков
 * в диалоге пробного, здесь это весь набор занятий, а не выбранный день.
 */
const teacherBranches = (teacherId: string): string[] => [
  ...new Set(LESSONS.filter((l) => l.teacher_id === teacherId).map((l) => l.branch_id)),
];

/* ---------- сборка ответов ---------- */

/** Статус занятия: held, как только появилась хотя бы одна отметка (контракт). */
const statusOf = (lesson: MockLesson) => ((marks.get(lesson.id)?.size ?? 0) > 0 ? 'held' : 'planned');

function toScheduleLesson(lesson: MockLesson, sameDay: MockLesson[]): ScheduleLesson {
  const start = toMinutes(lesson.start);
  // Для группы отдаём отметку первого участника: контракт не описывает,
  // как выглядит attendance_mark у группового занятия (см. README, пробелы).
  const firstMark = lesson.student_ids.map((id) => markOf(lesson.id, id)).find((m) => m !== null) ?? null;

  return {
    id: lesson.id,
    starts_at: iso(lesson.date, start),
    ends_at: iso(lesson.date, start + lesson.duration_min),
    duration_min: lesson.duration_min,
    kind: lesson.kind,
    status: statusOf(lesson),
    title: lesson.title,
    student_id: lesson.student_ids.length === 1 ? lesson.student_ids[0] : null,
    room: { id: lesson.room_id, name: findRoom(lesson.room_id).name },
    attendance_mark: firstMark,
    conflicts: conflictsFor(lesson, sameDay),
  };
}

function toParticipant(lesson: MockLesson, studentId: string): LessonParticipant {
  const student = findStudent(studentId);
  const teacher = findTeacher(lesson.teacher_id);
  const s = student.subscription;
  const mark = markOf(lesson.id, studentId);
  const attendanceId = mark ? attendanceIds.get(`${lesson.id}:${studentId}`) ?? null : null;
  return {
    student_id: student.id,
    name: student.name,
    attendance: mark,
    attendance_id: attendanceId,
    subscription: s
      ? {
          id: s.id,
          lessons_total: s.lessons_total,
          lessons_balance: s.lessons_balance,
          makeups_balance: s.makeups_balance,
          valid_until: s.valid_until,
          status: s.status,
        }
      : null,
    mark_effects: allEffects(s, teacher.rate),
    applied_effect: attendanceId && mark ? appliedEffect(attendanceId, s, teacher.rate, mark) : null,
  };
}

/**
 * Что отметка уже сделала. Остаток «после» — настоящий текущий остаток
 * абонемента, а не «баланс плюс дельта»: если после отметки было ещё
 * движение (продление, отработка), карточка занятия и карточка ученика
 * обязаны показывать одно число.
 */
function appliedEffect(
  attendanceId: string,
  subscription: MockSubscription | null,
  teacherRate: number,
  mark: AttendanceMark,
): AppliedMarkEffect | null {
  /**
   * У отметок из фикстур журнала нет: они «случились» до запуска вкладки.
   * Дельты от остатка не зависят, поэтому расчёт по правилам даёт ровно те
   * числа, которые отметка и применила, — даже сейчас, когда остаток
   * в фикстуре уже уменьшен на это занятие.
   */
  const fact = appliedFacts.get(attendanceId) ?? factFromRules(mark, subscription, teacherRate);
  if (!fact) return null;
  const lessonsAfter = subscription ? subscription.lessons_balance : null;
  const pay = fact.teacher_amount > 0 ? `Преподавателю ${money(fact.teacher_amount)}.` : 'Преподавателю не начислялось.';
  const lessons =
    fact.lessons_delta === 0
      ? 'Занятие с абонемента не списывалось.'
      : `Списано ${lessonsWord(-fact.lessons_delta)}${lessonsAfter === null ? '' : `, остаток ${lessonsAfter}`}.`;
  const makeups = fact.makeups_delta > 0 ? ` Добавлена отработка (${fact.makeups_delta}).` : '';
  return {
    mark: fact.mark,
    attendance_id: attendanceId,
    lessons_delta: fact.lessons_delta,
    makeups_delta: fact.makeups_delta,
    lessons_after: lessonsAfter,
    makeups_after: subscription ? subscription.makeups_balance : null,
    teacher_amount: fact.teacher_amount,
    summary: `${lessons}${makeups} ${pay}`,
  };
}

const factFromRules = (mark: AttendanceMark, subscription: MockSubscription | null, rate: number) => {
  const effect = computeEffect(mark, subscription, rate);
  return {
    mark,
    lessons_delta: effect.lessons_delta,
    makeups_delta: effect.makeups_delta,
    teacher_amount: effect.teacher_amount,
  };
};

/* ==========================================================================
   Этап 2: поиск, карточка, продажа абонемента, заморозка
   ========================================================================== */

const daysWord = (n: number) => `${n} ${plural(n, 'день', 'дня', 'дней')}`;

/** Полные месяцы стажа — нужны и в риске оттока, и в подписи карточки. */
function monthsSince(date: string): number {
  const [y, m, d] = date.split('-').map(Number);
  const [ty, tm, td] = MOCK_TODAY.split('-').map(Number);
  let months = (ty - y) * 12 + (tm - m);
  if (td < d) months -= 1;
  return Math.max(0, months);
}

/** Заморозка считается за календарный год — так требует контракт. */
function freezeDaysUsed(subscription: MockSubscription): number {
  const year = MOCK_TODAY.slice(0, 4);
  return subscription.holds
    .filter((h) => h.from.slice(0, 4) === year)
    .reduce((sum, h) => sum + h.days, 0);
}

/**
 * Сколько занятий попадёт в интервал. У мока нет расписания на месяцы вперёд,
 * поэтому считаем по темпу абонемента: занятий на день × дней заморозки.
 * Та же формула лежит в предпросмотре интерфейса — иначе предупреждение
 * расходилось бы с результатом.
 */
function lessonsInPeriod(subscription: MockSubscription, days: number): number {
  const periodDays = Math.max(1, daysBetween(subscription.valid_from, subscription.valid_until));
  const perDay = subscription.lessons_total / periodDays;
  return Math.min(subscription.lessons_balance, Math.round(perDay * days));
}

/** Правила в форме контракта. Внутренние имена мока совпадают с ключами базы. */
const toApiRules = (rules: MockRules) => ({
  no_show_burns: rules.no_show_burns,
  cancel_notice_hours: rules.cancel_notice_hours,
  cancel_early_effect: rules.cancel_early_effect,
  teacher_cancel_effect: rules.teacher_cancel_effect,
  makeup_ttl_days: rules.makeup_ttl_days,
  freeze_days_per_year: rules.freeze_days_per_year,
  carry_over_lessons: rules.carry_over_lessons,
  pay_teacher_on_no_show: rules.pay_teacher_on_no_show,
});

function toApiSubscription(subscription: MockSubscription): StudentSubscription {
  const used = freezeDaysUsed(subscription);
  return {
    id: subscription.id,
    plan_name: subscription.plan_name,
    lessons_total: subscription.lessons_total,
    lessons_balance: subscription.lessons_balance,
    makeups_balance: subscription.makeups_balance,
    price: subscription.price,
    lesson_price: Math.round(subscription.price / subscription.lessons_total),
    valid_from: subscription.valid_from,
    valid_until: subscription.valid_until,
    status: subscription.status,
    rules: toApiRules(subscription.rules),
    holds: subscription.holds.map((h) => ({ ...h })),
    freeze_days_used: used,
    freeze_days_left: Math.max(0, subscription.rules.freeze_days_per_year - used),
  };
}

function toApiFamily(student: MockStudent): Family | null {
  const family = findFamily(student.family_id);
  if (!family) return null;
  return {
    id: family.id,
    payer: family.payer ? { ...family.payer } : null,
    discount_pct: family.discount_pct,
    members: STUDENTS.filter((s) => s.family_id === family.id).map((s) => ({
      student_id: s.id,
      // В карточке семьи фамилия избыточна — она общая на всех
      name: s.name.split(' ')[0],
      age: s.age,
      discipline: s.discipline,
      lessons_balance: s.subscription?.lessons_balance ?? 0,
    })),
    paid_this_month: family.paid_this_month,
    debt: family.debt,
  };
}

/**
 * Риск оттока. Эвристика простая, но каждая причина — проверяемый факт
 * из журнала или из абонемента, а не оценка: администратор должен уметь
 * перепроверить её на этом же экране.
 */
function churnRisk(student: MockStudent): ChurnRisk {
  const ledger = LEDGER[student.id] ?? [];
  const since = addDays(MOCK_TODAY, -30);
  const noShows = ledger.filter((e) => e.date >= since && e.title.startsWith('Прогул')).length;

  let score = 30;
  const reasons: string[] = [];

  if (noShows > 0) {
    score += 20 * noShows;
    reasons.push(`${noShows} ${plural(noShows, 'прогул', 'прогула', 'прогулов')} за 30 дней`);
  }

  const s = student.subscription;
  if (!s) {
    score += 40;
    reasons.push('Действующего абонемента нет');
  } else {
    reasons.push(`Остаток ${lessonsWord(s.lessons_balance)} из ${s.lessons_total}`);
    if (s.lessons_balance <= 2) score += 20;
    const daysLeft = daysBetween(MOCK_TODAY, s.valid_until);
    if (daysLeft <= 7) {
      score += 15;
      reasons.push(daysLeft <= 0 ? 'Срок абонемента истёк' : `Абонемент действует ещё ${daysWord(daysLeft)}`);
    }
  }

  const months = monthsSince(student.started_on);
  if (months >= 6) {
    score -= 10;
    reasons.push(`Занимается ${months} ${plural(months, 'месяц', 'месяца', 'месяцев')} подряд`);
  }

  score = Math.max(0, Math.min(100, score));
  return { level: score >= 67 ? 'high' : score >= 34 ? 'medium' : 'low', score, reasons };
}

const findStudentOr404 = (id: string): MockStudent => {
  const student = STUDENTS.find((s) => s.id === id);
  if (!student) {
    throw new ApiError(404, 'student_not_found', 'Ученик не найден. Возможно, карточку архивировали — вернитесь к поиску.');
  }
  return student;
};

const findBySubscription = (subscriptionId: string): MockStudent => {
  const student = STUDENTS.find((s) => s.subscription?.id === subscriptionId);
  if (!student) {
    throw new ApiError(404, 'subscription_not_found', 'Абонемент не найден. Обновите карточку ученика.');
  }
  return student;
};

/**
 * Продажа абонемента. Вынесена из обработчика, потому что конверсия заявки
 * в ученика (этап 3) обязана продавать абонемент **этим же кодом**:
 * вторая реализация неизбежно разойдётся с первой в скидках или в журнале.
 */
function sellSubscriptionFor(student: MockStudent, payload: SellSubscriptionRequest): SellSubscriptionResponse {
  const plan = findPlan(payload.plan_id);
  if (!plan) {
    throw new ApiError(404, 'plan_not_found', 'Тариф не найден. Возможно, он выключен — обновите список тарифов.');
  }

  const current = student.subscription;
  if (current && payload.starts_on >= current.valid_from && payload.starts_on <= current.valid_until) {
    throw new ApiError(
      422,
      'subscription_overlaps',
      `У ${student.name} уже есть абонемент до ${current.valid_until}. Продление начинается со следующего дня — ${addDays(current.valid_until, 1)}.`,
    );
  }

  // Промокод проверяет сервер: клиент не знает ни списка кодов, ни их срока
  let promoBonus = 0;
  if (payload.promo_code) {
    if (payload.promo_code.trim().toUpperCase() !== 'RS25') {
      throw new ApiError(
        422,
        'promo_not_found',
        `Промокод «${payload.promo_code}» не найден или уже не действует. Уберите его или введите другой.`,
      );
    }
    promoBonus = 5;
  }

  const discount = Math.min(100, (payload.discount_pct ?? 0) + promoBonus);
  const charged = Math.round(plan.price * (1 - discount / 100));

  // Правила копируются из настроек школы на момент продажи, а не из старого
  // абонемента: старый жил по условиям своей покупки, новый — по текущим
  const rules: MockRules = { ...DEFAULT_RULES };
  const carried =
    payload.carry_over && current ? Math.min(current.lessons_balance, rules.carry_over_lessons) : 0;

  const subscription: MockSubscription = {
    id: `66666666-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`,
    plan_id: plan.id,
    plan_name: plan.name,
    lessons_total: plan.lessons_count + carried,
    lessons_balance: plan.lessons_count + carried,
    makeups_balance: 0,
    price: plan.price,
    discount_pct: discount,
    valid_from: payload.starts_on,
    // valid_days считаются включительно последний день: 31 день с 1 сентября
    // заканчивается 1 октября — так в примере контракта
    valid_until: addDays(payload.starts_on, plan.valid_days - 1),
    status: 'active',
    rules,
    holds: [],
  };

  const paid = payload.payment?.amount ?? 0;
  const debt = Math.max(0, charged - paid);

  const ledger = LEDGER[student.id] ?? (LEDGER[student.id] = []);
  ledger.push({
    id: nextEntryId(),
    date: payload.starts_on,
    kind: 'purchase',
    title: payload.payment
      ? `Оплата абонемента · ${PAYMENT_METHOD_LABELS[payload.payment.method]}`
      : 'Абонемент оформлен в долг',
    teacher: null,
    lessons_delta: plan.lessons_count,
    makeups_delta: 0,
    amount: paid > 0 ? paid : null,
  });
  if (carried > 0) {
    ledger.push({
      id: nextEntryId(),
      date: payload.starts_on,
      kind: 'transfer_in',
      title: 'Перенос остатка с прошлого абонемента',
      teacher: null,
      lessons_delta: carried,
      makeups_delta: 0,
      amount: null,
    });
  }

  student.subscription = subscription;

  const family = findFamily(student.family_id);
  if (family) {
    family.paid_this_month += paid;
    family.debt += debt;
  }

  return {
    subscription_id: subscription.id,
    lessons_total: subscription.lessons_total,
    lessons_balance: subscription.lessons_balance,
    valid_from: subscription.valid_from,
    valid_until: subscription.valid_until,
    price: plan.price,
    discount_pct: discount,
    charged,
    carried_over: carried,
    payment_id: payload.payment ? `88888888-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}` : null,
    debt,
  };
}

/* ==========================================================================
   Этап 3: воронка заявок
   ========================================================================== */

/**
 * «Сейчас» мок-режима — полдень дня прототипа. Час важен: от него зависят
 * и «просрочено» у напоминания на 17:00 вчера, и «2 часа назад» в карточке.
 */
const MOCK_NOW = Date.parse(`${MOCK_TODAY}T12:30:00${TZ_OFFSET}`);

const HOUR = 3600_000;

/** «2 часа», «3 дня» — длительность так, как её произносит администратор. */
function humanDuration(fromIso: string): string {
  const ms = Math.max(0, MOCK_NOW - Date.parse(fromIso));
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} ${plural(minutes, 'минута', 'минуты', 'минут')}`;
  const hours = Math.round(ms / HOUR);
  if (hours < 24) return `${hours} ${plural(hours, 'час', 'часа', 'часов')}`;
  const days = Math.round(ms / (24 * HOUR));
  return `${days} ${plural(days, 'день', 'дня', 'дней')}`;
}

/** Дата занятия-пробного в местном календаре: "2026-08-12". */
const lessonDate = (lesson: MockLesson): string => lesson.date;

function trialOf(lead: MockLead): { trial: LeadTrial | null; conflicts: LessonConflict[] } {
  if (!lead.trial_lesson_id) return { trial: null, conflicts: [] };
  const lesson = LESSONS.find((l) => l.id === lead.trial_lesson_id);
  if (!lesson) return { trial: null, conflicts: [] };

  const sameDay = LESSONS.filter((l) => l.branch_id === lesson.branch_id && l.date === lesson.date);
  const conflicts = conflictsFor(lesson, sameDay);
  return {
    trial: {
      lesson_id: lesson.id,
      starts_at: iso(lesson.date, toMinutes(lesson.start)),
      teacher: findTeacher(lesson.teacher_id).name,
      room: findRoom(lesson.room_id).name,
      status: statusOf(lesson),
      conflicts,
    },
    conflicts,
  };
}

/**
 * Флаги — единственное, что превращает доску из списка в инструмент:
 * администратор смотрит на них, а не перечитывает карточки.
 */
function flagsOf(lead: MockLead): LeadFlag[] {
  const flags: LeadFlag[] = [];
  const closed = lead.stage === 'won' || lead.stage === 'lost';

  if (!closed && lead.contact_attempts >= 2) flags.push('no_answer');
  if (!closed && lead.next_action_at && Date.parse(lead.next_action_at) < MOCK_NOW) flags.push('overdue');

  const { trial, conflicts } = trialOf(lead);
  if (trial) {
    const lesson = LESSONS.find((l) => l.id === lead.trial_lesson_id);
    if (lesson && lessonDate(lesson) === MOCK_TODAY) flags.push('trial_today');
    if (conflicts.length > 0) flags.push('trial_conflict');
  }
  return flags;
}

const lastStageChange = (lead: MockLead): string =>
  lead.history.length > 0 ? lead.history[lead.history.length - 1].at : lead.created_at;

function toBoardLead(lead: MockLead): BoardLead {
  return {
    id: lead.id,
    name: lead.name,
    student_name: lead.student_name,
    student_age: lead.student_age,
    discipline: findDiscipline(lead.discipline_id)?.name ?? null,
    source: lead.source,
    phone: lead.phone,
    created_at: lead.created_at,
    waiting_for: humanDuration(lastStageChange(lead)),
    next_action_at: lead.next_action_at,
    contact_attempts: lead.contact_attempts,
    assigned_to: lead.assigned_to ? { id: lead.assigned_to, name: findUser(lead.assigned_to)?.name ?? '—' } : null,
    trial: trialOf(lead).trial,
    flags: flagsOf(lead),
  };
}

function toLeadCard(lead: MockLead): LeadCard {
  const discipline = findDiscipline(lead.discipline_id);
  const branch = BRANCHES.find((b) => b.id === lead.branch_id);
  return {
    id: lead.id,
    name: lead.name,
    phone: lead.phone,
    student_name: lead.student_name,
    student_age: lead.student_age,
    discipline: discipline
      ? { id: discipline.id, name: discipline.name, min_age: discipline.min_age ?? undefined }
      : null,
    branch: branch ? { id: branch.id, name: branch.name } : null,
    stage: lead.stage,
    lost_reason: lead.lost_reason,
    source: lead.source,
    utm: lead.utm,
    promo_code: lead.promo_code,
    assigned_to: lead.assigned_to ? { id: lead.assigned_to, name: findUser(lead.assigned_to)?.name ?? '—' } : null,
    next_action_at: lead.next_action_at,
    contact_attempts: lead.contact_attempts,
    created_at: lead.created_at,
    comment: lead.comment,
    trial: trialOf(lead).trial,
    // Старые сверху: историю читают сверху вниз, как путь заявки
    history: lead.history.map((h) => ({
      at: h.at,
      from: h.from,
      to: h.to,
      by: h.by ? findUser(h.by)?.name ?? null : null,
    })),
    converted: { student_id: lead.student_id, person_id: lead.person_id },
  };
}

const findLeadOr404 = (id: string): MockLead => {
  const lead = findLead(id);
  if (!lead) {
    throw new ApiError(404, 'lead_not_found', 'Заявка не найдена. Возможно, её удалили — обновите доску.');
  }
  return lead;
};

/** Телефон к E.164: администратор набирает как угодно, база принимает один формат. */
function normalizePhone(raw: string): string {
  const digits = raw.replace(/\D/g, '');
  if (digits.length === 11 && digits.startsWith('8')) return `+7${digits.slice(1)}`;
  if (digits.length === 11 && digits.startsWith('7')) return `+${digits}`;
  if (digits.length === 10) return `+7${digits}`;
  return `+${digits}`;
}

const OPEN_STAGES: LeadStage[] = ['new', 'contacting', 'trial_booked', 'trial_held'];

/** Преподаватель оформленного ученика, если при конверсии его не выбрали. */
const TEACHER_FALLBACK = LESSONS[0].teacher_id;

let nowTick = 0;

/**
 * «Сейчас» мок-режима в виде ISO со смещением филиала. Каждый вызов
 * на минуту позже предыдущего: иначе несколько переходов подряд получили бы
 * одинаковое время, и история стадий читалась бы как одномоментная.
 */
const nowIso = (): string => iso(MOCK_TODAY, 12 * 60 + 30 + ++nowTick);

/** Смена стадии и запись истории — одно действие: одно без другого ломает отчёт. */
function moveStage(lead: MockLead, to: LeadStage): void {
  if (lead.stage === to) return;
  lead.history.push({ at: nowIso(), from: lead.stage, to, by: CURRENT_USER.id });
  lead.stage = to;
}

/* ---------- имитация сети ---------- */

const LATENCY = Number(import.meta.env.VITE_MOCK_LATENCY_MS ?? 320);
const delay = <T,>(value: T): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), LATENCY));

/* ==========================================================================
   Этап 5: вход в мок-режиме.

   Мок-режим нужен для разработки без бэкенда, и появление входа не имеет
   права его отключить. Роль сессии задаётся переменной `VITE_MOCK_ROLE`:
   это единственный способ посмотреть все пять ролей, не заводя пять учётных
   записей и не пересевая базу между проверками.

   Формы ответов и коды отказов повторяют живой сервер, включая разницу между
   401 и 429: если мок отвечает на неверный код так же, как на перебор,
   то ветку «подождите 15 минут» на моках проверить нечем — а забывают
   обычно именно её.
   ========================================================================== */

/** Идентификаторы того же вида, что и остальные мок-данные. */
const authUid = (n: number) => `aaaaaaaa-0000-4000-8000-${String(n).padStart(12, '0')}`;

const MOCK_TENANT: TenantRef = {
  id: authUid(10),
  name: 'RockSchool Алматы',
  // Контакты школы: в кабинете родителя оплаты нет намеренно, поэтому телефон
  // и WhatsApp — не украшение, а единственный выход из тупика «надо продлить».
  // Ни один контракт их не описывает (пробел 36) — мок отдаёт то, что должен
  // отдавать сервер, чтобы кабинет проверялся целиком.
  contacts: { phone: '+77273550101', whatsapp: '+77015550101' },
};

/** Код и пароль фиксированы: очереди уведомлений в моках нет, читать неоткуда. */
const MOCK_CODE = '123456';
const MOCK_PASSWORD = 'rockschool';
/** Столько же, сколько у живого сервера: три запроса кода за четверть часа… */
const MOCK_CODE_REQUEST_LIMIT = 3;
/** …и пять неверных кодов подряд. */
const MOCK_CODE_ATTEMPT_LIMIT = 5;

const studentIdByName = (name: string): string => STUDENTS.find((s) => s.name === name)?.id ?? '';

/** Кто входит при каждой роли. Люди — те же, что в демо-данных бэкенда. */
const MOCK_USERS: Record<Role, Omit<Me, 'tenant'>> = {
  owner: {
    user_id: authUid(94),
    name: 'Ерлан Тасмагамбетов',
    role: 'owner',
    person_id: authUid(84),
    staff_id: authUid(74),
    // Пусто — значит ВСЕ филиалы. Ровно так это читает и бэкенд.
    branch_ids: [],
    student_ids: [],
  },
  admin: {
    user_id: authUid(95),
    name: 'Асель Нурланова',
    role: 'admin',
    person_id: authUid(85),
    staff_id: authUid(75),
    branch_ids: [],
    student_ids: [],
  },
  teacher: {
    user_id: authUid(96),
    name: TEACHERS[0].name,
    role: 'teacher',
    person_id: authUid(86),
    // Свой `staff_id` — тот же, что у дорожки в расписании: по нему
    // запрашивается собственная ведомость.
    staff_id: TEACHERS[0].id,
    branch_ids: BRANCHES.map((b) => b.id),
    student_ids: STUDENTS.filter((s) => s.teacher_id === TEACHERS[0].id).map((s) => s.id),
  },
  guardian: {
    user_id: authUid(98),
    name: 'Гульнара Сагындык',
    role: 'guardian',
    person_id: authUid(88),
    staff_id: null,
    branch_ids: [],
    student_ids: [studentIdByName('Амина Сагындык'), studentIdByName('Тимур Сагындык')],
  },
  student: {
    user_id: authUid(99),
    name: 'Дмитрий Со',
    role: 'student',
    person_id: authUid(89),
    staff_id: null,
    branch_ids: [],
    student_ids: [studentIdByName('Дмитрий Со')],
  },
};

const MOCK_ROLE: Role = (() => {
  const raw = (import.meta.env.VITE_MOCK_ROLE ?? 'owner').trim();
  return raw in MOCK_USERS ? (raw as Role) : 'owner';
})();

const mockMe = (): Me => ({ ...MOCK_USERS[MOCK_ROLE], tenant: MOCK_TENANT });

/**
 * Стартовое состояние. По умолчанию сессия уже есть: заставлять разработчика
 * набирать код на каждой перезагрузке значило бы наказывать его за то, что
 * вход вообще появился. `VITE_MOCK_SIGNED_OUT=true` открывает форму входа —
 * ею же экран входа и проверяется.
 */
let mockSession: Me | null = import.meta.env.VITE_MOCK_SIGNED_OUT === 'true' ? null : mockMe();
let mockCodeRequests = 0;
let mockCodeAttempts = 0;

/** «+77015 ••• •• 18» — маска собирается из присланного, а не из найденного. */
function maskLogin(login: string): string {
  const digits = login.replace(/[^\d+]/g, '');
  return digits.length > 8 ? `${digits.slice(0, 6)} ••• •• ${digits.slice(-2)}` : '…';
}

/**
 * Кого роль вообще имеет право видеть. `null` — ограничения нет.
 * Сервер урезает выдачу сам, и мок обязан вести себя так же: иначе кабинет
 * родителя на моках показывает всю школу и выглядит рабочим.
 */
/**
 * Ставка преподавателя в карточке занятия: своя видна ему самому, чужая —
 * только владельцу (§2). Администратору ресепшена приходит `null` — «вам
 * не видно», а не «ставка ноль»: ноль читается как «работает бесплатно»
 * и однажды попадёт в разговор.
 */
function visibleRate(teacher: MockTeacher): number | null {
  const me = mockSession;
  if (!me) return null;
  if (me.role === 'owner') return teacher.rate;
  if (me.role === 'teacher' && me.staff_id === teacher.id) return teacher.rate;
  return null;
}

function visibleStudentIds(): string[] | null {
  const me = mockSession;
  if (!me || me.role === 'owner' || me.role === 'admin' || me.role === 'teacher') return null;
  return me.student_ids;
}

/* ==========================================================================
   Этап 5: кабинет родителя (docs/contract-v5.md).

   Ресурсы `/me/*` собираются СЛОЖЕНИЕМ: в ответ кладётся только то, что
   положили осознанно. Здесь нет ни долга семьи, ни риска оттока, ни ставки
   преподавателя, ни внутренних заметок — и появиться они могут лишь тогда,
   когда кто-то допишет их сюда руками, а не молча, вслед за новым полем
   в общей карточке ученика.

   Состав детей мок берёт из сессии, как и сервер: параметр, которым клиент
   называет себя сам, — это параметр, которым он однажды назовётся чужим.
   ========================================================================== */

/**
 * Порог отмены. У живого сервера это правило школы (`cancel_notice_hours`),
 * и считает его он же: на клиенте этого числа нет нигде.
 */
const ME_NOTICE_HOURS = 24;

/** Кому вообще открыт кабинет. Сотрудник видит то же самое в карточке ученика. */
function familyStudentIds(): string[] {
  const me = mockSession;
  if (!me) throw new ApiError(401, 'unauthenticated', 'Нужен вход. Запросите код на телефон.');
  if (me.role !== 'guardian' && me.role !== 'student') {
    throw new ApiError(
      403,
      'forbidden',
      'Кабинет открыт родителю и ученику. Сотрудник видит эти данные в карточке ученика.',
    );
  }
  return me.student_ids;
}

/** Чужой ребёнок — 404, а не 403: по тому же правилу, что и везде. */
function familyChildOr404(studentId: string): MockStudent {
  const ids = familyStudentIds();
  const student = ids.includes(studentId) ? STUDENTS.find((s) => s.id === studentId) : undefined;
  if (!student) {
    throw new ApiError(404, 'student_not_found', 'Ученик не найден. Проверьте, что открываете карточку своего ребёнка.');
  }
  return student;
}

/** 0 — воскресенье. Через UTC, иначе день недели поползёт от пояса браузера. */
function weekdayOf(date: string): number {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

interface MePatternSlot {
  weekday: number;
  start: string;
  duration_min: number;
  room_id: string;
}

/**
 * Недельный рисунок занятий. Фикстуры расписания покрывают два дня — 11 и 12
 * августа, — а кабинету нужна неделя вперёд и назад: без неё ни «ближайшее
 * занятие», ни заявку на перенос проверить нечем.
 */
const ME_PATTERN: Record<string, MePatternSlot[]> = {
  [studentIdByName('Амина Сагындык')]: [
    { weekday: 3, start: '11:00', duration_min: 55, room_id: ROOMS[0].id },
    { weekday: 5, start: '11:00', duration_min: 55, room_id: ROOMS[0].id },
  ],
  [studentIdByName('Тимур Сагындык')]: [
    { weekday: 2, start: '17:00', duration_min: 55, room_id: ROOMS[1].id },
    { weekday: 4, start: '17:00', duration_min: 55, room_id: ROOMS[1].id },
  ],
  [studentIdByName('Дмитрий Со')]: [{ weekday: 3, start: '20:00', duration_min: 55, room_id: ROOMS[1].id }],
};

/**
 * Отменённое занятие. Оно обязано быть в фикстурах: родитель должен увидеть
 * в расписании «отменено», а не обнаружить пустоту и приехать к закрытой двери.
 */
const ME_CANCELLED = new Set([`${studentIdByName('Тимур Сагындык')}|2026-08-13`]);

/** Идентификаторы сгенерированных занятий стабильны в пределах вкладки. */
const meLessonIds = new Map<string, string>();
let meLessonSeq = 0;
const meLessonId = (key: string): string => {
  let id = meLessonIds.get(key);
  if (!id) {
    id = `eeeeeeee-0000-4000-8000-${String(++meLessonSeq).padStart(12, '0')}`;
    meLessonIds.set(key, id);
  }
  return id;
};

/** Правило переноса целиком на сервере: до занятия больше порога и оно не проведено. */
const meCanReschedule = (startsAt: string, status: LessonStatus): boolean =>
  status === 'planned' && Date.parse(startsAt) - MOCK_NOW > ME_NOTICE_HOURS * HOUR;

/** «Амина Сагындык» → «Амина». */
const shortName = (name: string): string => name.split(' ')[0];

const meBranchOf = (branchId: string) => BRANCHES.find((b) => b.id === branchId) ?? BRANCHES[0];

/** Занятие из фикстур — то же самое, что видит администратор в сетке филиала. */
function meLessonFromFixture(lesson: MockLesson, student: MockStudent): MeScheduleLesson {
  const startMin = toMinutes(lesson.start);
  const startsAt = iso(lesson.date, startMin);
  const attendance = markOf(lesson.id, student.id);
  const status: LessonStatus = attendance ? 'held' : 'planned';
  return {
    lesson_id: lesson.id,
    student_id: student.id,
    // Короткое имя: в списке недели родитель ищет глазами «Амина», а не
    // «Сагындык А.» — фамилию он и так знает, она у него своя.
    student_name: shortName(student.name),
    starts_at: startsAt,
    ends_at: iso(lesson.date, startMin + lesson.duration_min),
    duration_min: lesson.duration_min,
    teacher: findTeacher(lesson.teacher_id).name,
    branch: meBranchOf(lesson.branch_id).name,
    room: findRoom(lesson.room_id).name,
    kind: lesson.kind,
    status,
    attendance,
    can_request_reschedule: meCanReschedule(startsAt, status),
    reschedule_request: meRequestFor(lesson.id),
  };
}

function meLessonFromPattern(student: MockStudent, date: string, slot: MePatternSlot): MeScheduleLesson {
  const startMin = toMinutes(slot.start);
  const startsAt = iso(date, startMin);
  const cancelled = ME_CANCELLED.has(`${student.id}|${date}`);
  // Прошедшее занятие уже отмечено, будущее — ещё нет. Отметку ставит
  // преподаватель, поэтому у сегодняшнего прошедшего её может и не быть.
  const attendance: AttendanceMark | null = cancelled ? 'cancelled_teacher' : Date.parse(startsAt) < MOCK_NOW ? 'came' : null;
  const status: LessonStatus = cancelled ? 'cancelled' : attendance ? 'held' : 'planned';
  const id = meLessonId(`${student.id}|${date}|${slot.start}`);
  return {
    lesson_id: id,
    student_id: student.id,
    student_name: shortName(student.name),
    starts_at: startsAt,
    ends_at: iso(date, startMin + slot.duration_min),
    duration_min: slot.duration_min,
    teacher: findTeacher(student.teacher_id).name,
    branch: meBranchOf(student.branch_id).name,
    room: findRoom(slot.room_id).name,
    kind: 'regular',
    status,
    attendance,
    can_request_reschedule: meCanReschedule(startsAt, status),
    reschedule_request: meRequestFor(id),
  };
}

/** Занятия всех своих детей за период, по времени. Родителю нужно «когда вести кого». */
function meLessons(ids: string[], from: string, to: string): MeScheduleLesson[] {
  const out: MeScheduleLesson[] = [];
  if (from > to) return out;
  for (const studentId of ids) {
    const student = STUDENTS.find((s) => s.id === studentId);
    if (!student) continue;
    const covered = new Set<string>();
    for (const lesson of LESSONS) {
      if (!lesson.student_ids.includes(studentId) || lesson.date < from || lesson.date > to) continue;
      covered.add(lesson.date);
      out.push(meLessonFromFixture(lesson, student));
    }
    for (let date = from; date <= to; date = addDays(date, 1)) {
      if (covered.has(date)) continue;
      for (const slot of ME_PATTERN[studentId] ?? []) {
        if (weekdayOf(date) === slot.weekday) out.push(meLessonFromPattern(student, date, slot));
      }
    }
  }
  return out.sort((a, b) => a.starts_at.localeCompare(b.starts_at));
}

function toMeSubscription(subscription: MockSubscription | null): MeSubscription | null {
  if (!subscription) return null;
  return {
    lessons_balance: subscription.lessons_balance,
    lessons_total: subscription.lessons_total,
    makeups_balance: subscription.makeups_balance,
    valid_until: subscription.valid_until,
    status: subscription.status,
    // Порог «мало» считает сервер: это правило школы, а не число в интерфейсе.
    ends_soon: subscription.lessons_balance <= 2 || daysBetween(MOCK_TODAY, subscription.valid_until) <= 7,
  };
}

function toMeChild(student: MockStudent): MeChild {
  const branch = meBranchOf(student.branch_id);
  const next = meLessons([student.id], MOCK_TODAY, addDays(MOCK_TODAY, 30)).find(
    (lesson) => lesson.status === 'planned' && Date.parse(lesson.starts_at) > MOCK_NOW,
  );
  return {
    student_id: student.id,
    // Родитель зовёт ребёнка по имени, а не по фамилии с инициалами.
    name: shortName(student.name),
    full_name: student.name,
    age: student.age,
    discipline: student.discipline,
    teacher: { name: findTeacher(student.teacher_id).name },
    branch: { name: branch.name, address: branch.name },
    subscription: toMeSubscription(student.subscription),
    next_lesson: next ? { lesson_id: next.lesson_id, starts_at: next.starts_at, room: next.room } : null,
  };
}

/** Отметка восстанавливается из формулировки журнала — в фикстурах её нет отдельно. */
function meAttendanceOf(title: string): AttendanceMark {
  if (title.includes('Прогул')) return 'no_show';
  if (title.includes('Отмена')) return 'cancelled_early';
  if (title.includes('Опозд')) return 'late';
  return 'came';
}

/** Во сколько было занятие в этот день — из фикстур, иначе из недельного рисунка. */
function meStartsAt(student: MockStudent, date: string): string | null {
  const fixture = LESSONS.find((l) => l.date === date && l.student_ids.includes(student.id));
  if (fixture) return iso(date, toMinutes(fixture.start));
  const slot = (ME_PATTERN[student.id] ?? []).find((s) => s.weekday === weekdayOf(date));
  return slot ? iso(date, toMinutes(slot.start)) : null;
}

/**
 * История посещений: движение абонемента вместе с занятием и заметкой.
 * Родитель видит, за что списано, а не только сколько осталось.
 *
 * Внутренние заметки отсеиваются здесь, потому что мок — это и хранилище,
 * и выборка сразу. На сервере условие видимости обязано стоять В ЗАПРОСЕ:
 * фильтр поверх ответа рано или поздно забудут при добавлении поля.
 */
function meHistory(student: MockStudent): MeHistoryEntry[] {
  const rows = new Map<string, MeHistoryEntry>();
  for (const entry of LEDGER[student.id] ?? []) {
    if (entry.kind !== 'charge' && entry.kind !== 'makeup_grant' && entry.kind !== 'makeup_use') continue;
    rows.set(entry.date, {
      date: entry.date,
      starts_at: meStartsAt(student, entry.date),
      // Формулировку берём из журнала: пересказ своими словами завёл бы
      // второй источник правды рядом с движением абонемента.
      title: entry.title,
      attendance: meAttendanceOf(entry.title),
      lessons_delta: entry.lessons_delta,
      makeups_delta: entry.makeups_delta,
      note: null,
    });
  }
  for (const note of NOTES[student.id] ?? []) {
    if (note.internal) continue;
    const visible: MeNote = { body: note.body, homework: note.homework || null, tags: note.tags };
    const row = rows.get(note.date);
    if (row) {
      row.note = visible;
      continue;
    }
    // Заметка есть, а движения нет — занятие оплачено прошлым абонементом.
    // Выбрасывать её нельзя: заметка и есть то, ради чего кабинет открывают.
    rows.set(note.date, {
      date: note.date,
      starts_at: meStartsAt(student, note.date),
      title: 'Занятие проведено',
      attendance: 'came',
      lessons_delta: 0,
      makeups_delta: 0,
      note: visible,
    });
  }
  return [...rows.values()].sort((a, b) => b.date.localeCompare(a.date));
}

/** Репертуар собирается из тегов заметок — тех же, что видит родитель. */
function meProgress(student: MockStudent, history: MeHistoryEntry[]): MeProgress {
  const repertoire: string[] = [];
  for (const entry of history) {
    for (const tag of entry.note?.tags ?? []) if (!repertoire.includes(tag)) repertoire.push(tag);
  }
  return {
    lessons_attended: history.filter((e) => e.attendance === 'came' || e.attendance === 'late').length,
    months: monthsSince(student.started_on),
    repertoire,
  };
}

const meMakeups = (student: MockStudent): MeMakeup[] =>
  (MAKEUPS[student.id] ?? [])
    .filter((m) => m.used_at === null)
    .map((m) => ({ expires_on: m.expires_on, days_left: daysBetween(MOCK_TODAY, m.expires_on) }));

/**
 * Заявки живут в памяти вкладки: повторная на то же занятие обязана дать 409.
 * Занятие носит заявку с собой (`reschedule_request`), поэтому «уже отправлена»
 * переживает перезагрузку — иначе родитель нажал бы второй раз и решил,
 * что первая заявка потерялась.
 */
const meRescheduleRequests = new Map<string, { request_id: string; status: string }>();
const meRequestFor = (lessonId: string) => meRescheduleRequests.get(lessonId) ?? null;
let meRequestSeq = 0;
const meRequestId = (): string => `dddddddd-0000-4000-8000-${String(++meRequestSeq).padStart(12, '0')}`;

/* ---------- API ---------- */

export const mockApi = {
  /* ---------- этап 5: вход ---------- */

  requestCode: async (tenant: string, login: string): Promise<CodeSent> => {
    if (!tenant.trim()) {
      throw new ApiError(404, 'unknown_tenant', 'Школа не найдена. Проверьте адрес, по которому открыт кабинет.');
    }
    mockCodeRequests += 1;
    if (mockCodeRequests > MOCK_CODE_REQUEST_LIMIT) {
      throw new ApiError(429, 'too_many_attempts', 'Слишком много запросов кода с этого адреса. Подождите 15 минут.');
    }
    // 202 на любой номер, включая несуществующий.
    return delay({
      sent: true,
      to: maskLogin(login),
      expires_in: 300,
      message: 'Если такая учётная запись есть, код придёт в течение минуты.',
    });
  },

  login: async (payload: LoginRequest): Promise<LoggedIn> => {
    if (Boolean(payload.code) === Boolean(payload.password)) {
      throw new ApiError(400, 'bad_credentials_form', 'Пришлите либо одноразовый код, либо пароль — что-то одно.');
    }
    if (mockCodeAttempts >= MOCK_CODE_ATTEMPT_LIMIT) {
      throw new ApiError(
        429,
        'too_many_attempts',
        'Слишком много неверных кодов. Подождите 15 минут и запросите новый.',
      );
    }
    const ok = payload.code ? payload.code === MOCK_CODE : payload.password === MOCK_PASSWORD;
    if (!ok) {
      mockCodeAttempts += 1;
      // Тот же код и тот же текст, что у живого сервера: неверный код,
      // неверный пароль и несуществующий телефон отвечают одинаково.
      throw new ApiError(401, 'bad_credentials', 'Не подошло. Проверьте номер и код — или запросите новый код.');
    }
    mockCodeAttempts = 0;
    mockCodeRequests = 0;
    mockSession = mockMe();
    return delay({
      user: mockSession,
      expires_at: new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString(),
    });
  },

  logout: async (everywhere = false): Promise<LoggedOut> => {
    mockSession = null;
    mockCodeAttempts = 0;
    mockCodeRequests = 0;
    return delay({ ok: true, message: everywhere ? 'Вы вышли из всех сеансов.' : 'Вы вышли.' });
  },

  me: async (): Promise<Me> => {
    if (!mockSession) {
      throw new ApiError(401, 'unauthenticated', 'Нужен вход. Запросите код на телефон.');
    }
    return delay(mockSession);
  },

  branches: (): Promise<Branch[]> => delay(BRANCHES.map((b) => ({ ...b }))),

  // async — чтобы отказ уходил отклонённым промисом, а не синхронным исключением
  schedule: async (branchId: string, date: string): Promise<ScheduleResponse> => {
    const branch = BRANCHES.find((b) => b.id === branchId);
    if (!branch) {
      throw new ApiError(404, 'branch_not_found', 'Филиал не найден. Обновите страницу и выберите филиал заново.');
    }

    const sameDay = LESSONS.filter((l) => l.branch_id === branchId && l.date === date);
    const sorted = [...sameDay].sort((a, b) => toMinutes(a.start) - toMinutes(b.start));

    // Дорожки — только преподаватели с занятиями в этот день, в порядке первого занятия
    const tracks: ScheduleTrack[] = [];
    for (const lesson of sorted) {
      const teacher = findTeacher(lesson.teacher_id);
      let track = tracks.find((t) => t.teacher.id === teacher.id);
      if (!track) {
        track = {
          teacher: {
            id: teacher.id,
            name: teacher.name,
            disciplines: teacher.disciplines,
            color: teacher.color,
          },
          lessons: [],
        };
        tracks.push(track);
      }
      track.lessons.push(toScheduleLesson(lesson, sameDay));
    }

    const conflictLessons = tracks.flatMap((t) => t.lessons).filter((l) => l.conflicts.length > 0);
    const openMinutes = toMinutes(branch.closes_at) - toMinutes(branch.opens_at);
    const roomsInBranch = new Set(sameDay.map((l) => l.room_id)).size || 1;
    const busyMinutes = sameDay.reduce((sum, l) => sum + l.duration_min, 0);

    return delay({
      date,
      branch: { id: branch.id, name: branch.name, opens_at: branch.opens_at, closes_at: branch.closes_at },
      tracks,
      summary: {
        lessons: sameDay.length,
        trials: sameDay.filter((l) => l.kind === 'trial').length,
        // Каждая пара пересечений даёт конфликт у обоих занятий — считаем пары
        conflicts: Math.floor(conflictLessons.length / 2),
        room_utilization_pct: Math.round((busyMinutes / (openMinutes * roomsInBranch)) * 100),
      },
    });
  },

  lesson: async (lessonId: string): Promise<LessonCard> => {
    const lesson = LESSONS.find((l) => l.id === lessonId);
    if (!lesson) {
      throw new ApiError(404, 'lesson_not_found', 'Занятие не найдено. Возможно, его удалили — обновите расписание.');
    }
    const teacher = findTeacher(lesson.teacher_id);
    const start = toMinutes(lesson.start);

    return delay({
      id: lesson.id,
      starts_at: iso(lesson.date, start),
      ends_at: iso(lesson.date, start + lesson.duration_min),
      duration_min: lesson.duration_min,
      kind: lesson.kind,
      status: statusOf(lesson),
      room: { id: lesson.room_id, name: findRoom(lesson.room_id).name },
      teacher: { id: teacher.id, name: teacher.name, rate: visibleRate(teacher) },
      participants: lesson.student_ids.map((id) => toParticipant(lesson, id)),
      note: lesson.note,
    });
  },

  markAttendance: async (lessonId: string, payload: AttendanceRequest): Promise<AttendanceResponse> => {
    const lesson = LESSONS.find((l) => l.id === lessonId);
    if (!lesson) {
      throw new ApiError(404, 'lesson_not_found', 'Занятие не найдено. Обновите расписание.');
    }
    const student = STUDENTS.find((s) => s.id === payload.student_id);
    if (!student || !lesson.student_ids.includes(student.id)) {
      throw new ApiError(404, 'student_not_found', 'Ученик не найден среди участников занятия.');
    }
    if (markOf(lessonId, student.id)) {
      throw new ApiError(
        409,
        'attendance_exists',
        `${student.name} уже отмечен на этом занятии. Чтобы исправить, отмените отметку.`,
      );
    }

    const teacher = findTeacher(lesson.teacher_id);
    const subscription = student.subscription;
    const effect = computeEffect(payload.mark, subscription, teacher.rate);

    if (effect.lessons_delta < 0 && subscription && subscription.lessons_balance <= 0) {
      throw new ApiError(
        422,
        'subscription_empty',
        `На абонементе ${student.name} не осталось занятий. Продайте продление и отметьте занятие снова.`,
      );
    }

    // Применяем ровно то, что показал предпросмотр
    if (subscription) {
      subscription.lessons_balance += effect.lessons_delta;
      subscription.makeups_balance += effect.makeups_delta;
    }
    const perLesson = marks.get(lessonId) ?? new Map<string, AttendanceMark>();
    perLesson.set(student.id, payload.mark);
    marks.set(lessonId, perLesson);

    const attendanceId = newAttendanceId(lessonId, student.id);
    // Записываем факт, а не ссылку на правила: карточка занятия после
    // перезагрузки обязана показать то, что было применено.
    appliedFacts.set(attendanceId, {
      mark: payload.mark,
      lessons_delta: effect.lessons_delta,
      makeups_delta: effect.makeups_delta,
      teacher_amount: effect.teacher_amount,
    });

    // Отметка сразу попадает в ведомость: зарплата начисляется от каждого
    // проведённого занятия, и экран денег обязан отражать смену тут же.
    ACCRUALS.push({
      id: nextAccrualId(),
      date: lesson.date,
      start: lesson.start,
      teacher_id: teacher.id,
      branch_id: lesson.branch_id,
      student_name: student.name,
      discipline: student.discipline,
      duration_min: lesson.duration_min,
      mark: payload.mark,
      rate: teacher.rate,
      amount: effect.teacher_amount,
      kind: 'lesson',
      period_id: null,
    });

    const alerts =
      subscription && effect.lessons_delta < 0 && effect.lessons_after <= 2
        ? [
            {
              kind: 'subscription_low',
              message: `Осталось ${lessonsWord(effect.lessons_after)} — пора предложить продление`,
            },
          ]
        : [];

    return delay({
      attendance_id: attendanceId,
      mark: payload.mark,
      applied: {
        lessons_delta: effect.lessons_delta,
        lessons_after: effect.lessons_after,
        makeups_delta: effect.makeups_delta,
        teacher_amount: effect.teacher_amount,
        teacher_id: teacher.id,
        subscription_id: subscription?.id ?? null,
      },
      lesson_status: 'held',
      alerts,
    });
  },

  /**
   * Отмена ошибочной отметки. Настоящий бэкенд журнал не правит, а добавляет
   * компенсирующие записи (`refund` в абонемент, `correction` в зарплату);
   * мок повторяет только видимый результат: остаток возвращается, начисление
   * снимается, занятие без отметок снова становится запланированным.
   */
  revokeAttendance: async (attendanceId: string): Promise<AttendanceRevokeResponse> => {
    const owner = attendanceOwners.get(attendanceId);
    if (!owner) {
      throw new ApiError(404, 'attendance_not_found', 'Отметка не найдена. Обновите расписание и повторите.');
    }
    const mark = markOf(owner.lessonId, owner.studentId);
    if (revokedAttendance.has(attendanceId) || !mark) {
      throw new ApiError(409, 'already_revoked', 'Эта отметка уже отменена. Обновите карточку занятия.');
    }

    const lesson = LESSONS.find((l) => l.id === owner.lessonId)!;
    const student = findStudent(owner.studentId);
    const teacher = findTeacher(lesson.teacher_id);
    const subscription = student.subscription;

    // Гасим ровно то, что списала отметка. Дельты от остатка не зависят,
    // поэтому расчёт по тем же правилам возвращает те же числа, что списывал.
    const effect = computeEffect(mark, subscription, teacher.rate);
    if (subscription) {
      subscription.lessons_balance -= effect.lessons_delta;
      subscription.makeups_balance -= effect.makeups_delta;
    }

    // Отметка снимается целиком, а не помечается отозванной: сразу после
    // отмены ученик обязан снова стать доступным для отметки. Живая база
    // это пока запрещает уникальным индексом — задача #2 бэкенда как раз
    // про это, и мок описывает состояние после её исправления.
    const perLesson = marks.get(owner.lessonId);
    perLesson?.delete(owner.studentId);
    if (perLesson && perLesson.size === 0) marks.delete(owner.lessonId);
    attendanceIds.delete(`${owner.lessonId}:${owner.studentId}`);
    appliedFacts.delete(attendanceId);
    revokedAttendance.add(attendanceId);

    // Снятое начисление приезжает в ведомость отдельной строкой-корректировкой:
    // журнал не правится задним числом, он дописывается.
    if (effect.teacher_amount > 0) {
      ACCRUALS.push({
        id: nextAccrualId(),
        date: lesson.date,
        start: '00:00',
        teacher_id: teacher.id,
        branch_id: lesson.branch_id,
        student_name: student.name,
        discipline: student.discipline,
        duration_min: 0,
        mark: null,
        rate: effect.teacher_amount,
        amount: -effect.teacher_amount,
        kind: 'correction',
        period_id: null,
      });
    }

    return delay({
      attendance_id: attendanceId,
      mark,
      revoked_at: nowIso(),
      reverted: {
        lessons_delta: -effect.lessons_delta,
        lessons_after: subscription ? subscription.lessons_balance : null,
        makeups_delta: -effect.makeups_delta,
        makeups_after: subscription ? subscription.makeups_balance : null,
        teacher_amount: -effect.teacher_amount,
        teacher_id: teacher.id,
        subscription_id: subscription?.id ?? null,
      },
      lesson_status: statusOf(lesson),
    });
  },

  /* ---------- справочники (задача #1) ---------- */

  teachers: async (branchId?: string | null): Promise<DirectoryTeacher[]> =>
    delay(
      TEACHERS.filter((t) => {
        const branches = teacherBranches(t.id);
        // Преподаватель без единого занятия считается доступным везде:
        // справочник обязан показывать и тех, у кого расписание пустое, —
        // ради этого он и нужен вместо среза дня.
        return !branchId || branches.length === 0 || branches.includes(branchId);
      }).map((t) => ({
        id: t.id,
        name: t.name,
        disciplines: [...t.disciplines],
        branch_ids: teacherBranches(t.id),
        color: t.color,
        rate: t.rate,
      })),
    ),

  rooms: async (branchId?: string | null): Promise<DirectoryRoom[]> =>
    delay(
      ROOMS.filter((r) => !branchId || r.branch_id === branchId).map((r) => ({
        id: r.id,
        name: r.name,
        branch_id: r.branch_id,
        features: { ...r.features },
      })),
    ),

  disciplines: async (): Promise<Discipline[]> =>
    delay(
      DISCIPLINES.map((d) => ({ id: d.id, name: d.name, min_age: d.min_age, room_reqs: { ...d.room_reqs } })),
    ),

  /* ---------- этап 2 ---------- */

  students: async (query: string, branchId?: string | null): Promise<StudentSearchItem[]> => {
    const needle = query.trim().toLowerCase();
    const visible = visibleStudentIds();
    // Пустой запрос у родителя осмыслен: ему возвращают его же детей,
    // и сервер ведёт себя так же. У сотрудника пустой запрос вернул бы
    // половину школы, поэтому у него список остаётся пустым.
    if (!needle && visible === null) return delay([]);
    // Телефон ищем по цифрам: администратор набирает его как угодно —
    // с +7, с 8, со скобками, — а совпасть должно всё равно
    const digits = needle.replace(/\D/g, '');

    const found = STUDENTS.filter((student) => {
      // Ограничение по роли накладывается до поиска, а не на результат:
      // иначе `limit` отрезал бы детей родителя раньше, чем до них дойдёт
      // очередь, и кабинет показал бы пустой список.
      if (visible !== null && !visible.includes(student.id)) return false;
      if (branchId && student.branch_id !== branchId) return false;
      if (!needle) return true;
      if (student.name.toLowerCase().includes(needle)) return true;
      const payer = findFamily(student.family_id)?.payer;
      if (!payer) return false;
      if (payer.name.toLowerCase().includes(needle)) return true;
      return digits.length >= 3 && payer.phone.replace(/\D/g, '').includes(digits);
    }).slice(0, 20);

    return delay(
      found.map((student) => ({
        id: student.id,
        name: student.name,
        age: student.age,
        discipline: student.discipline,
        teacher: findTeacher(student.teacher_id).name,
        branch: BRANCHES.find((b) => b.id === student.branch_id)?.name ?? '—',
        subscription: student.subscription
          ? {
              lessons_balance: student.subscription.lessons_balance,
              lessons_total: student.subscription.lessons_total,
              valid_until: student.subscription.valid_until,
              status: student.subscription.status,
            }
          : null,
        payer: findFamily(student.family_id)?.payer ?? null,
      })),
    );
  },

  student: async (studentId: string): Promise<StudentCard> => {
    const visible = visibleStudentIds();
    // Чужой ребёнок отвечает 404, а не 403: 403 подтверждал бы, что такой
    // ученик в школе есть, и перебором идентификаторов её можно было бы
    // пересчитать целиком. Живой сервер отвечает так же.
    if (visible !== null && !visible.includes(studentId)) {
      throw new ApiError(404, 'student_not_found', 'Ученик не найден.');
    }
    const student = findStudentOr404(studentId);
    const ledger = LEDGER[student.id] ?? [];

    return delay({
      id: student.id,
      name: student.name,
      age: student.age,
      discipline: student.discipline,
      teacher: findTeacher(student.teacher_id).name,
      branch: BRANCHES.find((b) => b.id === student.branch_id)?.name ?? '—',
      started_on: student.started_on,
      status: student.status,
      family: toApiFamily(student),
      subscription: student.subscription ? toApiSubscription(student.subscription) : null,
      makeups: (MAKEUPS[student.id] ?? []).map((m) => ({
        id: m.id,
        granted_for: m.granted_for,
        expires_on: m.expires_on,
        days_left: daysBetween(MOCK_TODAY, m.expires_on),
        used_at: m.used_at,
      })),
      // Новые сверху: журнал читают, чтобы понять, что случилось последним
      ledger: [...ledger].sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id),
      notes: [...(NOTES[student.id] ?? [])].sort((a, b) => b.date.localeCompare(a.date)),
      churn_risk: churnRisk(student),
    });
  },

  plans: async (): Promise<Plan[]> => delay(PLANS.map((p) => ({ ...p }))),

  sellSubscription: async (
    studentId: string,
    payload: SellSubscriptionRequest,
  ): Promise<SellSubscriptionResponse> => delay(sellSubscriptionFor(findStudentOr404(studentId), payload)),

  createHold: async (subscriptionId: string, payload: HoldRequest): Promise<HoldResponse> => {
    const student = findBySubscription(subscriptionId);
    const subscription = student.subscription as MockSubscription;

    const days = daysBetween(payload.from, payload.to);
    if (days <= 0) {
      throw new ApiError(400, 'bad_period', 'Конец заморозки должен быть позже начала. Проверьте даты.');
    }
    if (payload.from < MOCK_TODAY) {
      throw new ApiError(
        422,
        'hold_in_past',
        `Заморозка не может начинаться в прошлом. Выберите дату не раньше ${MOCK_TODAY}.`,
      );
    }
    // База запрещает пересечение ограничением исключения — переводим в текст
    const overlap = subscription.holds.find((h) => payload.from < h.to && h.from < payload.to);
    if (overlap) {
      throw new ApiError(
        409,
        'hold_overlaps',
        `Интервал пересекается с заморозкой ${overlap.from} — ${overlap.to}. Выберите другие даты или снимите ту заморозку.`,
      );
    }
    const used = freezeDaysUsed(subscription);
    const left = Math.max(0, subscription.rules.freeze_days_per_year - used);
    if (days > left) {
      throw new ApiError(
        422,
        'freeze_limit_exceeded',
        `Лимит заморозки — ${daysWord(subscription.rules.freeze_days_per_year)} в год, осталось ${daysWord(left)}. Сократите интервал.`,
      );
    }

    const hold: MockHold = {
      id: `99999999-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`,
      from: payload.from,
      to: payload.to,
      days,
      reason: payload.reason || null,
    };
    const before = subscription.valid_until;
    const cancelled = lessonsInPeriod(subscription, days);

    subscription.holds.push(hold);
    subscription.valid_until = addDays(before, days);

    return delay({
      hold_id: hold.id,
      days,
      valid_until_before: before,
      valid_until_after: subscription.valid_until,
      lessons_cancelled: cancelled,
      freeze_days_left: Math.max(0, subscription.rules.freeze_days_per_year - used - days),
    });
  },

  releaseHold: async (subscriptionId: string, holdId: string): Promise<HoldReleaseResponse> => {
    const student = findBySubscription(subscriptionId);
    const subscription = student.subscription as MockSubscription;
    const hold = subscription.holds.find((h) => h.id === holdId);
    if (!hold) {
      throw new ApiError(404, 'hold_not_found', 'Заморозка не найдена. Обновите карточку ученика.');
    }

    const before = subscription.valid_until;
    subscription.holds = subscription.holds.filter((h) => h.id !== holdId);
    subscription.valid_until = addDays(before, -hold.days);
    const cancelled = lessonsInPeriod(subscription, hold.days);

    return delay({
      hold_id: holdId,
      days: hold.days,
      valid_until_before: before,
      valid_until_after: subscription.valid_until,
      lessons_cancelled: cancelled,
      freeze_days_left: Math.max(0, subscription.rules.freeze_days_per_year - freezeDaysUsed(subscription)),
      message: `Заморозка снята, абонемент снова действует до ${subscription.valid_until}. Отменённые занятия (${cancelled}) не восстанавливаются — поставьте их заново в расписании.`,
    });
  },

  /* ---------- этап 3 ---------- */

  leads: async (
    filters: { stage?: string; source?: string; assigned_to?: string; branch_id?: string | null } = {},
  ): Promise<LeadsBoard> => {
    const visible = LEADS.filter((lead) => {
      if (filters.branch_id && lead.branch_id !== filters.branch_id) return false;
      if (filters.source && lead.source !== filters.source) return false;
      if (filters.assigned_to && lead.assigned_to !== filters.assigned_to) return false;
      if (filters.stage && lead.stage !== filters.stage) return false;
      return true;
    });

    const columns: BoardColumn[] = [...STAGE_ORDER, 'lost' as LeadStage].map((stage) => {
      const leads = visible
        .filter((lead) => lead.stage === stage)
        // Внутри колонки сверху то, что ждёт дольше: это и есть очередь работы
        .sort((a, b) => Date.parse(lastStageChange(a)) - Date.parse(lastStageChange(b)))
        .map(toBoardLead);
      return { stage, title: STAGE_LABELS[stage], count: leads.length, leads };
    });

    const open = visible.filter((l) => OPEN_STAGES.includes(l.stage));
    const overdue = open.filter((l) => flagsOf(l).includes('overdue')).length;
    // Конверсия считается по истории, а не по текущим стадиям: выигранная
    // заявка уже не лежит в колонке «пробный проведён»
    const withTrial = LEADS.filter((l) => l.history.some((h) => h.to === 'trial_booked'));
    const won = LEADS.filter((l) => l.stage === 'won');
    const conversion = withTrial.length > 0 ? Math.round((won.length / withTrial.length) * 100) : 0;

    return delay({
      columns,
      summary: {
        total: visible.length,
        overdue,
        conversion_trial_to_won_pct: conversion,
        avg_days_to_won: Math.round(avgDaysToWon(won) * 10) / 10,
      },
    });
  },

  lead: async (leadId: string): Promise<LeadCard> => delay(toLeadCard(findLeadOr404(leadId))),

  createLead: async (payload: CreateLeadRequest): Promise<LeadCard> => {
    const phone = normalizePhone(payload.phone ?? '');
    if (!/^\+[1-9][0-9]{7,14}$/.test(phone)) {
      throw new ApiError(400, 'bad_phone', 'Телефон не похож на номер. Введите его в любом формате, например +7 701 555 00 03.');
    }
    // Дубль в незакрытых стадиях запрещён: два лида на одного человека
    // заставят воронку врать о конверсии
    const existing = LEADS.find((l) => l.phone === phone && OPEN_STAGES.includes(l.stage));
    if (existing) {
      throw new ApiError(
        409,
        'lead_duplicate',
        `На номер ${phone} уже есть открытая заявка: ${existing.name} · ${STAGE_LABELS[existing.stage]}. Откройте её вместо создания второй.`,
        { lead_id: existing.id },
      );
    }

    const created = nowIso();
    const lead: MockLead = {
      id: nextLeadId(),
      name: payload.name.trim(),
      phone,
      student_name: payload.student_name?.trim() || payload.name.trim(),
      student_age: payload.student_age ?? null,
      discipline_id: payload.discipline_id ?? null,
      branch_id: payload.branch_id ?? BRANCH_AF,
      stage: 'new',
      lost_reason: null,
      source: payload.source,
      utm: {},
      promo_code: null,
      external_id: null,
      assigned_to: CURRENT_USER.id,
      next_action_at: null,
      contact_attempts: 0,
      created_at: created,
      comment: payload.comment?.trim() || null,
      trial_lesson_id: null,
      history: [{ at: created, from: null, to: 'new', by: CURRENT_USER.id }],
      student_id: null,
      person_id: null,
    };
    LEADS.push(lead);
    return delay(toLeadCard(lead));
  },

  patchLead: async (leadId: string, payload: PatchLeadRequest): Promise<LeadCard> => {
    const lead = findLeadOr404(leadId);

    if (payload.stage && payload.stage !== lead.stage) {
      if (payload.stage === 'won') {
        throw new ApiError(
          422,
          'won_requires_conversion',
          'Вручную перевести в «Абонемент куплен» нельзя: выигрыш ставит конверсия. Нажмите «Оформить ученика» — заявка перейдёт сама.',
        );
      }
      if (payload.stage === 'lost' && !payload.lost_reason && !lead.lost_reason) {
        throw new ApiError(
          422,
          'lost_reason_required',
          'Отказ без причины не сохранится: без неё воронка не покажет, что чинить. Выберите причину.',
        );
      }
      if (payload.stage === 'trial_booked' && !lead.trial_lesson_id) {
        throw new ApiError(
          422,
          'trial_required',
          'Стадия «Пробный назначен» ставится назначением пробного урока: нажмите «Назначить пробный», чтобы занятие попало в расписание.',
        );
      }
      if (payload.lost_reason !== undefined) lead.lost_reason = payload.lost_reason;
      moveStage(lead, payload.stage);
      // Выход из отказа очищает причину: она относится к закрытой заявке
      if (lead.stage !== 'lost') lead.lost_reason = null;
    } else if (payload.lost_reason !== undefined) {
      lead.lost_reason = payload.lost_reason;
    }

    if (payload.assigned_to !== undefined) lead.assigned_to = payload.assigned_to;
    if (payload.next_action_at !== undefined) lead.next_action_at = payload.next_action_at;
    if (payload.contact_attempts !== undefined) lead.contact_attempts = Math.max(0, payload.contact_attempts);

    return delay(toLeadCard(lead));
  },

  bookTrial: async (leadId: string, payload: TrialRequest): Promise<TrialResponse> => {
    const lead = findLeadOr404(leadId);
    const teacher = findTeacher(payload.teacher_id);
    const room = ROOMS.find((r) => r.id === payload.room_id);
    if (!teacher || !room) {
      throw new ApiError(404, 'slot_not_found', 'Преподаватель или кабинет не найден. Обновите страницу.');
    }

    const date = payload.starts_at.slice(0, 10);
    const startMinutes = Number(payload.starts_at.slice(11, 13)) * 60 + Number(payload.starts_at.slice(14, 16));
    const endMinutes = startMinutes + payload.duration_min;

    // Кабинет обязан подходить направлению: барабаны без установки не поставить
    const discipline = findDiscipline(lead.discipline_id);
    const missing = Object.keys(discipline?.room_reqs ?? {}).filter((req) => !room.features[req]);
    if (missing.length > 0) {
      throw new ApiError(
        422,
        'room_unsuitable',
        `Кабинет «${room.name}» не подходит направлению «${discipline?.name}»: нет ${missing.join(', ')}. Выберите другой кабинет.`,
      );
    }

    const sameDay = LESSONS.filter((l) => l.branch_id === room.branch_id && l.date === date);
    const busy = sameDay.find((l) => {
      const s = toMinutes(l.start);
      const e = s + l.duration_min;
      if (startMinutes >= e || s >= endMinutes) return false;
      return l.room_id === room.id || l.teacher_id === teacher.id;
    });
    if (busy && !payload.overbook_ack) {
      const who = busy.room_id === room.id ? `Кабинет «${room.name}»` : `${teacher.name}`;
      throw new ApiError(
        409,
        'slot_busy',
        `${who} занят с ${busy.start} до ${toHhMm(toMinutes(busy.start) + busy.duration_min)} — «${busy.title}». Поставить всё равно?`,
        { lesson_id: busy.id, overbook: true },
      );
    }

    const lesson: MockLesson = {
      id: `55555555-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`,
      branch_id: room.branch_id,
      date,
      teacher_id: teacher.id,
      room_id: room.id,
      start: toHhMm(startMinutes),
      duration_min: payload.duration_min,
      kind: 'trial',
      title: lead.student_name ?? lead.name,
      // У пробного нет ученика — он ссылается на заявку (lesson.lead_id в схеме)
      student_ids: [],
      note: null,
      lead_id: lead.id,
    };
    LESSONS.push(lesson);

    lead.trial_lesson_id = lesson.id;
    moveStage(lead, 'trial_booked');
    lead.next_action_at = null;

    return delay({
      lesson_id: lesson.id,
      stage: lead.stage,
      starts_at: iso(date, startMinutes),
      teacher: teacher.name,
      room: room.name,
      // Напоминание за сутки кладётся в очередь с ключом trial_reminder:<lesson_id>
      notification_queued: true,
    });
  },

  convertLead: async (leadId: string, payload: ConvertRequest): Promise<ConvertResponse> => {
    const lead = findLeadOr404(leadId);
    if (lead.stage === 'won' && lead.student_id) {
      throw new ApiError(
        409,
        'lead_already_converted',
        'Заявка уже оформлена в ученика. Откройте карточку ученика вместо повторного оформления.',
      );
    }

    const payerPhone = payload.payer ? normalizePhone(payload.payer.phone) : normalizePhone(lead.phone);
    const payerName = payload.payer
      ? `${payload.payer.first_name} ${payload.payer.last_name}`.trim()
      : `${payload.student.first_name} ${payload.student.last_name}`.trim();

    // Персона с тем же телефоном переиспользуется: второй профиль на одного
    // родителя означал бы потерянную семейную скидку
    let family = FAMILIES.find((f) => f.payer && f.payer.phone === payerPhone);
    if (!family) {
      family = {
        id: `77777777-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`,
        name: payload.payer?.last_name ?? payload.student.last_name,
        payer: { name: payerName, phone: payerPhone },
        discount_pct: 0,
        paid_this_month: 0,
        debt: 0,
      };
      FAMILIES.push(family);
    }

    const discipline = findDiscipline(payload.student.discipline_id ?? lead.discipline_id);
    const student: MockStudent = {
      id: `44444444-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`,
      name: `${payload.student.first_name} ${payload.student.last_name}`.trim(),
      subscription: null,
      age: lead.student_age ?? 0,
      discipline: discipline?.name ?? '—',
      teacher_id: payload.student.main_teacher_id ?? findTeacher(TEACHER_FALLBACK).id,
      branch_id: payload.student.branch_id ?? lead.branch_id ?? BRANCH_AF,
      started_on: MOCK_TODAY,
      status: 'active',
      family_id: family.id,
    };
    STUDENTS.push(student);
    LEDGER[student.id] = [];

    // Продажа идёт тем же кодом, что и в этапе 2, — второй реализации нет
    let subscriptionId: string | null = null;
    if (payload.subscription) {
      const sold = sellSubscriptionFor(student, {
        plan_id: payload.subscription.plan_id,
        starts_on: payload.subscription.starts_on,
        discount_pct: payload.subscription.discount_pct,
        ...(payload.subscription.promo_code ? { promo_code: payload.subscription.promo_code } : {}),
        ...(payload.subscription.payment ? { payment: payload.subscription.payment } : {}),
      });
      subscriptionId = sold.subscription_id;
    }

    lead.student_id = student.id;
    lead.person_id = `33333333-0000-4000-8000-${String(Date.now() % 1e12).padStart(12, '0')}`;
    moveStage(lead, 'won');
    lead.next_action_at = null;

    return delay({
      student_id: student.id,
      person_id: lead.person_id,
      family_id: family.id,
      subscription_id: subscriptionId,
      stage: lead.stage,
    });
  },

  funnel: async (from: string, to: string): Promise<FunnelReport> => {
    const inPeriod = (isoAt: string) => {
      const day = isoAt.slice(0, 10);
      return day >= from && day <= to;
    };
    const path: LeadStage[] = STAGE_ORDER;

    const stages = path.map((stage, index) => {
      const entered = LEADS.filter((l) => l.history.some((h) => h.to === stage && inPeriod(h.at)));
      const next = path[index + 1];
      const movedOn = next
        ? entered.filter((l) => l.history.some((h) => h.to === next)).length
        : entered.length;
      return {
        stage,
        entered: entered.length,
        moved_on: movedOn,
        conversion_pct: entered.length > 0 ? Math.round((movedOn / entered.length) * 100) : 0,
      };
    });

    const sources: FunnelSourceRow[] = SOURCES.map((source) => {
      const leads = LEADS.filter((l) => l.source === source && inPeriod(l.created_at));
      const trials = leads.filter((l) => l.history.some((h) => h.to === 'trial_booked')).length;
      const won = leads.filter((l) => l.stage === 'won');
      return {
        source,
        leads: leads.length,
        trials,
        won: won.length,
        conversion_pct: leads.length > 0 ? Math.round((won.length / leads.length) * 100) : 0,
        avg_days_to_won: Math.round(avgDaysToWon(won) * 10) / 10,
      };
    }).filter((row) => row.leads > 0);

    const lostCounts = new Map<LostReason, number>();
    for (const lead of LEADS) {
      if (lead.stage !== 'lost' || !lead.lost_reason) continue;
      if (!lead.history.some((h) => h.to === 'lost' && inPeriod(h.at))) continue;
      lostCounts.set(lead.lost_reason, (lostCounts.get(lead.lost_reason) ?? 0) + 1);
    }

    return delay({
      period: { from, to },
      stages,
      sources: sources.sort((a, b) => b.leads - a.leads),
      lost_reasons: [...lostCounts.entries()]
        .map(([reason, count]) => ({ reason, count }))
        .sort((a, b) => b.count - a.count),
      avg_days_to_won: Math.round(avgDaysToWon(LEADS.filter((l) => l.stage === 'won')) * 10) / 10,
    });
  },

  /* ==========================================================================
     Этап 4: ведомость, закрытие периода и отчёты
     ========================================================================== */

  payroll: async (from: string, to: string, branchId?: string | null): Promise<PayrollSheet> => {
    const closed = closedPeriodFor(from, to);
    const rows = sheetRows(from, to, branchId);
    const teachers = TEACHERS.map((teacher) => payrollRow(teacher, rows.filter((r) => r.teacher_id === teacher.id), from))
      .filter((row) => row.entries > 0)
      .sort((a, b) => b.total - a.total);

    return delay({
      period: { from, to },
      closed: closed !== null,
      period_id: closed?.id ?? null,
      closed_at: closed?.closed_at ?? null,
      closed_by: closed?.closed_by ?? null,
      branch_id: branchId ?? null,
      teachers,
      totals: payrollTotals(teachers),
      note: sheetNote(closed !== null, teachers),
    });
  },

  payrollTeacher: async (
    staffId: string,
    from: string,
    to: string,
    branchId?: string | null,
  ): Promise<PayrollDetail> => {
    const teacher = TEACHERS.find((t) => t.id === staffId);
    if (!teacher) {
      throw new ApiError(404, 'teacher_not_found', 'Преподаватель не найден.');
    }
    const closed = closedPeriodFor(from, to);
    const rows = sheetRows(from, to, branchId).filter((r) => r.teacher_id === staffId);

    return delay({
      teacher: { id: teacher.id, name: teacher.name, color: teacher.color },
      period: { from, to },
      closed: closed !== null,
      period_id: closed?.id ?? null,
      closed_at: closed?.closed_at ?? null,
      closed_by: closed?.closed_by ?? null,
      totals: payrollRow(teacher, rows, from),
      // Порядок хронологический, как у живого бэкенда: расшифровку читают
      // сверху вниз вместе с журналом занятий
      entries: rows
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date) || a.start.localeCompare(b.start))
        .map((row) => toPayrollEntry(row, from)),
    });
  },

  payrollPeriods: async (limit = 12): Promise<PayrollPeriodRow[]> =>
    delay(
      PAYROLL_PERIODS.slice()
        .sort((a, b) => b.from.localeCompare(a.from))
        .slice(0, limit)
        .map((period) => {
          const rows = ACCRUALS.filter((a) => a.period_id === period.id);
          return {
            id: period.id,
            period: { from: period.from, to: period.to },
            closed: true,
            closed_at: period.closed_at,
            closed_by: period.closed_by,
            teachers: new Set(rows.map((r) => r.teacher_id)).size,
            entries: rows.length,
            total: rows.reduce((sum, r) => sum + r.amount, 0),
          };
        }),
    ),

  /**
   * Закрытие периода. Обратной операции нет: строка заводится сразу закрытой,
   * а ошибка чинится корректировкой в следующем периоде. Поэтому здесь
   * три отказа до записи, а не откат после неё.
   */
  closePayrollPeriod: async (from: string, to: string): Promise<PeriodClosed> => {
    if (from > to) {
      throw new ApiError(400, 'bad_period', 'Начало периода позже его конца.');
    }
    if (to >= MOCK_TODAY) {
      throw new ApiError(
        422,
        'period_not_over',
        'Период ещё не закончился. Занятия, которые в нём пройдут, целиком уехали бы корректировкой в следующий месяц.',
      );
    }
    const overlap = PAYROLL_PERIODS.find((p) => p.from <= to && from <= p.to);
    if (overlap) {
      throw new ApiError(
        409,
        'period_overlap',
        `Период пересекается с уже закрытым ${overlap.from} — ${overlap.to}. Переоткрыть его нельзя.`,
      );
    }

    const stamped = ACCRUALS.filter((a) => a.period_id === null && a.date <= to);
    const period = {
      id: nextPeriodId(),
      from,
      to,
      closed_at: nowIso(),
      closed_by: CURRENT_USER.name,
    };
    for (const row of stamped) row.period_id = period.id;
    PAYROLL_PERIODS.push(period);

    const total = stamped.reduce((sum, r) => sum + r.amount, 0);
    const teachers = new Set(stamped.map((r) => r.teacher_id)).size;
    return delay({
      id: period.id,
      period: { from, to },
      closed: true,
      closed_at: period.closed_at,
      entries: stamped.length,
      teachers,
      total,
      message: `Период закрыт: ${stamped.length} ${plural(stamped.length, 'начисление', 'начисления', 'начислений')} на ${money(total)} по ${teachers} ${plural(teachers, 'преподавателю', 'преподавателям', 'преподавателям')}. Новые отметки за эти дни уйдут в следующий период корректировкой.`,
    });
  },

  revenueReport: async (from: string, to: string, branchId?: string | null): Promise<RevenueReport> => {
    const rows = PAYMENTS.filter((p) => p.date >= from && p.date <= to && (!branchId || p.branch_id === branchId));
    const total = rows.reduce((sum, p) => sum + p.amount, 0);

    const byBranch = groupSlices(rows, total, (p) => {
      const branch = BRANCHES.find((b) => b.id === p.branch_id);
      return { id: p.branch_id, name: branch?.name ?? 'Неизвестный филиал' };
    });
    // Платёж без абонемента попадает в «Не распределено»: иначе сумма разрезов
    // не сошлась бы с итогом, а несходящийся финансовый отчёт хуже отсутствующего.
    const byDiscipline = groupSlices(rows, total, (p) => ({
      id: p.discipline ? disciplineByName(p.discipline)?.id ?? null : null,
      name: p.discipline ?? 'Не распределено',
    }));

    const months = new Map<string, { amount: number; payments: number }>();
    for (const payment of rows) {
      const key = payment.date.slice(0, 7);
      const cell = months.get(key) ?? { amount: 0, payments: 0 };
      cell.amount += payment.amount;
      cell.payments += 1;
      months.set(key, cell);
    }

    const methods = groupSlices(rows, total, (p) => ({ id: p.method, name: p.method }));

    return delay({
      period: { from, to },
      branch_id: branchId ?? null,
      total,
      payments: rows.length,
      by_branch: byBranch,
      by_discipline: byDiscipline,
      by_month: [...months.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([month, cell]) => ({ month, ...cell })),
      by_method: methods.map((slice) => ({
        method: slice.id as PaymentMethod,
        amount: slice.amount,
        payments: slice.payments,
        share_pct: slice.share_pct,
      })),
    });
  },

  roomsReport: async (from: string, to: string, branchId?: string | null): Promise<RoomsReport> => {
    const branches = BRANCHES.filter((b) => !branchId || b.id === branchId);
    const openDays = workingDays(from, to);

    const rooms: RoomLoad[] = ROOMS.filter((room) => branches.some((b) => b.id === room.branch_id)).map((room) => {
      const branch = BRANCHES.find((b) => b.id === room.branch_id)!;
      const perDay = branch.opens_at && branch.closes_at ? toMinutes(branch.closes_at) - toMinutes(branch.opens_at) : 660;
      const busy = (ROOM_DAILY_BUSY_MIN[room.id] ?? 0) * openDays;
      const capacity = perDay * openDays;
      return {
        room_id: room.id,
        room: room.name,
        branch_id: branch.id,
        branch: branch.name,
        lessons: Math.round(busy / 55),
        busy_minutes: busy,
        capacity_minutes: capacity,
        utilization_pct: capacity > 0 ? Math.round((busy / capacity) * 100) : 0,
      };
    });

    const branchLoads: BranchLoad[] = branches.map((branch) => {
      const own = rooms.filter((r) => r.branch_id === branch.id);
      const busy = own.reduce((sum, r) => sum + r.busy_minutes, 0);
      const capacity = own.reduce((sum, r) => sum + r.capacity_minutes, 0);
      return {
        branch_id: branch.id,
        branch: branch.name,
        rooms: own.length,
        open_days: openDays,
        open_minutes_per_day: toMinutes(branch.closes_at) - toMinutes(branch.opens_at),
        busy_minutes: busy,
        capacity_minutes: capacity,
        utilization_pct: capacity > 0 ? Math.round((busy / capacity) * 100) : 0,
      };
    });

    const busy = rooms.reduce((sum, r) => sum + r.busy_minutes, 0);
    const capacity = rooms.reduce((sum, r) => sum + r.capacity_minutes, 0);

    return delay({
      period: { from, to },
      branch_id: branchId ?? null,
      utilization_pct: capacity > 0 ? Math.round((busy / capacity) * 100) : 0,
      busy_minutes: busy,
      capacity_minutes: capacity,
      capacity_note:
        'Ёмкость = часы работы филиала × дни с занятиями × кабинеты. Календаря рабочих дней в схеме нет, поэтому рабочим считается день, в который в филиале было хотя бы одно занятие.',
      branches: branchLoads.sort((a, b) => b.utilization_pct - a.utilization_pct),
      // Сортировка по загрузке: отчёт отвечает на вопрос «где кончилось место»
      rooms: rooms.sort((a, b) => b.utilization_pct - a.utilization_pct),
    });
  },

  debtsReport: async (limit = 50): Promise<DebtsReport> => {
    const items: Debtor[] = FAMILIES.filter((f) => f.debt > 0)
      .map((family) => {
        const members = STUDENTS.filter((s) => s.family_id === family.id);
        const purchases = members.flatMap((s) => (LEDGER[s.id] ?? []).filter((e) => e.kind === 'purchase'));
        const dates = purchases.map((e) => e.date).sort();
        return {
          family_id: family.id,
          payer: family.payer?.name ?? null,
          phone: family.payer?.phone ?? null,
          students: members.map((s) => s.name),
          charged: family.paid_this_month + family.debt,
          paid: family.paid_this_month,
          debt: family.debt,
          since_on: dates[0] ?? null,
          last_paid_on: family.paid_this_month > 0 ? dates[dates.length - 1] ?? null : null,
        };
      })
      .sort((a, b) => b.debt - a.debt)
      .slice(0, limit);

    return delay({
      families: items.length,
      total: items.reduce((sum, item) => sum + item.debt, 0),
      items,
      note: 'Долг = начислено по абонементам семьи минус поступившие платежи. Периода нет: это состояние на сейчас, а не за отрезок.',
    });
  },

  moneySummary: async (from: string, to: string, branchId?: string | null): Promise<MoneySummary> => {
    const inPeriod = (p: { date: string; branch_id: string }) =>
      p.date >= from && p.date <= to && (!branchId || p.branch_id === branchId);
    const amount = PAYMENTS.filter(inPeriod).reduce((sum, p) => sum + p.amount, 0);

    // Прошлый период той же длины, вплотную перед выбранным
    const length = daysBetween(from, to) + 1;
    const prevTo = addDays(from, -1);
    const prevFrom = addDays(prevTo, -(length - 1));
    const previous = PAYMENTS.filter(
      (p) => p.date >= prevFrom && p.date <= prevTo && (!branchId || p.branch_id === branchId),
    ).reduce((sum, p) => sum + p.amount, 0);

    const rooms = await mockApi.roomsReport(from, to, branchId);
    const sheet = sheetRows(from, to, branchId);
    const debtors = FAMILIES.filter((f) => f.debt > 0);
    const subscriptions = STUDENTS.map((s) => s.subscription).filter((s): s is MockSubscription => s !== null);

    return delay({
      period: { from, to },
      branch_id: branchId ?? null,
      revenue: {
        amount,
        previous,
        previous_period: { from: prevFrom, to: prevTo },
        // null, когда прошлого периода нет: «+100%» от нуля читается как рост,
        // хотя означает лишь появление первых данных
        change_pct: previous > 0 ? Math.round(((amount - previous) / previous) * 100) : null,
      },
      rooms: { utilization_pct: rooms.utilization_pct, busiest: rooms.rooms[0] ?? null },
      churn: { ended: 12, churned: 4, churn_pct: 33, worst_teacher: null },
      payroll: {
        // Фонд оплаты труда — те же деньги людей, что и ведомость, только
        // одной строкой: администратору сервер отдаёт `null`, и мок обязан
        // отдавать его же, иначе эту ветку экрана проверить нечем.
        total: mockSession?.role === 'owner' ? sheet.reduce((sum, r) => sum + r.amount, 0) : null,
        lessons: new Set(sheet.filter((r) => r.kind === 'lesson').map((r) => `${r.teacher_id}|${r.date}|${r.start}`)).size,
        closed: closedPeriodFor(from, to) !== null,
      },
      attention: {
        debt_families: debtors.length,
        debt_amount: debtors.reduce((sum, f) => sum + f.debt, 0),
        subscriptions_running_low: subscriptions.filter((s) => s.lessons_balance <= 2).length,
        makeups_open: subscriptions.reduce((sum, s) => sum + s.makeups_balance, 0),
        frozen_now: subscriptions.filter((s) => s.holds.some((h) => h.from <= MOCK_TODAY && MOCK_TODAY < h.to)).length,
      },
    });
  },

  /* ---------- этап 5: кабинет родителя ---------- */

  meChildren: async (): Promise<MeChild[]> => {
    const ids = familyStudentIds();
    return delay(ids.map((id) => STUDENTS.find((s) => s.id === id)).filter((s): s is MockStudent => Boolean(s)).map(toMeChild));
  },

  meSchedule: async (from?: string | null, to?: string | null): Promise<MeSchedule> => {
    const ids = familyStudentIds();
    // Без периода — неделя вперёд от сегодня, как в контракте.
    const start = from || MOCK_TODAY;
    const end = to || addDays(start, 6);
    return delay({ period: { from: start, to: end }, lessons: meLessons(ids, start, end) });
  },

  meChild: async (studentId: string): Promise<MeChildCard> => {
    const student = familyChildOr404(studentId);
    const history = meHistory(student);
    return delay({
      student_id: student.id,
      name: student.name,
      age: student.age,
      discipline: student.discipline,
      teacher: findTeacher(student.teacher_id).name,
      started_on: student.started_on,
      subscription: toMeSubscription(student.subscription),
      makeups: meMakeups(student),
      history,
      progress: meProgress(student, history),
    });
  },

  /**
   * Заявка, а не перенос. `reason` и `preferred` уходят в задачу администратору
   * и родителю не возвращаются: он их только что написал.
   */
  requestReschedule: async (lessonId: string, payload: RescheduleRequest): Promise<RescheduleCreated> => {
    void payload;
    const ids = familyStudentIds();
    const lesson = meLessons(ids, addDays(MOCK_TODAY, -90), addDays(MOCK_TODAY, 90)).find(
      (l) => l.lesson_id === lessonId,
    );
    // Чужое занятие — 404, а не 403: ответ не должен подтверждать, что оно есть.
    if (!lesson) {
      throw new ApiError(404, 'lesson_not_found', 'Занятие не найдено. Обновите расписание и попробуйте снова.');
    }
    if (lesson.status !== 'planned') {
      throw new ApiError(
        422,
        'lesson_not_reschedulable',
        lesson.status === 'cancelled'
          ? 'Занятие уже отменено — переносить нечего. О новом времени договоритесь со школой.'
          : 'Занятие уже проведено, перенести его нельзя. По отработке позвоните в школу.',
      );
    }
    if (Date.parse(lesson.starts_at) - MOCK_NOW <= ME_NOTICE_HOURS * HOUR) {
      // Текст называет и порог, и что делать: отказ без выхода бесполезен.
      throw new ApiError(
        422,
        'too_late_to_reschedule',
        `До занятия меньше ${ME_NOTICE_HOURS} часов — заявку на перенос школа уже не примет. Позвоните администратору, он решит на месте.`,
      );
    }
    if (meRescheduleRequests.has(lessonId)) {
      throw new ApiError(409, 'request_exists', 'Заявка на это занятие уже отправлена. Администратор ответит сообщением.');
    }
    const request = { request_id: meRequestId(), status: 'pending' };
    meRescheduleRequests.set(lessonId, request);
    return delay({
      ...request,
      lesson: { starts_at: lesson.starts_at, student_name: lesson.student_name },
      message: 'Заявка передана администратору. Ответ придёт сообщением.',
    });
  },

  requestRenew: async (studentId: string, payload: RenewRequest): Promise<RenewCreated> => {
    const student = familyChildOr404(studentId);
    void payload;
    return delay({
      request_id: meRequestId(),
      status: 'pending',
      student: { student_id: student.id, name: student.name },
      message: `Заявка на продление принята. Администратор свяжется с вами и примет оплату — ученик: ${student.name}.`,
    });
  },
};

/* ---------- ведомость: сборка строк ---------- */

const closedPeriodFor = (from: string, to: string) =>
  PAYROLL_PERIODS.find((p) => p.from === from && p.to === to) ?? null;

/**
 * Строки ведомости за период.
 *
 * Закрытый месяц — это штамп: берём ровно то, что было выплачено, и новые
 * отметки за те же дни туда уже не попадут. Открытый — все непроштампованные
 * начисления с датой до конца периода, включая правки за прошлые месяцы:
 * именно так они и приезжают в ведомость `carried_over`.
 */
function sheetRows(from: string, to: string, branchId?: string | null): MockAccrual[] {
  const closed = closedPeriodFor(from, to);
  const branchOk = (row: MockAccrual) => !branchId || row.branch_id === branchId;
  if (closed) return ACCRUALS.filter((row) => row.period_id === closed.id && branchOk(row));
  return ACCRUALS.filter((row) => row.period_id === null && row.date <= to && branchOk(row));
}

function payrollRow(teacher: MockTeacher, rows: MockAccrual[], from: string): PayrollRow {
  const lessons = rows.filter((row) => row.kind === 'lesson');
  const sumOf = (kind: MockAccrual['kind']) =>
    rows.filter((row) => row.kind === kind).reduce((sum, row) => sum + row.amount, 0);

  // Ставка — самая частая сумма начисления. Показывать её одним числом,
  // когда их несколько, значит вводить в заблуждение, поэтому рядом флаг.
  const counts = new Map<number, number>();
  for (const row of lessons) {
    if (row.amount > 0) counts.set(row.amount, (counts.get(row.amount) ?? 0) + 1);
  }
  const rate = [...counts.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ?? 0;

  const accrued = sumOf('lesson');
  const corrections = sumOf('correction') + sumOf('deduction');
  const bonuses = sumOf('bonus');
  const carried = rows.filter((row) => row.date < from);

  return {
    teacher: { id: teacher.id, name: teacher.name, color: teacher.color },
    // Занятие одно, а начислений столько, сколько участников: у группы
    // из четырёх человек это 1 и 4, и путать их нельзя.
    lessons: new Set(lessons.map((row) => `${row.date}|${row.start}`)).size,
    entries: rows.length,
    rate,
    rate_varies: counts.size > 1,
    no_shows: rows.filter((row) => row.mark === 'no_show').length,
    accrued,
    corrections,
    bonuses,
    total: accrued + corrections + bonuses,
    carried_over: carried.reduce((sum, row) => sum + row.amount, 0),
    carried_over_entries: carried.length,
  };
}

function payrollTotals(rows: PayrollRow[]): PayrollTotals {
  const sum = (pick: (row: PayrollRow) => number) => rows.reduce((acc, row) => acc + pick(row), 0);
  return {
    teachers: rows.length,
    lessons: sum((r) => r.lessons),
    entries: sum((r) => r.entries),
    no_shows: sum((r) => r.no_shows),
    accrued: sum((r) => r.accrued),
    corrections: sum((r) => r.corrections),
    bonuses: sum((r) => r.bonuses),
    total: sum((r) => r.total),
    carried_over: sum((r) => r.carried_over),
    carried_over_entries: sum((r) => r.carried_over_entries),
  };
}

/** Формулировка сервера: она и объясняет бухгалтеру, что перед ним. */
function sheetNote(closed: boolean, rows: PayrollRow[]): string {
  const totals = payrollTotals(rows);
  if (closed) {
    return `Период закрыт: ${totals.entries} ${plural(totals.entries, 'начисление', 'начисления', 'начислений')} на ${money(totals.total)}. Новые отметки за эти дни уйдут в следующий период корректировкой.`;
  }
  if (totals.carried_over_entries > 0) {
    return `Период открыт: суммы ещё изменятся, пока идут отметки. В итог вошли ${totals.carried_over_entries} ${plural(totals.carried_over_entries, 'начисление', 'начисления', 'начислений')} за уже закрытые месяцы на ${money(totals.carried_over)} — они показаны отдельной колонкой.`;
  }
  return 'Период открыт: суммы ещё изменятся, пока идут отметки.';
}

function toPayrollEntry(row: MockAccrual, from: string): PayrollEntry {
  return {
    id: row.id,
    date: row.date,
    starts_at: row.kind === 'lesson' ? iso(row.date, toMinutes(row.start)) : null,
    kind: row.kind,
    lesson_id: null,
    student: row.student_name,
    discipline: row.discipline || null,
    branch: BRANCHES.find((b) => b.id === row.branch_id)?.name ?? null,
    duration_min: row.duration_min || null,
    mark: row.mark,
    amount: row.amount,
    // Снимок расчёта: ставка на момент занятия и доля от неё. Пересчитывать
    // его от сегодняшней ставки нельзя — она к этому времени поменяется.
    calc:
      row.kind === 'lesson'
        ? { kind: 'fixed', mark: row.mark, rate: row.rate, share: row.rate > 0 ? row.amount / row.rate : 0, amount: row.amount }
        : { kind: 'correction', rate: row.rate, share: 1, amount: row.amount },
    carried_over: row.date < from,
  };
}

/** Разрез выручки: доли считаются от итога, поэтому сумма разрезов сходится. */
function groupSlices(
  rows: MockPayment[],
  total: number,
  key: (payment: MockPayment) => { id: string | null; name: string },
): RevenueSlice[] {
  const cells = new Map<string, RevenueSlice>();
  for (const payment of rows) {
    const { id, name } = key(payment);
    const cell = cells.get(name) ?? { id, name, amount: 0, payments: 0, share_pct: 0 };
    cell.amount += payment.amount;
    cell.payments += 1;
    cells.set(name, cell);
  }
  return [...cells.values()]
    .map((cell) => ({ ...cell, share_pct: total > 0 ? Math.round((cell.amount / total) * 100) : 0 }))
    .sort((a, b) => b.amount - a.amount);
}

/** Рабочие дни периода: воскресенье школа не работает, будущее не считается. */
function workingDays(from: string, to: string): number {
  const last = to < MOCK_TODAY ? to : MOCK_TODAY;
  let days = 0;
  for (let date = from; date <= last; date = addDays(date, 1)) {
    if (new Date(`${date}T00:00:00Z`).getUTCDay() !== 0) days += 1;
  }
  return days;
}

/** Средний срок от заявки до покупки — в днях, по истории стадий. */
function avgDaysToWon(leads: MockLead[]): number {
  const spans = leads
    .map((lead) => {
      const wonAt = lead.history.find((h) => h.to === 'won')?.at;
      if (!wonAt) return null;
      return (Date.parse(wonAt) - Date.parse(lead.created_at)) / (24 * HOUR);
    })
    .filter((v): v is number => v !== null);
  if (spans.length === 0) return 0;
  return spans.reduce((sum, v) => sum + v, 0) / spans.length;
}
