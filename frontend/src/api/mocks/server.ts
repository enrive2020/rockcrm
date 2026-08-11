import { ApiError } from '../http';
import type {
  AttendanceMark,
  AttendanceRequest,
  AttendanceResponse,
  BoardColumn,
  BoardLead,
  Branch,
  ChurnRisk,
  ConvertRequest,
  ConvertResponse,
  CreateLeadRequest,
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
  LostReason,
  MarkEffect,
  MarkEffects,
  PatchLeadRequest,
  Plan,
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
} from '../types';
import { MARK_ORDER, PAYMENT_METHOD_LABELS, SOURCES, STAGE_LABELS, STAGE_ORDER } from '../types';
import {
  BRANCHES,
  BRANCH_AF,
  CURRENT_USER,
  DEFAULT_RULES,
  FAMILIES,
  LEADS,
  LEDGER,
  LESSONS,
  MAKEUPS,
  MOCK_TODAY,
  NOTES,
  PLANS,
  ROOMS,
  STUDENTS,
  TZ_OFFSET,
  addDays,
  daysBetween,
  findDiscipline,
  findFamily,
  findLead,
  findPlan,
  findRoom,
  findStudent,
  findTeacher,
  findUser,
  nextEntryId,
  nextLeadId,
  type MockHold,
  type MockLead,
  type MockLesson,
  type MockRules,
  type MockStudent,
  type MockSubscription,
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
/** lesson_id → student_id → attendance_id, чтобы отдать идентификатор отметки. */
const attendanceIds = new Map<string, string>();
let attendanceSeq = 0;

for (const lesson of LESSONS) {
  if (!lesson.initial_marks) continue;
  const perLesson = new Map<string, AttendanceMark>();
  for (const [studentId, mark] of Object.entries(lesson.initial_marks)) perLesson.set(studentId, mark);
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
  return {
    student_id: student.id,
    name: student.name,
    attendance: markOf(lesson.id, studentId),
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
  };
}

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

/* ---------- API ---------- */

export const mockApi = {
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
      teacher: { id: teacher.id, name: teacher.name, rate: teacher.rate },
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

    const attendanceId = `77777777-0000-4000-8000-${String(++attendanceSeq).padStart(12, '0')}`;
    attendanceIds.set(`${lessonId}:${student.id}`, attendanceId);

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

  /* ---------- этап 2 ---------- */

  students: async (query: string, branchId?: string | null): Promise<StudentSearchItem[]> => {
    const needle = query.trim().toLowerCase();
    if (!needle) return delay([]);
    // Телефон ищем по цифрам: администратор набирает его как угодно —
    // с +7, с 8, со скобками, — а совпасть должно всё равно
    const digits = needle.replace(/\D/g, '');

    const found = STUDENTS.filter((student) => {
      if (branchId && student.branch_id !== branchId) return false;
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
};

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
