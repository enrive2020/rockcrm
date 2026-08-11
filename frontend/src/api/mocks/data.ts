import type { AttendanceMark, LessonKind, LessonNote } from '../types';

/**
 * Фикстуры мок-режима. Повторяют день из прототипа: 12 августа 2026,
 * филиал «Аль-Фараби 53В», те же преподаватели, ученики и конфликт кабинета.
 *
 * Данные лежат отдельно от логики (server.ts), чтобы правка расписания
 * не задевала правила отметки.
 */

/** Идентификаторы формы UUID, но детерминированные — так их видно в отладке. */
const uid = (tag: number, n: number) =>
  `${String(tag).repeat(8)}-0000-4000-8000-${String(n).padStart(12, '0')}`;

export const TZ_OFFSET = '+06:00'; // Asia/Almaty, часовой пояс обоих филиалов

export interface MockBranch {
  id: string;
  name: string;
  timezone: string;
  opens_at: string;
  closes_at: string;
}

export const BRANCHES: MockBranch[] = [
  { id: uid(1, 1), name: 'Аль-Фараби 53В', timezone: 'Asia/Almaty', opens_at: '10:00', closes_at: '21:00' },
  { id: uid(1, 2), name: 'Абая 124', timezone: 'Asia/Almaty', opens_at: '10:00', closes_at: '21:00' },
];

export const BRANCH_AF = BRANCHES[0].id;
export const BRANCH_AB = BRANCHES[1].id;

export interface MockTeacher {
  id: string;
  name: string;
  disciplines: string[];
  /** Цвет дорожки: те же пять каналов, что в прототипе. */
  color: string;
  rate: number;
}

export const TEACHERS: MockTeacher[] = [
  { id: uid(2, 1), name: 'Дмитрий Шарапов', disciplines: ['Барабаны'], color: '#A65D3F', rate: 4500 },
  { id: uid(2, 2), name: 'Глеб Федько', disciplines: ['Гитара', 'Укулеле'], color: '#2F7D7A', rate: 4200 },
  { id: uid(2, 3), name: 'Егор Мадратов', disciplines: ['Барабаны', 'Перкуссия'], color: '#4B6489', rate: 4500 },
  { id: uid(2, 4), name: 'Андрей Меренков', disciplines: ['Гитара', 'Бас'], color: '#4E7A3E', rate: 4200 },
  { id: uid(2, 5), name: 'Алия Исенова', disciplines: ['Вокал', 'Фортепиано'], color: '#7C4A72', rate: 4000 },
];

const T = {
  sharapov: TEACHERS[0].id,
  fedko: TEACHERS[1].id,
  madratov: TEACHERS[2].id,
  merenkov: TEACHERS[3].id,
  isenova: TEACHERS[4].id,
};

export interface MockRoom {
  id: string;
  name: string;
  branch_id: string;
}

export const ROOMS: MockRoom[] = [
  { id: uid(3, 1), name: 'Барабанная A', branch_id: BRANCH_AF },
  { id: uid(3, 2), name: 'Класс 1', branch_id: BRANCH_AF },
  { id: uid(3, 3), name: 'Класс 2', branch_id: BRANCH_AF },
  { id: uid(3, 4), name: 'Барабанная B', branch_id: BRANCH_AB },
  { id: uid(3, 5), name: 'Класс 3', branch_id: BRANCH_AB },
];

const R = {
  drumsA: ROOMS[0].id,
  class1: ROOMS[1].id,
  class2: ROOMS[2].id,
  drumsB: ROOMS[3].id,
  class3: ROOMS[4].id,
};

/**
 * Правила списания. В настоящей системе это копия внутри абонемента:
 * проданный абонемент живёт по условиям момента покупки (spec 4.2).
 */
export interface MockRules {
  /** Прогул без предупреждения сгорает. */
  no_show_burns: boolean;
  /** Порог отмены «заранее», часов. */
  cancel_early_hours: number;
  /** Что делает отмена заранее. */
  cancel_early: 'makeup' | 'keep' | 'burn';
  /** Что делает отмена преподавателем. */
  teacher_cancel: 'makeup' | 'keep';
  /** Платит ли школа преподавателю за прогул ученика. */
  pay_on_no_show: boolean;
  /** Срок жизни отработки, дней. */
  makeup_ttl_days: number;
}

export const DEFAULT_RULES: MockRules = {
  no_show_burns: true,
  cancel_early_hours: 24,
  cancel_early: 'makeup',
  teacher_cancel: 'makeup',
  pay_on_no_show: true,
  makeup_ttl_days: 30,
};

