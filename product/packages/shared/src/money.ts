/** Money is always integer kopecks (bigint in DB, number in TS; safe below 2^53). */
export type Kopecks = number;
export const rub = (rubles: number): Kopecks => Math.round(rubles * 100);
export function assertKopecks(v: number): asserts v is Kopecks {
  if (!Number.isSafeInteger(v)) throw new TypeError(`money must be integer kopecks, got ${v}`);
}
export const formatRub = (k: Kopecks): string =>
  new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB' }).format(k / 100);
