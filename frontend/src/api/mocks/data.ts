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
 *
 * Имена ключей повторяют `tenant.default_rules` из db/001_core.sql — тот же
 * набор уходит в карточку ученика этапа 2, поэтому второго словаря нет.
 */
export interface MockRules {
  /** Прогул без предупреждения сгорает. */
  no_show_burns: boolean;
  /** Порог отмены «заранее», часов. */
  cancel_notice_hours: number;
  /** Что делает отмена заранее. */
  cancel_early_effect: 'makeup' | 'keep' | 'burn';
  /** Что делает отмена преподавателем. */
  teacher_cancel_effect: 'makeup' | 'keep' | 'no_charge';
  /** Платит ли школа преподавателю за прогул ученика. */
  pay_teacher_on_no_show: boolean;
  /** Срок жизни отработки, дней. */
  makeup_ttl_days: number;
  /** Лимит заморозки за календарный год, дней. */
  freeze_days_per_year: number;
  /** Сколько занятий переносится при продлении. Ноль — перенос запрещён. */
  carry_over_lessons: number;
}

export const DEFAULT_RULES: MockRules = {
  no_show_burns: true,
  cancel_notice_hours: 24,
  cancel_early_effect: 'makeup',
  teacher_cancel_effect: 'makeup',
  pay_teacher_on_no_show: true,
  makeup_ttl_days: 30,
  freeze_days_per_year: 14,
  carry_over_lessons: 0,
};

/* ---------- тарифы (этап 2) ---------- */

export interface MockPlan {
  id: string;
  name: string;
  discipline: string;
  format: 'individual' | 'pair' | 'group' | 'trial';
  duration_min: number;
  lessons_count: number;
  valid_days: number;
  price: number;
}

/** Цена за занятие всюду круглая: администратор считает её в уме при споре. */
export const PLANS: MockPlan[] = [
  { id: uid(8, 1), name: 'Барабаны, 2 раза в неделю, 55 мин', discipline: 'Барабаны', format: 'individual', duration_min: 55, lessons_count: 8, valid_days: 31, price: 54000 },
  { id: uid(8, 2), name: 'Барабаны, 3 раза в неделю, 55 мин', discipline: 'Барабаны', format: 'individual', duration_min: 55, lessons_count: 12, valid_days: 31, price: 78000 },
  { id: uid(8, 3), name: 'Барабаны, 2 раза в неделю, 85 мин', discipline: 'Барабаны', format: 'individual', duration_min: 85, lessons_count: 8, valid_days: 31, price: 81600 },
  { id: uid(8, 4), name: 'Барабаны, 2 раза в неделю, 55 мин · 6 месяцев', discipline: 'Барабаны', format: 'individual', duration_min: 55, lessons_count: 48, valid_days: 184, price: 276000 },
  { id: uid(8, 5), name: 'Гитара, 2 раза в неделю, 55 мин', discipline: 'Гитара', format: 'individual', duration_min: 55, lessons_count: 8, valid_days: 31, price: 52000 },
  { id: uid(8, 6), name: 'Гитара, 1 раз в неделю, 55 мин', discipline: 'Гитара', format: 'individual', duration_min: 55, lessons_count: 4, valid_days: 31, price: 28000 },
  { id: uid(8, 7), name: 'Вокал, 2 раза в неделю, 55 мин', discipline: 'Вокал', format: 'individual', duration_min: 55, lessons_count: 8, valid_days: 31, price: 56000 },
  { id: uid(8, 8), name: 'Бас, 2 раза в неделю, 55 мин', discipline: 'Бас', format: 'individual', duration_min: 55, lessons_count: 8, valid_days: 31, price: 52000 },
  { id: uid(8, 9), name: 'Ансамбль, 1 раз в неделю, 85 мин', discipline: 'Гитара', format: 'group', duration_min: 85, lessons_count: 4, valid_days: 31, price: 24000 },
];

export const findPlan = (id: string): MockPlan | undefined => PLANS.find((p) => p.id === id);

/* ---------- семьи (этап 2) ---------- */