export interface MockSubscription {
  id: string;
  lessons_total: number;
  lessons_balance: number;
  makeups_balance: number;
  valid_until: string;
  status: string;
  rules: MockRules;
}

export interface MockStudent {
  id: string;
  name: string;
  /** null — действующего абонемента нет, занятие идёт разовой оплатой. */
  subscription: MockSubscription | null;
}

let subSeq = 0;
const sub = (total: number, balance: number, makeups = 0, validUntil = '2026-08-31'): MockSubscription => ({
  id: uid(6, ++subSeq),
  lessons_total: total,
  lessons_balance: balance,
  makeups_balance: makeups,
  valid_until: validUntil,
  status: 'active',
  rules: DEFAULT_RULES,
});

let stuSeq = 0;
const student = (name: string, subscription: MockSubscription | null): MockStudent => ({
  id: uid(4, ++stuSeq),
  name,
  subscription,
});

export const STUDENTS: MockStudent[] = [
  student('Тимур Ахметов', sub(8, 4)),
  student('Амина Сагындык', sub(8, 5, 1)), // герой прототипа: 5 из 8, +1 отработка
  student('Даниал Ким', sub(8, 6)),
  student('Ержан Оспанов', sub(4, 1)), // после отметки останется 0 — сработает alert
  student('Алиса Ким', null), // пробный урок, абонемента нет
  student('Сабина Нурлан', sub(8, 3)),
  student('Айсулу Бек', sub(8, 7)),
  student('Марк Ли', sub(12, 9, 0, '2026-09-30')),
  student('Амир Жанат', sub(8, 2)),
  student('Ольга Ким', sub(8, 8)),
  student('Дмитрий Со', sub(4, 0)), // нулевой остаток — списание вернёт 422
  student('Мадина Абишева', sub(8, 2)),
  student('Санжар Тлеу', sub(8, 5)),
  student('Дамир Ералы', null),
  student('Камила Ер', sub(12, 11, 0, '2027-01-31')),
  student('Арай Токтар', sub(8, 6)),
  student('Рустам Ли', sub(8, 3)),
  student('Ильяс Абен', null),
  student('Данияр Аман', sub(8, 7)),
];

const S = (name: string): string => {
  const found = STUDENTS.find((s) => s.name === name);
  if (!found) throw new Error(`В фикстурах нет ученика «${name}»`);
  return found.id;
};

export interface MockLesson {
  id: string;
  branch_id: string;
  date: string;
  teacher_id: string;
  room_id: string;
  /** Местное время начала, "11:00". */
  start: string;
  duration_min: number;
  kind: LessonKind;
  /** Имя ученика, название группы или имя из заявки. */
  title: string;
  student_ids: string[];
  note: LessonNote | null;
  /** Отметки, проставленные «раньше» — стартовое состояние дня. */
  initial_marks?: Record<string, AttendanceMark>;
}

let lesSeq = 0;
const lesson = (l: Omit<MockLesson, 'id'>): MockLesson => ({ id: uid(5, ++lesSeq), ...l });

const AMINA_NOTE: LessonNote = {
  body: 'Разбираем сбивку на 16-х. Правая рука зажимается выше 90 bpm — держим метроном на 80.',
  homework: '10 минут в день на восьмые, правая нога на кике.',
  tags: ['Nirvana — Smells Like Teen Spirit', 'Single Paradiddle', '80 bpm'],
};

/** Главный день — тот же, что в прототипе. */
export const DEMO_DATE = '2026-08-12';

