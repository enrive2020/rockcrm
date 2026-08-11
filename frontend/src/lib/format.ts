/** Форматирование для интерфейса. Никакой бизнес-логики: числа приходят с сервера. */

/** 4500 → «4 500 ₸». Неразрывный пробел, чтобы сумма не рвалась по строкам. */
export const money = (value: number): string =>
  `${Math.abs(value).toLocaleString('ru-RU').replace(/[  ,]/g, ' ')} ₸`;

/**
 * Знак обязателен: «4 500 ₸» и «−4 500 ₸» — это начисление и его снятие,
 * и путать их нельзя. `money` печатает модуль, поэтому минус ставится здесь.
 */
export const signedMoney = (value: number): string =>
  value > 0 ? `+${money(value)}` : value < 0 ? `−${money(value)}` : money(0);

export const plural = (n: number, one: string, few: string, many: string): string => {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
};

export const lessonsWord = (n: number): string => `${n} ${plural(n, 'занятие', 'занятия', 'занятий')}`;

/**
 * Время из ISO-строки берётся регуляркой, а не через Date.
 * Строка уже несёт смещение филиала; пропускать её через Date значило бы
 * пересчитать в часовой пояс браузера и показать администратору в Алматы
 * расписание по времени его ноутбука.
 */
export const wallTime = (isoString: string): string => {
  const match = /T(\d{2}):(\d{2})/.exec(isoString);
  return match ? `${match[1]}:${match[2]}` : '--:--';
};

/** Минуты от полуночи по местному времени филиала. */
export const wallMinutes = (isoString: string): number => {
  const match = /T(\d{2}):(\d{2})/.exec(isoString);
  return match ? Number(match[1]) * 60 + Number(match[2]) : 0;
};

/** "10:00" → 600 */
export const clockMinutes = (hhmm: string): number => {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + (m || 0);
};

const MONTHS_GEN = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const WEEKDAYS = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const WEEKDAYS_SHORT = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** "2026-08-12" → Date в UTC-полночь: календарная дата не должна ползти от пояса. */
const parseDate = (date: string): Date => {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};

/** "2026-08-12" → «Среда, 12 августа» */
export const longDate = (date: string): string => {
  const d = parseDate(date);
  return `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS_GEN[d.getUTCMonth()]}`;
};

/** "2026-08-12" → «12 августа» — без дня недели, для сроков и дат оплаты. */
export const dateGen = (date: string): string => {
  const d = parseDate(date);
  return `${d.getUTCDate()} ${MONTHS_GEN[d.getUTCMonth()]}`;
};

/** "2026-08-07" → «7 авг» — для плотных таблиц и заметок. */
export const dayMonth = (date: string): string => {
  const d = parseDate(date);
  return `${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
};

/** Разница в календарных днях. Заморозка — полуинтервал, как daterange в базе. */
export const daysBetween = (from: string, to: string): number => {
  const a = parseDate(from).getTime();
  const b = parseDate(to).getTime();
  return Math.round((b - a) / 86400000);
};

export const daysWord = (n: number): string => `${n} ${plural(n, 'день', 'дня', 'дней')}`;

/** "2026-08-12" → «Ср 12 авг» */
export const shortDate = (date: string): string => {
  const d = parseDate(date);
  return `${WEEKDAYS_SHORT[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS_SHORT[d.getUTCMonth()]}`;
};

/** Сдвиг даты на дни, вход и выход — "YYYY-MM-DD". */
export const shiftDate = (date: string, days: number): string => {
  const d = parseDate(date);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

/** Сегодняшняя дата в локальном календаре пользователя. */
export const todayIso = (): string => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

const MONTHS_NOM = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

/**
 * Границы календарного месяца, в который попадает дата. Период отчётов —
 * пара `from`/`to` включительно с обеих сторон, поэтому `to` — последний
 * день месяца, а не первое число следующего.
 */
export const monthBounds = (date: string): { from: string; to: string } => {
  const [y, m] = date.split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const mm = String(m).padStart(2, '0');
  return { from: `${y}-${mm}-01`, to: `${y}-${mm}-${String(last).padStart(2, '0')}` };
};

/**
 * Границы недели, в которую попадает дата: понедельник — воскресенье
 * включительно. Неделя начинается с понедельника, потому что расписание
 * музыкальной школы живёт учебной неделей, а не календарём США.
 */
export const weekBounds = (date: string): { from: string; to: string } => {
  const d = parseDate(date);
  // getUTCDay(): 0 — воскресенье, поэтому оно и есть последний день недели
  const shift = (d.getUTCDay() + 6) % 7;
  const from = shiftDate(date, -shift);
  return { from, to: shiftDate(from, 6) };
};

/**
 * «Сегодня», «Завтра», «Вчера» — иначе календарной датой. Родитель читает
 * ближайшее занятие на ходу, и «Завтра, 11:00» он понимает без пересчёта,
 * а «13 августа» требует вспомнить, какое сегодня число.
 */
export const relativeDay = (date: string, today: string): string => {
  const diff = daysBetween(today, date);
  if (diff === 0) return 'Сегодня';
  if (diff === 1) return 'Завтра';
  if (diff === -1) return 'Вчера';
  return longDate(date);
};

/** Сдвиг на календарные месяцы: «прошлый месяц» в переключателе периода. */
export const shiftMonth = (date: string, months: number): string => {
  const [y, m, d] = date.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1 + months, 1));
  const last = new Date(Date.UTC(at.getUTCFullYear(), at.getUTCMonth() + 1, 0)).getUTCDate();
  return `${at.getUTCFullYear()}-${String(at.getUTCMonth() + 1).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
};

/** "2026-08" или "2026-08-12" → «Август 2026». */
export const monthTitle = (date: string): string => {
  const [y, m] = date.split('-').map(Number);
  return `${MONTHS_NOM[m - 1]} ${y}`;
};

/**
 * Период человеческой строкой. Целый месяц называем месяцем — «1 августа —
 * 31 августа» читается медленнее, чем «Август 2026», а это подпись экрана.
 */
export const periodTitle = (from: string, to: string): string => {
  const bounds = monthBounds(from);
  if (bounds.from === from && bounds.to === to) return monthTitle(from);
  return `${dateGen(from)} — ${dateGen(to)}`;
};

/** Минуты → «124 ч»: загрузка кабинетов измеряется часами, а не минутами. */
export const hoursFromMinutes = (minutes: number): string =>
  `${Math.round(minutes / 60).toLocaleString('ru-RU').replace(/[  ,]/g, ' ')} ч`;

/** Инициалы для плашки: «Дмитрий Шарапов» → «ДШ». */
export const initials = (name: string): string =>
  name
    .replace(/[«»]/g, '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
