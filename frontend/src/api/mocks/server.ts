import { ApiError } from '../http';
import type {
  AttendanceMark,
  AttendanceRequest,
  AttendanceResponse,
  Branch,
  ChurnRisk,
  Family,
  HoldReleaseResponse,
  HoldRequest,
  HoldResponse,
  LessonCard,
  LessonConflict,
  LessonParticipant,
  MarkEffect,
  MarkEffects,
  Plan,
  ScheduleLesson,
  ScheduleResponse,
  ScheduleTrack,
  SellSubscriptionRequest,
  SellSubscriptionResponse,
  StudentCard,
  StudentSearchItem,
  StudentSubscription,
} from '../types';
import { MARK_ORDER, PAYMENT_METHOD_LABELS } from '../types';
import {
  BRANCHES,
  DEFAULT_RULES,
  LEDGER,
  LESSONS,
  MAKEUPS,
  MOCK_TODAY,
  NOTES,
  PLANS,
  STUDENTS,
  TZ_OFFSET,
  addDays,
  daysBetween,
  findFamily,
  findPlan,
  findRoom,
  findStudent,
  findTeacher,
  nextEntryId,
  type MockHold,
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
  ): Promise<SellSubscriptionResponse> => {
    const student = findStudentOr404(studentId);
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

    return delay({
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
    });
  },

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
};