export interface MockFamily {
  id: string;
  name: string;
  /** null — семья заведена без плательщика; такое бывает у взрослых учеников. */
  payer: { name: string; phone: string } | null;
  discount_pct: number;
  paid_this_month: number;
  debt: number;
}

/**
 * Семьи. Администратору звонит родитель, поэтому телефон плательщика —
 * второй ключ поиска после имени ребёнка.
 */
export const FAMILIES: MockFamily[] = [
  { id: uid(7, 1), name: 'Сагындык', payer: { name: 'Гульнара Сагындык', phone: '+77015550003' }, discount_pct: 10, paid_this_month: 97200, debt: 0 },
  { id: uid(7, 2), name: 'Ахметов', payer: { name: 'Асель Ахметова', phone: '+77015550004' }, discount_pct: 0, paid_this_month: 52000, debt: 0 },
  { id: uid(7, 3), name: 'Ким', payer: { name: 'Ирина Ким', phone: '+77015550005' }, discount_pct: 0, paid_this_month: 52000, debt: 0 },
  { id: uid(7, 4), name: 'Оспанов', payer: { name: 'Ержан Оспанов', phone: '+77015550006' }, discount_pct: 0, paid_this_month: 28000, debt: 0 },
  { id: uid(7, 5), name: 'Ли', payer: { name: 'Наталья Ли', phone: '+77015550007' }, discount_pct: 10, paid_this_month: 117000, debt: 0 },
  { id: uid(7, 6), name: 'Бек', payer: { name: 'Айгуль Бек', phone: '+77015550008' }, discount_pct: 0, paid_this_month: 56000, debt: 0 },
  // Долг: абонемент оформлен, деньги обещали донести — карточка обязана это показывать
  { id: uid(7, 7), name: 'Абишева', payer: { name: 'Гульмира Абишева', phone: '+77015550009' }, discount_pct: 0, paid_this_month: 0, debt: 27000 },
  { id: uid(7, 8), name: 'Ким (Ольга)', payer: { name: 'Ольга Ким', phone: '+77015550010' }, discount_pct: 0, paid_this_month: 52000, debt: 0 },
  { id: uid(7, 9), name: 'Жанат', payer: { name: 'Сауле Жанат', phone: '+77015550011' }, discount_pct: 0, paid_this_month: 54000, debt: 0 },
  { id: uid(7, 10), name: 'Ер', payer: { name: 'Динара Ер', phone: '+77015550012' }, discount_pct: 0, paid_this_month: 56000, debt: 0 },
];

const F = {
  sagyndyk: FAMILIES[0].id,
  ahmetov: FAMILIES[1].id,
  kim: FAMILIES[2].id,
  ospanov: FAMILIES[3].id,
  li: FAMILIES[4].id,
  bek: FAMILIES[5].id,
  abisheva: FAMILIES[6].id,
  kim_o: FAMILIES[7].id,
  zhanat: FAMILIES[8].id,
  er: FAMILIES[9].id,
};

export const findFamily = (id: string | null): MockFamily | undefined =>
  id ? FAMILIES.find((f) => f.id === id) : undefined;

/* ---------- абонементы и ученики ---------- */

export interface MockHold {
  id: string;
  /** Первый день заморозки, "2026-08-14". */
  from: string;
  /** Последний день интервала (полуинтервал, как daterange в базе). */
  to: string;
  days: number;
  reason: string | null;
}

export interface MockSubscription {
  id: string;
  plan_id: string;
  plan_name: string;
  lessons_total: number;
  lessons_balance: number;
  makeups_balance: number;
  price: number;
  discount_pct: number;
  valid_from: string;
  valid_until: string;
  status: string;
  rules: MockRules;
  holds: MockHold[];
}

/** Профиль ученика для карточки и поиска. */
export interface MockStudent {
  id: string;
  name: string;
  /** null — действующего абонемента нет, занятие идёт разовой оплатой. */
  subscription: MockSubscription | null;
  age: number;
  discipline: string;
  teacher_id: string;
  branch_id: string;
  started_on: string;
  status: string;
  family_id: string | null;
}

