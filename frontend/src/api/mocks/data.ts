import type { AttendanceMark, LeadSource, LeadStage, LessonKind, LessonNote, LostReason } from '../types';

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

/** То же, но с буквенным тегом: цифры кончились на этапе 2. */
const uidx = (tag: string, n: number) =>
  `${tag.repeat(8)}-0000-4000-8000-${String(n).padStart(12, '0')}`;

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
  /** Характеристики кабинета из `room.features`: барабаны без установки не поставить. */
  features: Record<string, boolean>;
}

export const ROOMS: MockRoom[] = [
  { id: uid(3, 1), name: 'Барабанная A', branch_id: BRANCH_AF, features: { drum_kit: true, soundproof: true } },
  { id: uid(3, 2), name: 'Класс 1', branch_id: BRANCH_AF, features: { piano: true } },
  { id: uid(3, 3), name: 'Класс 2', branch_id: BRANCH_AF, features: {} },
  { id: uid(3, 4), name: 'Барабанная B', branch_id: BRANCH_AB, features: { drum_kit: true, soundproof: true } },
  { id: uid(3, 5), name: 'Класс 3', branch_id: BRANCH_AB, features: { piano: true } },
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
  // Оплатил половину и обещал донести остаток — самый частый вид долга
  { id: uid(7, 4), name: 'Оспанов', payer: { name: 'Ержан Оспанов', phone: '+77015550006' }, discount_pct: 0, paid_this_month: 14000, debt: 14000 },
  { id: uid(7, 5), name: 'Ли', payer: { name: 'Наталья Ли', phone: '+77015550007' }, discount_pct: 10, paid_this_month: 117000, debt: 0 },
  { id: uid(7, 6), name: 'Бек', payer: { name: 'Айгуль Бек', phone: '+77015550008' }, discount_pct: 0, paid_this_month: 56000, debt: 0 },
  // Долг: абонемент оформлен, деньги обещали донести — карточка обязана это показывать
  { id: uid(7, 7), name: 'Абишева', payer: { name: 'Гульмира Абишева', phone: '+77015550009' }, discount_pct: 0, paid_this_month: 0, debt: 27000 },
  { id: uid(7, 8), name: 'Ким (Ольга)', payer: { name: 'Ольга Ким', phone: '+77015550010' }, discount_pct: 0, paid_this_month: 52000, debt: 0 },
  { id: uid(7, 9), name: 'Жанат', payer: { name: 'Сауле Жанат', phone: '+77015550011' }, discount_pct: 0, paid_this_month: 40500, debt: 13500 },
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
  // Остаток 2 — ровно порог «кончается»: без него кабинет родителя нечем было бы
  // проверить на предупреждении и заявке на продление, а это половина этапа 5.
  student('Тимур Сагындык', sub(P.guitar8, 2), { age: 12, discipline: 'Гитара', teacher_id: T.fedko, family_id: F.sagyndyk }),
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
  /** Пробный урок ссылается на заявку, а не на ученика (схема, 004_sales). */
  lead_id?: string;
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
  /**
   * Внутренняя пометка преподавателя: администратору видна, родителю — нет.
   * В базе это `visible_to_family = false`, и выбирается оно условием
   * в запросе, а не фильтром поверх ответа. Флаг нужен здесь затем же: без
   * него мок кабинета отдавал бы наружу всё подряд и выглядел бы рабочим,
   * а утечка обнаружилась бы уже на живом сервере.
   */
  internal?: boolean;
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
  // Внутренняя пометка: в карточке администратора она есть, в кабинете
  // родителя её быть не должно — этим и проверяется правило видимости.
  {
    date: '2026-08-05',
    author: 'Дмитрий Шарапов',
    body: 'Мама давит на результат, ребёнок зажимается. На отчётный концерт пока не ставим.',
    homework: '',
    tags: ['Внутреннее'],
    internal: true,
  },
];

