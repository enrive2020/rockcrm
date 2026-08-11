/** Форматирование для интерфейса. Никакой бизнес-логики: числа приходят с сервера. */

/** 4500 → «4 500 ₸». Неразрывный пробел, чтобы сумма не рвалась по строкам. */
export const money = (value: number): string =>
  `${Math.abs(value).toLocaleString('ru-RU').replace(/[  ,]/g, ' ')} ₸`;

export const signedMoney = (value: number): string => (value > 0 ? `+${money(value)}` : money(value));

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

/** Инициалы для плашки: «Дмитрий Шарапов» → «ДШ». */
export const initials = (name: string): string =>
  name
    .replace(/[«»]/g, '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