interface MockProfile {
  age: number;
  discipline: string;
  teacher_id: string;
  branch_id?: string;
  started_on?: string;
  family_id?: string | null;
  status?: string;
}

let subSeq = 0;
const sub = (
  planId: string,
  balance: number,
  makeups = 0,
  validFrom = '2026-08-01',
  validUntil = '2026-08-31',
  total?: number,
): MockSubscription => {
  const plan = findPlan(planId)!;
  return {
    id: uid(6, ++subSeq),
    plan_id: plan.id,
    plan_name: plan.name,
    lessons_total: total ?? plan.lessons_count,
    lessons_balance: balance,
    makeups_balance: makeups,
    price: plan.price,
    discount_pct: 0,
    valid_from: validFrom,
    valid_until: validUntil,
    status: 'active',
    // Копия правил, а не ссылка: заморозка одного абонемента не должна
    // менять лимит у всех остальных.
    rules: { ...DEFAULT_RULES },
    holds: [],
  };
};

const P = {
  drums8: PLANS[0].id,
  drums12: PLANS[1].id,
  guitar8: PLANS[4].id,
  guitar4: PLANS[5].id,
  vocal8: PLANS[6].id,
  bass8: PLANS[7].id,
};

let stuSeq = 0;
const student = (name: string, subscription: MockSubscription | null, profile: MockProfile): MockStudent => ({
  id: uid(4, ++stuSeq),
  name,
  subscription,
  age: profile.age,
  discipline: profile.discipline,
  teacher_id: profile.teacher_id,
  branch_id: profile.branch_id ?? BRANCH_AF,
  started_on: profile.started_on ?? '2026-02-04',
  status: profile.status ?? 'active',
  family_id: profile.family_id ?? null,
});