export const LESSONS: MockLesson[] = [
  // --- Аль-Фараби, 12 августа ---
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.fedko, room_id: R.class1, start: '10:00', duration_min: 55, kind: 'regular', title: 'Тимур Ахметов', student_ids: [S('Тимур Ахметов')], note: null, initial_marks: { [S('Тимур Ахметов')]: 'came' } }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.sharapov, room_id: R.drumsA, start: '11:00', duration_min: 55, kind: 'regular', title: 'Амина Сагындык', student_ids: [S('Амина Сагындык')], note: AMINA_NOTE }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.merenkov, room_id: R.class2, start: '11:00', duration_min: 85, kind: 'regular', title: 'Даниал Ким', student_ids: [S('Даниал Ким')], note: null }),
  // Пересечение по «Барабанной A» с пробным в 13:00 — тот самый конфликт прототипа
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.sharapov, room_id: R.drumsA, start: '12:30', duration_min: 55, kind: 'regular', title: 'Ержан Оспанов', student_ids: [S('Ержан Оспанов')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.madratov, room_id: R.drumsA, start: '13:00', duration_min: 45, kind: 'trial', title: 'Алиса Ким', student_ids: [S('Алиса Ким')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.fedko, room_id: R.class1, start: '14:00', duration_min: 55, kind: 'regular', title: 'Сабина Нурлан', student_ids: [S('Сабина Нурлан')], note: null, initial_marks: { [S('Сабина Нурлан')]: 'no_show' } }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.isenova, room_id: R.class2, start: '15:00', duration_min: 55, kind: 'regular', title: 'Айсулу Бек', student_ids: [S('Айсулу Бек')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.sharapov, room_id: R.drumsA, start: '16:00', duration_min: 85, kind: 'regular', title: 'Марк Ли', student_ids: [S('Марк Ли')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.merenkov, room_id: R.class1, start: '17:30', duration_min: 55, kind: 'regular', title: 'Ансамбль «Пятый лад» · 4 чел', student_ids: [S('Мадина Абишева'), S('Санжар Тлеу'), S('Дамир Ералы'), S('Камила Ер')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.sharapov, room_id: R.drumsA, start: '18:30', duration_min: 55, kind: 'regular', title: 'Амир Жанат', student_ids: [S('Амир Жанат')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.fedko, room_id: R.class2, start: '19:00', duration_min: 55, kind: 'regular', title: 'Ольга Ким', student_ids: [S('Ольга Ким')], note: null }),
  lesson({ branch_id: BRANCH_AF, date: DEMO_DATE, teacher_id: T.isenova, room_id: R.class1, start: '20:00', duration_min: 55, kind: 'regular', title: 'Дмитрий Со', student_ids: [S('Дмитрий Со')], note: null }),

  // --- Абая, 12 августа ---
  lesson({ branch_id: BRANCH_AB, date: DEMO_DATE, teacher_id: T.madratov, room_id: R.drumsB, start: '11:00', duration_min: 55, kind: 'regular', title: 'Арай Токтар', student_ids: [S('Арай Токтар')], note: null }),
  lesson({ branch_id: BRANCH_AB, date: DEMO_DATE, teacher_id: T.isenova, room_id: R.class3, start: '12:00', duration_min: 55, kind: 'regular', title: 'Камила Ер', student_ids: [S('Камила Ер')], note: null, initial_marks: { [S('Камила Ер')]: 'came' } }),
  lesson({ branch_id: BRANCH_AB, date: DEMO_DATE, teacher_id: T.fedko, room_id: R.class3, start: '15:00', duration_min: 55, kind: 'regular', title: 'Рустам Ли', student_ids: [S('Рустам Ли')], note: null }),
  lesson({ branch_id: BRANCH_AB, date: DEMO_DATE, teacher_id: T.madratov, room_id: R.drumsB, start: '16:30', duration_min: 45, kind: 'trial', title: 'Ильяс Абен', student_ids: [S('Ильяс Абен')], note: null }),
  lesson({ branch_id: BRANCH_AB, date: DEMO_DATE, teacher_id: T.merenkov, room_id: R.class3, start: '18:00', duration_min: 55, kind: 'regular', title: 'Данияр Аман', student_ids: [S('Данияр Аман')], note: null }),

  // --- Аль-Фараби, 11 августа: день уже отмечен целиком ---
  lesson({ branch_id: BRANCH_AF, date: '2026-08-11', teacher_id: T.fedko, room_id: R.class1, start: '10:00', duration_min: 55, kind: 'regular', title: 'Тимур Ахметов', student_ids: [S('Тимур Ахметов')], note: null, initial_marks: { [S('Тимур Ахметов')]: 'came' } }),
  lesson({ branch_id: BRANCH_AF, date: '2026-08-11', teacher_id: T.sharapov, room_id: R.drumsA, start: '11:00', duration_min: 55, kind: 'regular', title: 'Амина Сагындык', student_ids: [S('Амина Сагындык')], note: AMINA_NOTE, initial_marks: { [S('Амина Сагындык')]: 'came' } }),
  lesson({ branch_id: BRANCH_AF, date: '2026-08-11', teacher_id: T.isenova, room_id: R.class2, start: '15:00', duration_min: 55, kind: 'regular', title: 'Айсулу Бек', student_ids: [S('Айсулу Бек')], note: null, initial_marks: { [S('Айсулу Бек')]: 'cancelled_early' } }),
];

export const findRoom = (id: string): MockRoom => ROOMS.find((r) => r.id === id)!;
export const findTeacher = (id: string): MockTeacher => TEACHERS.find((t) => t.id === id)!;
export const findStudent = (id: string): MockStudent => STUDENTS.find((s) => s.id === id)!;
