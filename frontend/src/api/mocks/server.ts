import { ApiError } from '../http';
import type {
  AttendanceMark,
  AttendanceRequest,
  AttendanceResponse,
  Branch,
  LessonCard,
  LessonConflict,
  LessonParticipant,
  MarkEffect,
  MarkEffects,
  ScheduleLesson,
  ScheduleResponse,
  ScheduleTrack,
} from '../types';
import { MARK_ORDER } from '../types';
import {
  BRANCHES,
  LESSONS,
  STUDENTS,
  TZ_OFFSET,
  findRoom,
  findStudent,
  findTeacher,
  type MockLesson,
  type MockRules,
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
      teacherAmount = rules?.pay_on_no_show === false ? 0 : teacherRate;
      break;
    case 'cancelled_early':
      if (rules?.cancel_early === 'burn') lessonsDelta = -1;
      else if (rules?.cancel_early === 'keep') lessonsDelta = 0;
      else makeupsDelta = 1; // 'makeup' — значение по умолчанию
      break;
    case 'cancelled_late':
      // Контракт вводит отметку, но не задаёт её правило. Приравниваем к прогулу:
      // предупреждение позже порога отмены не спасает занятие (spec 4.2).
      lessonsDelta = -1;
      teacherAmount = teacherRate;
      break;
    case 'cancelled_teacher':
      if (rules?.teacher_cancel === 'keep') lessonsDelta = 0;
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
};