// Второй ребёнок семьи Сагындык: без заметок кабинет родителя показал бы
// пустой репертуар у обоих детей, и работающий блок было бы не с чем сверить.
NOTES[S('Тимур Сагындык')] = [
  {
    date: '2026-08-08',
    author: 'Глеб Федько',
    body: 'Барре на пятом ладу берёт чисто, но теряет темп в переходе на припев. Разбирали бой «восьмёрка».',
    homework: 'Смена аккордов Am — F по метроному 70 bpm, 10 минут.',
    tags: ['Кино — Кукушка', 'Барре F', '70 bpm'],
  },
  {
    date: '2026-08-01',
    author: 'Глеб Федько',
    body: 'Начали разбирать перебор. Правая рука пока зажата, следим за большим пальцем.',
    homework: 'Перебор «шестёрка» на открытых струнах.',
    tags: ['Перебор «шестёрка»'],
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

/* ==========================================================================
   Этап 3: направления, пользователи и воронка заявок
   ========================================================================== */

export interface MockDiscipline {
  id: string;
  name: string;
  /** Барабаны с 5 лет, скрипка с 7 — из `discipline.min_age`. */
  min_age: number | null;
  /** Требования к кабинету, сверяются с `room.features`. */
  room_reqs: Record<string, boolean>;
}

export const DISCIPLINES: MockDiscipline[] = [
  { id: uidx('b', 1), name: 'Барабаны', min_age: 5, room_reqs: { drum_kit: true } },
  { id: uidx('b', 2), name: 'Гитара', min_age: 6, room_reqs: {} },
  { id: uidx('b', 3), name: 'Бас', min_age: 10, room_reqs: {} },
  { id: uidx('b', 4), name: 'Вокал', min_age: 6, room_reqs: {} },
  { id: uidx('b', 5), name: 'Фортепиано', min_age: 5, room_reqs: { piano: true } },
  { id: uidx('b', 6), name: 'Скрипка', min_age: 7, room_reqs: {} },
  { id: uidx('b', 7), name: 'Укулеле', min_age: 6, room_reqs: {} },
  { id: uidx('b', 8), name: 'Перкуссия', min_age: 7, room_reqs: {} },
];

export const findDiscipline = (id: string | null): MockDiscipline | undefined =>
  id ? DISCIPLINES.find((d) => d.id === id) : undefined;

export const disciplineByName = (name: string | null): MockDiscipline | undefined =>
  name ? DISCIPLINES.find((d) => d.name.toLowerCase() === name.trim().toLowerCase()) : undefined;

const D = {
  drums: DISCIPLINES[0].id,
  guitar: DISCIPLINES[1].id,
  bass: DISCIPLINES[2].id,
  vocal: DISCIPLINES[3].id,
  piano: DISCIPLINES[4].id,
  violin: DISCIPLINES[5].id,
};

export interface MockUser {
  id: string;
  name: string;
}

/** Администраторы школы — на них назначаются заявки. */
export const USERS: MockUser[] = [
  { id: uidx('c', 1), name: 'Айгерим Дюсенова' },
  { id: uidx('c', 2), name: 'Асель Нурланова' },
];

/** Текущий пользователь: от его имени пишется история стадий. */
export const CURRENT_USER = USERS[0];

export const findUser = (id: string | null): MockUser | undefined =>
  id ? USERS.find((u) => u.id === id) : undefined;

export interface MockStageChange {
  at: string;
  from: LeadStage | null;
  to: LeadStage;
  by: string | null;
}

export interface MockLead {
  id: string;
  name: string;
  phone: string;
  student_name: string | null;
  student_age: number | null;
  discipline_id: string | null;
  branch_id: string | null;
  stage: LeadStage;
  lost_reason: LostReason | null;
  source: LeadSource;
  utm: Record<string, string>;
  promo_code: string | null;
  external_id: string | null;
  assigned_to: string | null;
  /** ISO со смещением филиала: «перезвонить в 18:00». */
  next_action_at: string | null;
  contact_attempts: number;
  created_at: string;
  comment: string | null;
  /** Ссылка на занятие-пробный в расписании. */
  trial_lesson_id: string | null;
  history: MockStageChange[];
  student_id: string | null;
  person_id: string | null;
}

/** Момент времени в дне мок-режима: день + местное время. */
const at = (date: string, hhmm: string) => `${date}T${hhmm}:00${TZ_OFFSET}`;

let leadSeq = 0;

interface LeadInput extends Omit<MockLead, 'id' | 'history' | 'utm' | 'promo_code' | 'external_id' | 'student_id' | 'person_id'> {
  utm?: Record<string, string>;
  promo_code?: string | null;
  external_id?: string | null;
  student_id?: string | null;
  person_id?: string | null;
  /** Даты переходов по стадиям, по одной на шаг от `new` до текущей. */
  stage_dates: string[];
}

/**
 * История стадий строится из дат переходов, а не пишется руками: отчёт
 * по воронке считается именно из неё, и любая нестыковка между стадией
 * и историей сразу исказила бы конверсию.
 */
const lead = (input: LeadInput): MockLead => {
  const path: LeadStage[] = ['new', 'contacting', 'trial_booked', 'trial_held', 'won'];
  const target = input.stage;
  const steps: LeadStage[] =
    target === 'lost'
      ? ['new', 'lost']
      : path.slice(0, path.indexOf(target) + 1);

  const history: MockStageChange[] = steps.map((stage, index) => ({
    at: input.stage_dates[index] ?? input.stage_dates[input.stage_dates.length - 1],
    from: index === 0 ? null : steps[index - 1],
    to: stage,
    by: index === 0 ? null : input.assigned_to ?? CURRENT_USER.id,
  }));

  const { stage_dates: _stageDates, ...rest } = input;
  return {
    id: uidx('a', ++leadSeq),
    utm: {},
    promo_code: null,
    external_id: null,
    student_id: null,
    person_id: null,
    ...rest,
    history,
  };
};

const TRIAL_ALISA = LESSONS.find((l) => l.title === 'Алиса Ким')!.id;
const TRIAL_ILYAS = LESSONS.find((l) => l.title === 'Ильяс Абен')!.id;
const A = USERS[0].id;
const B = USERS[1].id;

/**
 * Воронка повторяет доску из прототипа: заявки на всех стадиях, разные
 * источники, отказы с причинами и один пробный в занятом кабинете.
 */
export const LEADS: MockLead[] = [
  // --- новые ---
  lead({
    name: 'Гульмира Абишева', phone: '+77015550009', student_name: 'Мадина', student_age: 10,
    discipline_id: D.vocal, branch_id: BRANCH_AF, stage: 'new', lost_reason: null, source: 'site_form',
    assigned_to: A, next_action_at: at('2026-08-11', '17:00'), contact_attempts: 2,
    created_at: at('2026-08-10', '09:12'), comment: 'Просила перезвонить после обеда',
    trial_lesson_id: null, stage_dates: [at('2026-08-10', '09:12')],
  }),
  lead({
    name: 'Ержан Оспанов', phone: '+77015550006', student_name: 'Ержан', student_age: 31,
    discipline_id: D.guitar, branch_id: BRANCH_AF, stage: 'new', lost_reason: null, source: 'instagram',
    utm: { utm_source: 'instagram', utm_campaign: 'august' },
    assigned_to: null, next_action_at: null, contact_attempts: 0,
    created_at: at('2026-08-12', '08:40'), comment: 'Взрослый, хочет вечером после 19',
    trial_lesson_id: null, stage_dates: [at('2026-08-12', '08:40')],
  }),
  // Возраст ниже минимального: барабаны с 5 лет. Не ошибка — предупреждение
  lead({
    name: 'Асем Досым', phone: '+77015550021', student_name: 'Аяна', student_age: 4,
    discipline_id: D.drums, branch_id: BRANCH_AF, stage: 'new', lost_reason: null, source: 'telegram_bot',
    external_id: 'tg-90211', assigned_to: null, next_action_at: null, contact_attempts: 0,
    created_at: at('2026-08-12', '10:05'), comment: 'Очень просит на барабаны как старший брат',
    trial_lesson_id: null, stage_dates: [at('2026-08-12', '10:05')],
  }),

  // --- дозвон ---
  lead({
    name: 'Айнур Тлеу', phone: '+77015550022', student_name: 'Санжар', student_age: 13,
    discipline_id: D.drums, branch_id: BRANCH_AF, stage: 'contacting', lost_reason: null, source: 'whatsapp',
    assigned_to: A, next_action_at: at('2026-08-13', '11:00'), contact_attempts: 1,
    created_at: at('2026-08-09', '19:30'), comment: 'Ждёт ответа мамы',
    trial_lesson_id: null, stage_dates: [at('2026-08-09', '19:30'), at('2026-08-10', '12:15')],
  }),
  lead({
    name: 'Ольга Ким', phone: '+77015550010', student_name: 'Ольга', student_age: 34,
    discipline_id: D.piano, branch_id: BRANCH_AF, stage: 'contacting', lost_reason: null, source: 'referral',
    assigned_to: B, next_action_at: at('2026-08-12', '18:00'), contact_attempts: 1,
    created_at: at('2026-08-11', '13:05'), comment: 'Перезвонить в 18:00',
    trial_lesson_id: null, stage_dates: [at('2026-08-11', '13:05'), at('2026-08-11', '15:40')],
  }),

  // --- пробный назначен ---
  lead({
    name: 'Сауле Ким', phone: '+77015551234', student_name: 'Алиса', student_age: 7,
    discipline_id: D.drums, branch_id: BRANCH_AF, stage: 'trial_booked', lost_reason: null, source: 'telegram_bot',
    external_id: 'tg-88104', assigned_to: A, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-08-11', '14:20'), comment: null,
    trial_lesson_id: TRIAL_ALISA,
    stage_dates: [at('2026-08-11', '14:20'), at('2026-08-11', '16:05'), at('2026-08-11', '16:40')],
  }),
  lead({
    name: 'Абен Ильясов', phone: '+77015550023', student_name: 'Ильяс', student_age: 8,
    discipline_id: D.drums, branch_id: BRANCH_AB, stage: 'trial_booked', lost_reason: null, source: 'instagram',
    assigned_to: B, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-08-10', '11:00'), comment: 'Пробный оплачен, 2 000 ₸',
    trial_lesson_id: TRIAL_ILYAS,
    stage_dates: [at('2026-08-10', '11:00'), at('2026-08-10', '14:00'), at('2026-08-11', '10:20')],
  }),

  // --- пробный проведён ---
  lead({
    name: 'Ералы Айдос', phone: '+77015550024', student_name: 'Айдос', student_age: 15,
    discipline_id: D.guitar, branch_id: BRANCH_AF, stage: 'trial_held', lost_reason: null, source: 'site_form',
    assigned_to: A, next_action_at: at('2026-08-12', '12:00'), contact_attempts: 2,
    created_at: at('2026-08-07', '10:10'), comment: 'Думают два дня',
    trial_lesson_id: null,
    stage_dates: [at('2026-08-07', '10:10'), at('2026-08-07', '12:00'), at('2026-08-08', '09:00'), at('2026-08-10', '15:00')],
  }),
  // Телефон совпадает с плательщиком семьи Бек: конверсия обязана переиспользовать
  // семью, а не завести вторую, — иначе пропадёт скидка за второго ребёнка
  lead({
    name: 'Айгуль Бек', phone: '+77015550008', student_name: 'Алихан', student_age: 9,
    discipline_id: D.drums, branch_id: BRANCH_AF, stage: 'trial_held', lost_reason: null, source: 'referral',
    assigned_to: B, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-08-06', '17:45'), comment: 'Второй ребёнок, сестра уже занимается вокалом',
    trial_lesson_id: null,
    stage_dates: [at('2026-08-06', '17:45'), at('2026-08-06', '19:00'), at('2026-08-07', '11:00'), at('2026-08-09', '16:00')],
  }),

  // --- абонемент куплен ---
  lead({
    name: 'Наталья Ли', phone: '+77015550007', student_name: 'Марк', student_age: 12,
    discipline_id: D.drums, branch_id: BRANCH_AF, stage: 'won', lost_reason: null, source: 'instagram',
    assigned_to: A, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-07-28', '12:00'), comment: null,
    trial_lesson_id: null, student_id: findStudentIdByName('Марк Ли'),
    stage_dates: [at('2026-07-28', '12:00'), at('2026-07-28', '14:30'), at('2026-07-29', '10:00'), at('2026-07-31', '17:00'), at('2026-08-01', '11:20')],
  }),
  lead({
    name: 'Ирина Ким', phone: '+77015550005', student_name: 'Даниал', student_age: 13,
    discipline_id: D.bass, branch_id: BRANCH_AF, stage: 'won', lost_reason: null, source: 'telegram_bot',
    external_id: 'tg-87330', assigned_to: A, next_action_at: null, contact_attempts: 2,
    created_at: at('2026-07-25', '09:30'), comment: null,
    trial_lesson_id: null, student_id: findStudentIdByName('Даниал Ким'),
    stage_dates: [at('2026-07-25', '09:30'), at('2026-07-25', '18:00'), at('2026-07-27', '12:00'), at('2026-07-29', '13:00'), at('2026-08-01', '10:00')],
  }),
  lead({
    name: 'Динара Ер', phone: '+77015550012', student_name: 'Камила', student_age: 16,
    discipline_id: D.vocal, branch_id: BRANCH_AB, stage: 'won', lost_reason: null, source: 'referral',
    promo_code: 'RS25', assigned_to: B, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-07-20', '15:00'), comment: 'Абонемент 6 месяцев',
    trial_lesson_id: null, student_id: findStudentIdByName('Камила Ер'),
    stage_dates: [at('2026-07-20', '15:00'), at('2026-07-20', '16:10'), at('2026-07-22', '11:00'), at('2026-07-24', '18:00'), at('2026-07-26', '12:30')],
  }),

  // --- отказы: без причины воронка не показывает, что чинить ---
  lead({
    name: 'Арман Сеит', phone: '+77015550025', student_name: 'Арман', student_age: 11,
    discipline_id: D.guitar, branch_id: BRANCH_AF, stage: 'lost', lost_reason: 'price', source: 'site_form',
    assigned_to: A, next_action_at: null, contact_attempts: 2,
    created_at: at('2026-07-30', '10:00'), comment: 'Дорого, вернутся осенью',
    trial_lesson_id: null, stage_dates: [at('2026-07-30', '10:00'), at('2026-08-02', '12:00')],
  }),
  lead({
    name: 'Жанна Ким', phone: '+77015550026', student_name: 'Жанна', student_age: 27,
    discipline_id: D.violin, branch_id: BRANCH_AF, stage: 'lost', lost_reason: 'schedule', source: 'whatsapp',
    assigned_to: B, next_action_at: null, contact_attempts: 1,
    created_at: at('2026-08-01', '14:00'), comment: 'Работает до 21',
    trial_lesson_id: null, stage_dates: [at('2026-08-01', '14:00'), at('2026-08-04', '09:30')],
  }),
  lead({
    name: 'Нурлан Бек', phone: '+77015550027', student_name: 'Нурлан', student_age: 9,
    discipline_id: D.drums, branch_id: BRANCH_AB, stage: 'lost', lost_reason: 'no_answer', source: 'telegram_bot',
    assigned_to: A, next_action_at: null, contact_attempts: 4,
    created_at: at('2026-07-27', '11:15'), comment: 'Четыре попытки дозвона',
    trial_lesson_id: null, stage_dates: [at('2026-07-27', '11:15'), at('2026-08-03', '10:00')],
  }),
];

/** Ученик, в которого превратилась выигранная заявка: карточка на него ссылается. */
function findStudentIdByName(name: string): string | null {
  return STUDENTS.find((s) => s.name === name)?.id ?? null;
}

export const findLead = (id: string): MockLead | undefined => LEADS.find((l) => l.id === id);

export const nextLeadId = (): string => uidx('a', ++leadSeq);

/* ==========================================================================
   Этап 4: начисления, закрытые периоды и платежи

   Начисление — строка «преподавателю за занятие», штамп периода на ней
   означает «деньги посчитаны и отданы». Открытого периода не существует:
   пока месяц не закрыт, ведомость собирается из непроштампованных строк
   (contract-v4, «Ведомость»), поэтому фикстура хранит именно штамп,
   а не флаг «закрыто».
   ========================================================================== */

export interface MockPayrollPeriod {
  id: string;
  from: string;
  to: string;
  closed_at: string;
  closed_by: string;
}

export interface MockAccrual {
  id: number;
  /** Дата занятия в поясе филиала — по ней начисление попадает в период. */
  date: string;
  start: string;
  teacher_id: string;
  branch_id: string;
  student_name: string;
  discipline: string;
  duration_min: number;
  mark: AttendanceMark | null;
  /** Ставка на момент занятия — снимок расчёта, а не сегодняшняя настройка. */
  rate: number;
  amount: number;
  /** Занятие, премия, корректировка (снятое начисление) или удержание. */
  kind: 'lesson' | 'bonus' | 'correction' | 'deduction';
  /** Штамп закрытого периода. null — строка ещё не выплачена. */
  period_id: string | null;
}

/** Закрытые месяцы. Июль закрыт первого августа — обычный ход вещей. */
export const PAYROLL_PERIODS: MockPayrollPeriod[] = [
  { id: uidx('p', 1), from: '2026-06-01', to: '2026-06-30', closed_at: `2026-07-01T10:00:00${TZ_OFFSET}`, closed_by: 'Асель Нурланова' },
  { id: uidx('p', 2), from: '2026-07-01', to: '2026-07-31', closed_at: `2026-08-01T10:00:00${TZ_OFFSET}`, closed_by: 'Асель Нурланова' },
];

let periodSeq = PAYROLL_PERIODS.length;
export const nextPeriodId = (): string => uidx('p', ++periodSeq);

/**
 * Псевдослучайность с зерном: набор начислений обязан быть одинаковым
 * при каждой перезагрузке, иначе «проверил вчера» ничего не значит.
 */
function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

const ACCRUAL_MARKS: { mark: AttendanceMark; share: number }[] = [
  { mark: 'came', share: 0.78 },
  { mark: 'late', share: 0.06 },
  { mark: 'no_show', share: 0.09 },
  { mark: 'cancelled_early', share: 0.04 },
  { mark: 'cancelled_teacher', share: 0.03 },
];

/** Ставка зависит от длительности: 85 минут дороже 55. Отсюда `rate_varies`. */
const rateFor = (teacherRate: number, durationMin: number): number =>
  durationMin >= 85 ? Math.round(teacherRate * 1.5) : teacherRate;

/**
 * Сколько платят преподавателю при этой отметке. Правила школы: прогул
 * оплачивается полностью (`pay_teacher_on_no_show`), отмена заранее — нет,
 * отмена преподавателем — нет.
 */
const payShare = (mark: AttendanceMark): number =>
  mark === 'came' || mark === 'late' || mark === 'no_show' ? 1 : mark === 'cancelled_late' ? 1 : 0;

let accrualSeq = 30000;

/**
 * Начисления за три месяца: июнь и июль закрыты, август идёт.
 * Ученики берутся из фикстур этапа 2 — ведомость и карточка ученика
 * обязаны говорить об одних и тех же людях.
 */
function seedAccruals(): MockAccrual[] {
  const rows: MockAccrual[] = [];
  const random = seeded(20260812);
  const withSub = STUDENTS.filter((s) => s.subscription !== null);

  const months: { first: string; last: string; period: string | null }[] = [
    { first: '2026-06-01', last: '2026-06-30', period: PAYROLL_PERIODS[0].id },
    { first: '2026-07-01', last: '2026-07-31', period: PAYROLL_PERIODS[1].id },
    // Август идёт: строки без штампа, до сегодняшнего дня включительно
    { first: '2026-08-01', last: MOCK_TODAY, period: null },
  ];

  for (const month of months) {
    const days = daysBetween(month.first, month.last) + 1;
    for (let day = 0; day < days; day++) {
      const date = addDays(month.first, day);
      const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
      if (weekday === 0) continue; // воскресенье школа не работает
      for (const teacher of TEACHERS) {
        // Три-пять занятий в день у каждого преподавателя
        const count = 3 + Math.floor(random() * 3);
        let previousStart = '';
        for (let i = 0; i < count; i++) {
          const student = withSub[Math.floor(random() * withSub.length)];
          const duration = random() < 0.18 ? 85 : 55;
          const rate = rateFor(teacher.rate, duration);
          const roll = random();
          let acc = 0;
          let mark: AttendanceMark = 'came';
          for (const option of ACCRUAL_MARKS) {
            acc += option.share;
            if (roll <= acc) {
              mark = option.mark;
              break;
            }
          }
          // Иногда второй ученик встаёт в тот же час — это групповое занятие:
          // занятие одно, а начислений два. Ради этой разницы в ведомости
          // и стоят две отдельные колонки.
          const group = i > 0 && random() < 0.16;
          const start = group ? previousStart : `${String(10 + ((i * 2 + day) % 10)).padStart(2, '0')}:00`;
          previousStart = start;
          rows.push({
            id: ++accrualSeq,
            date,
            start,
            teacher_id: teacher.id,
            branch_id: student.branch_id,
            student_name: student.name,
            discipline: student.discipline,
            duration_min: duration,
            mark,
            rate,
            amount: Math.round(rate * payShare(mark)),
            kind: 'lesson',
            period_id: month.period,
          });
        }
      }
    }
  }

  /**
   * Три правки за июль, сделанные после закрытия месяца: отметки поставили
   * задним числом, штампа они не получили и приедут в августовскую ведомость
   * строками `carried_over`. Ради них экран и показывает эту колонку
   * отдельно — бухгалтер не должен искать расхождение.
   */
  const late: { teacher: string; date: string; student: string; discipline: string; mark: AttendanceMark }[] = [
    { teacher: TEACHERS[0].id, date: '2026-07-28', student: 'Амина Сагындык', discipline: 'Барабаны', mark: 'came' },
    { teacher: TEACHERS[0].id, date: '2026-07-30', student: 'Марк Ли', discipline: 'Барабаны', mark: 'no_show' },
    { teacher: TEACHERS[3].id, date: '2026-07-29', student: 'Санжар Тлеу', discipline: 'Гитара', mark: 'came' },
  ];
  for (const row of late) {
    const teacher = findTeacher(row.teacher);
    rows.push({
      id: ++accrualSeq,
      date: row.date,
      start: '18:00',
      teacher_id: teacher.id,
      branch_id: BRANCH_AF,
      student_name: row.student,
      discipline: row.discipline,
      duration_min: 55,
      mark: row.mark,
      rate: teacher.rate,
      amount: Math.round(teacher.rate * payShare(row.mark)),
      kind: 'lesson',
      period_id: null,
    });
  }

  /**
   * Корректировки августа: ошибочные отметки отменили, начисление сняли.
   * Записи журнала не удаляются — сервер добавляет компенсирующие,
   * поэтому в ведомости они видны отдельной колонкой, а не вычитанием
   * из «начислено».
   */
  const fixes: { teacher: string; date: string; student: string; amount: number }[] = [
    { teacher: TEACHERS[0].id, date: '2026-08-05', student: 'Амир Жанат', amount: -4500 },
    { teacher: TEACHERS[1].id, date: '2026-08-07', student: 'Сабина Нурлан', amount: -4200 },
    { teacher: TEACHERS[4].id, date: '2026-08-11', student: 'Айсулу Бек', amount: -4000 },
  ];
  for (const fix of fixes) {
    rows.push({
      id: ++accrualSeq,
      date: fix.date,
      start: '00:00',
      teacher_id: fix.teacher,
      branch_id: BRANCH_AF,
      student_name: fix.student,
      discipline: '',
      duration_min: 0,
      mark: null,
      rate: Math.abs(fix.amount),
      amount: fix.amount,
      kind: 'correction',
      period_id: null,
    });
  }

  return rows.sort((a, b) => a.date.localeCompare(b.date) || a.start.localeCompare(b.start));
}

export const ACCRUALS: MockAccrual[] = seedAccruals();

export const nextAccrualId = (): number => ++accrualSeq;

/* ---------- платежи (выручка) ---------- */

export interface MockPayment {
  id: number;
  date: string;
  branch_id: string;
  /** null — платёж не привязан к абонементу: направление неизвестно. */
  discipline: string | null;
  method: 'kaspi' | 'card' | 'cash' | 'transfer' | 'other';
  amount: number;
}

const METHOD_MIX: MockPayment['method'][] = ['kaspi', 'kaspi', 'kaspi', 'kaspi', 'card', 'cash', 'transfer'];

/**
 * Платежи за три месяца. Выручка считается по поступившим деньгам, а не
 * по проданным абонементам: продажа в долг — обычное дело, и выручка,
 * показывающая невыплаченное, отвечает не на тот вопрос.
 */
function seedPayments(): MockPayment[] {
  const rows: MockPayment[] = [];
  const random = seeded(4200);
  let id = 0;
  const months: [string, string][] = [
    ['2026-06-01', '2026-06-30'],
    ['2026-07-01', '2026-07-31'],
    ['2026-08-01', MOCK_TODAY],
  ];
  for (const [first, last] of months) {
    const days = daysBetween(first, last) + 1;
    for (let day = 0; day < days; day++) {
      const date = addDays(first, day);
      const count = Math.floor(random() * 3);
      for (let i = 0; i < count; i++) {
        const plan = PLANS[Math.floor(random() * PLANS.length)];
        rows.push({
          id: ++id,
          date,
          branch_id: random() < 0.62 ? BRANCH_AF : BRANCH_AB,
          discipline: plan.discipline,
          method: METHOD_MIX[Math.floor(random() * METHOD_MIX.length)],
          amount: plan.price,
        });
      }
    }
  }
  // Платёж без абонемента: разовое занятие оплатили на ресепшене. Он обязан
  // попасть в строку «Не распределено», иначе сумма разрезов не сойдётся
  // с итогом, а несходящийся финансовый отчёт хуже отсутствующего.
  rows.push({ id: ++id, date: '2026-08-06', branch_id: BRANCH_AF, discipline: null, method: 'cash', amount: 9000 });
  rows.push({ id: ++id, date: '2026-07-16', branch_id: BRANCH_AB, discipline: null, method: 'kaspi', amount: 12000 });
  return rows.sort((a, b) => a.date.localeCompare(b.date));
}

export const PAYMENTS: MockPayment[] = seedPayments();

/* ---------- загрузка кабинетов ---------- */

/**
 * Среднее число занятых минут в кабинете за рабочий день. Настоящий бэкенд
 * считает это из расписания; в моках полугода расписания нет, поэтому
 * фикстура задаёт темп кабинета, а отчёт умножает его на дни периода —
 * иначе загрузка за месяц равнялась бы загрузке за один день.
 */
export const ROOM_DAILY_BUSY_MIN: Record<string, number> = {
  [ROOMS[0].id]: 495, // Барабанная A — самая занятая, в неё и упирается школа
  [ROOMS[1].id]: 330,
  [ROOMS[2].id]: 275,
  [ROOMS[3].id]: 385,
  [ROOMS[4].id]: 220,
};