export const STUDENTS: MockStudent[] = [
  student('Тимур Ахметов', sub(P.guitar8, 4), { age: 10, discipline: 'Гитара', teacher_id: T.fedko, family_id: F.ahmetov }),
  // герой прототипа: 5 из 8, +1 отработка, семья с двумя детьми и скидкой
  student('Амина Сагындык', sub(P.drums8, 5, 1), { age: 9, discipline: 'Барабаны', teacher_id: T.sharapov, family_id: F.sagyndyk }),
  student('Даниал Ким', sub(P.bass8, 6), { age: 13, discipline: 'Бас', teacher_id: T.merenkov, family_id: F.kim }),
  student('Ержан Оспанов', sub(P.guitar4, 1), { age: 31, discipline: 'Гитара', teacher_id: T.sharapov, family_id: F.ospanov }), // после отметки останется 0 — сработает alert
  student('Алиса Ким', null, { age: 7, discipline: 'Барабаны', teacher_id: T.madratov, started_on: '2026-08-12' }), // пробный урок, абонемента нет
  student('Сабина Нурлан', sub(P.guitar8, 3), { age: 11, discipline: 'Гитара', teacher_id: T.fedko }),
  student('Айсулу Бек', sub(P.vocal8, 7), { age: 14, discipline: 'Вокал', teacher_id: T.isenova, family_id: F.bek }),
  student('Марк Ли', sub(P.drums12, 9, 0, '2026-07-15', '2026-09-30'), { age: 12, discipline: 'Барабаны', teacher_id: T.sharapov, family_id: F.li }),
  student('Амир Жанат', sub(P.drums8, 2), { age: 10, discipline: 'Барабаны', teacher_id: T.sharapov, family_id: F.zhanat }),
  student('Ольга Ким', sub(P.guitar8, 8), { age: 34, discipline: 'Гитара', teacher_id: T.fedko, family_id: F.kim_o }),
  student('Дмитрий Со', sub(P.vocal8, 0), { age: 28, discipline: 'Вокал', teacher_id: T.isenova }), // нулевой остаток — списание вернёт 422
  student('Мадина Абишева', sub(P.guitar8, 2), { age: 10, discipline: 'Гитара', teacher_id: T.merenkov, family_id: F.abisheva }),
  student('Санжар Тлеу', sub(P.guitar8, 5), { age: 13, discipline: 'Гитара', teacher_id: T.merenkov }),
  student('Дамир Ералы', null, { age: 15, discipline: 'Гитара', teacher_id: T.merenkov, started_on: '2026-08-10' }),
  student('Камила Ер', sub(P.vocal8, 11, 0, '2026-08-01', '2027-01-31', 12), { age: 16, discipline: 'Вокал', teacher_id: T.isenova, branch_id: BRANCH_AB, family_id: F.er }),
  student('Арай Токтар', sub(P.drums8, 6), { age: 9, discipline: 'Барабаны', teacher_id: T.madratov, branch_id: BRANCH_AB }),
  student('Рустам Ли', sub(P.guitar8, 3), { age: 9, discipline: 'Гитара', teacher_id: T.fedko, branch_id: BRANCH_AB, family_id: F.li }),
  student('Ильяс Абен', null, { age: 8, discipline: 'Барабаны', teacher_id: T.madratov, branch_id: BRANCH_AB, started_on: '2026-08-12' }),
  student('Данияр Аман', sub(P.guitar8, 7), { age: 12, discipline: 'Гитара', teacher_id: T.merenkov, branch_id: BRANCH_AB }),
  // Второй ребёнок семьи Сагындык. В расписании 12 августа его нет — он приходит
  // по другим дням, но в карточке сестры и в поиске по маме обязан находиться.
  student('Тимур Сагындык', sub(P.guitar8, 3), { age: 12, discipline: 'Гитара', teacher_id: T.fedko, family_id: F.sagyndyk }),
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

/* ==========================================================================
   Этап 2: журнал абонемента, заметки и отработки
   ========================================================================== */

/** «Сегодня» мок-режима — тот же день, что и в расписании. */
export const MOCK_TODAY = DEMO_DATE;

/** Сдвиг календарной даты. Через UTC — иначе дата поползёт от пояса браузера. */
export const addDays = (date: string, days: number): string => {
  const [y, m, d] = date.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
};

/** Разница в днях. Заморозка считается полуинтервалом, как daterange в базе. */
export const daysBetween = (from: string, to: string): number => {
  const parse = (s: string) => {
    const [y, m, d] = s.split('-').map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((parse(to) - parse(from)) / 86400000);
};

export interface MockLedgerEntry {
  id: number;
  date: string;
  kind:
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
  /** Человеческая формулировка — её и читает администратор в споре о занятии. */
  title: string;
  teacher: string | null;
  lessons_delta: number;
  makeups_delta: number;
  amount: number | null;
}

export interface MockNote {
  date: string;
  author: string;
  body: string;
  homework: string;
  tags: string[];
}

export interface MockMakeup {
  id: string;
  granted_for: string;
  expires_on: string;
  used_at: string | null;
}

let entrySeq = 0;
export const nextEntryId = (): number => ++entrySeq;

let makeupSeq = 0;
export const nextMakeupId = (): string => uid(9, ++makeupSeq);

/** Журнал по ученику, старые записи первыми. Мок-сервер дописывает в конец. */
export const LEDGER: Record<string, MockLedgerEntry[]> = {};
export const NOTES: Record<string, MockNote[]> = {};
export const MAKEUPS: Record<string, MockMakeup[]> = {};

/**
 * Журнал для всех, кроме Амины, собирается из состояния абонемента: продажа
 * плюс столько списаний, сколько занятий уже израсходовано. Иначе остаток
 * в карточке не сходился бы с журналом, а именно этим экраном администратор
 * отвечает на вопрос «куда делось занятие».
 */
function seedGenericLedger(student: MockStudent): MockLedgerEntry[] {
  const s = student.subscription;
  if (!s) return [];
  const teacher = findTeacher(student.teacher_id).name.split(' ')[1];
  const used = s.lessons_total - s.lessons_balance;
  const entries: MockLedgerEntry[] = [
    {
      id: nextEntryId(),
      date: s.valid_from,
      kind: 'purchase',
      title: 'Оплата абонемента · Kaspi',
      teacher: null,
      lessons_delta: s.lessons_total,
      makeups_delta: 0,
      amount: s.price,
    },
  ];
  // Занятия раскладываем назад от вчерашнего дня по два в неделю
  for (let i = used; i > 0; i--) {
    entries.push({
      id: nextEntryId(),
      date: addDays(MOCK_TODAY, -1 - (i - 1) * 3),
      kind: 'charge',
      title: 'Занятие проведено',
      teacher,
      lessons_delta: -1,
      makeups_delta: 0,
      amount: null,
    });
  }
  if (s.makeups_balance > 0) {
    entries.push({
      id: nextEntryId(),
      date: addDays(MOCK_TODAY, -10),
      kind: 'makeup_grant',
      title: 'Отмена заранее → отработка',
      teacher,
      lessons_delta: 0,
      makeups_delta: s.makeups_balance,
      amount: null,
    });
  }
  return entries.sort((a, b) => a.date.localeCompare(b.date));
}

const AMINA = S('Амина Сагындык');

for (const s of STUDENTS) LEDGER[s.id] = seedGenericLedger(s);

/** Журнал Амины — тот же, что в прототипе: покупка, отработка, прогул, занятия. */
LEDGER[AMINA] = [
  { id: nextEntryId(), date: '2026-08-01', kind: 'purchase', title: 'Оплата абонемента · Kaspi', teacher: null, lessons_delta: 8, makeups_delta: 0, amount: 54000 },
  { id: nextEntryId(), date: '2026-08-02', kind: 'makeup_grant', title: 'Отмена за 2 дня → отработка', teacher: 'Шарапов', lessons_delta: 0, makeups_delta: 1, amount: null },
  { id: nextEntryId(), date: '2026-08-04', kind: 'charge', title: 'Занятие проведено', teacher: 'Шарапов', lessons_delta: -1, makeups_delta: 0, amount: null },
  { id: nextEntryId(), date: '2026-08-05', kind: 'charge', title: 'Прогул без предупреждения', teacher: 'Шарапов', lessons_delta: -1, makeups_delta: 0, amount: null },
  { id: nextEntryId(), date: '2026-08-07', kind: 'charge', title: 'Занятие проведено', teacher: 'Шарапов', lessons_delta: -1, makeups_delta: 0, amount: null },
];

MAKEUPS[AMINA] = [
  { id: nextMakeupId(), granted_for: '2026-08-02', expires_on: '2026-09-01', used_at: null },
];

NOTES[AMINA] = [
  {
    date: '2026-08-07',
    author: 'Дмитрий Шарапов',
    body: 'Разобрали сбивку на 16-х. Правая рука зажимается на скорости выше 90 bpm — держим метроном на 80.',
    homework: '10 минут в день на восьмые, правая нога на кике.',
    tags: ['Nirvana — Smells Like Teen Spirit', 'Рудимент: Single Paradiddle', '80 bpm'],
  },
  {
    date: '2026-08-04',
    author: 'Дмитрий Шарапов',
    body: 'Впервые сыграла припев целиком под минус. Готовим к отчётному концерту 27 сентября.',
    homework: 'Играть припев под минус два раза в день.',
    tags: ['Отчётный концерт', 'Игра под минус'],
  },
  {
    date: '2026-07-31',
    author: 'Дмитрий Шарапов',
    body: 'Постановка правой ноги на кике. Дома — 10 минут в день на восьмые.',
    homework: '10 минут в день на восьмые.',
    tags: ['Техника: bass drum', 'ДЗ: 10 мин/день'],
  },
];

NOTES[S('Марк Ли')] = [
  {
    date: '2026-08-10',
    author: 'Дмитрий Шарапов',
    body: 'Держит темп ровно, но теряет счёт на переходах. Работаем по клику 100 bpm.',
    homework: 'Метроном 100 bpm, 15 минут.',
    tags: ['Muse — Hysteria', '100 bpm'],
  },
];
