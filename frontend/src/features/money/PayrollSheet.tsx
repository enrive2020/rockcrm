import { useState } from 'react';
import {
  MARK_LABELS,
  PAYROLL_ENTRY_KIND_LABELS,
  api,
  type PayrollDetail,
  type PayrollRow,
  type PayrollSheet as PayrollSheetData,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dayMonth, initials, money, periodTitle, plural, signedMoney, wallTime } from '../../lib/format';
import { CardSkeleton, ErrorState } from '../../components/States';
import { ClosePeriodDialog } from './ClosePeriodDialog';

/**
 * Ведомость по преподавателям.
 *
 * Строка раскрывается в расшифровку по занятиям: спор «откуда эта сумма»
 * разрешается открытием списка, а не пересчётом в Excel. Колонка
 * «из прошлых месяцев» стоит отдельно — это правки за уже закрытые периоды,
 * и бухгалтер должен видеть их, а не искать расхождение между итогом
 * ведомости и суммой занятий текущего месяца.
 */
export function PayrollSheet({
  sheet,
  from,
  to,
  branchId,
  onClosed,
}: {
  sheet: PayrollSheetData;
  from: string;
  to: string;
  branchId: string | null;
  onClosed: (message: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const totals = sheet.totals;
  const hasCarried = totals.carried_over_entries > 0;

  return (
    <div className="card">
      <div className="card-head">
        <span className="lbl">Ведомость · {periodTitle(from, to)}</span>
        <span className="spacer" />
        {sheet.closed ? (
          <span className="pill ok" title={`Закрыл${sheet.closed_by ? ` ${sheet.closed_by}` : ''}`}>
            <i className="dot" />
            Период закрыт
          </span>
        ) : (
          <>
            <span className="pill acc">
              <i className="dot" />
              Период открыт
            </span>
            <button className="btn slim" onClick={() => setClosing(true)}>
              Закрыть период
            </button>
          </>
        )}
      </div>

      {sheet.teachers.length === 0 ? (
        <p className="trend">За период начислений нет. Проверьте другой период или другой филиал.</p>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Преподаватель</th>
                <th className="r" title="Уникальные занятия: у группы из четырёх человек занятие одно">
                  Занятий
                </th>
                <th className="r" title="Строк начислений: у группы из четырёх человек начислений четыре">
                  Начислений
                </th>
                <th className="r">Ставка</th>
                <th className="r">Прогулы</th>
                <th className="r">Начислено</th>
                <th className="r">Правки</th>
                <th className="r" title="Начисления за уже закрытые месяцы, приехавшие правками">
                  Из прошлых
                </th>
                <th className="r">К выплате</th>
              </tr>
            </thead>
            <tbody>
              {sheet.teachers.map((row) => (
                <TeacherRows
                  key={row.teacher.id}
                  row={row}
                  from={from}
                  to={to}
                  branchId={branchId}
                  expanded={expanded === row.teacher.id}
                  onToggle={() => setExpanded((id) => (id === row.teacher.id ? null : row.teacher.id))}
                />
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>
                  Итого · {totals.teachers} {plural(totals.teachers, 'преподаватель', 'преподавателя', 'преподавателей')}
                </td>
                <td className="r num">{totals.lessons}</td>
                <td className="r num">{totals.entries}</td>
                <td className="r num dim">—</td>
                <td className="r num">{totals.no_shows}</td>
                <td className="r num">{money(totals.accrued)}</td>
                <td className={`r num ${totals.corrections !== 0 ? 'bad-num' : 'dim'}`}>
                  {totals.corrections === 0 ? '—' : signedMoney(totals.corrections)}
                </td>
                <td className={`r num ${hasCarried ? 'acc-num' : 'dim'}`}>
                  {hasCarried ? signedMoney(totals.carried_over) : '—'}
                </td>
                <td className="r num strong">{money(totals.total)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* Формулировка сервера дословно: он знает, открыт период или закрыт,
          и что именно вошло в сумму */}
      <p className="hint">{sheet.note}</p>

      {hasCarried && (
        <div className="rule" style={{ marginTop: 12 }}>
          <strong>
            Из прошлых месяцев: {signedMoney(totals.carried_over)} · {totals.carried_over_entries}{' '}
            {plural(totals.carried_over_entries, 'начисление', 'начисления', 'начислений')}
          </strong>
          <p style={{ margin: '6px 0 0' }}>
            Это отметки за уже закрытые месяцы, поставленные задним числом. Переоткрыть закрытый период нельзя,
            поэтому они приезжают правкой в текущую ведомость — в расшифровке такие строки помечены.
          </p>
        </div>
      )}

      {closing && (
        <ClosePeriodDialog
          from={from}
          to={to}
          sheet={sheet}
          onClose={() => setClosing(false)}
          onClosed={(message) => {
            setClosing(false);
            onClosed(message);
          }}
        />
      )}
    </div>
  );
}

/** Строка ведомости плюс раскрытая расшифровка под ней. */
function TeacherRows({
  row,
  from,
  to,
  branchId,
  expanded,
  onToggle,
}: {
  row: PayrollRow;
  from: string;
  to: string;
  branchId: string | null;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className={expanded ? 'row-open' : undefined}>
        <td>
          <button className="who-cell link-row" onClick={onToggle} aria-expanded={expanded}>
            <span className="chip" style={{ ['--ch' as string]: row.teacher.color ?? 'var(--ink-3)' }}>
              {initials(row.teacher.name)}
            </span>
            <span className="caret" aria-hidden="true">
              {expanded ? '▾' : '▸'}
            </span>
            {row.teacher.name}
          </button>
        </td>
        <td className="r num">{row.lessons}</td>
        <td className="r num dim">{row.entries}</td>
        <td className="r num">
          {money(row.rate)}
          {/* Показывать одну ставку, когда их несколько, значит вводить
              в заблуждение: контракт даёт для этого отдельный флаг */}
          {row.rate_varies && (
            <span className="varies" title="У преподавателя несколько ставок: 55 и 85 минут, индивидуальное и группа">
              ±
            </span>
          )}
        </td>
        <td className={`r num ${row.no_shows > 0 ? 'bad-num' : 'dim'}`}>{row.no_shows}</td>
        <td className="r num">{money(row.accrued)}</td>
        <td className={`r num ${row.corrections !== 0 ? 'bad-num' : 'dim'}`}>
          {row.corrections === 0 ? '—' : signedMoney(row.corrections)}
        </td>
        <td className={`r num ${row.carried_over !== 0 ? 'acc-num' : 'dim'}`}>
          {row.carried_over === 0 ? '—' : signedMoney(row.carried_over)}
        </td>
        <td className="r num strong">{money(row.total)}</td>
      </tr>
      {expanded && (
        <tr className="row-detail">
          <td colSpan={9}>
            <TeacherDetail staffId={row.teacher.id} from={from} to={to} branchId={branchId} />
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Расшифровка: за что именно начислено, занятие за занятием. `calc` —
 * снимок расчёта, сохранённый при отметке: через полгода объяснить
 * преподавателю сумму больше нечем, ставки к тому времени поменяются.
 *
 * Экспортируется, потому что тем же запросом и в том же виде преподаватель
 * смотрит собственную ведомость (`MyPayrollScreen`). Вторая таблица «почти
 * такая же» разошлась бы с этой при первой правке — и спорить о зарплате
 * начали бы с того, чей экран прав.
 */
export function TeacherDetail({
  staffId,
  from,
  to,
  branchId,
}: {
  staffId: string;
  from: string;
  to: string;
  branchId: string | null;
}) {
  const detail = useAsync<PayrollDetail>(
    () => api.payrollTeacher(staffId, from, to, branchId),
    [staffId, from, to, branchId],
  );

  if (detail.loading) return <CardSkeleton />;
  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.reload} title="Расшифровка не загрузилась" />;
  const data = detail.data;
  if (!data) return null;

  if (data.entries.length === 0) {
    return <p className="trend">Начислений за период нет.</p>;
  }

  return (
    <div className="detail">
      <div className="tbl-wrap">
        <table className="inner">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Ученик</th>
              <th>Направление</th>
              <th>Филиал</th>
              <th>Отметка</th>
              <th className="r">Мин</th>
              <th className="r">Расчёт</th>
              <th className="r">Сумма</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.map((entry) => (
              <tr key={entry.id} className={entry.carried_over ? 'carried' : undefined}>
                <td className="num">
                  {dayMonth(entry.date)}
                  {entry.starts_at && <span className="dim"> {wallTime(entry.starts_at)}</span>}
                  {entry.carried_over && (
                    <span className="tag carried-tag" title="Начисление за уже закрытый месяц — приехало правкой">
                      прошлый месяц
                    </span>
                  )}
                </td>
                <td>{entry.student ?? '—'}</td>
                <td className="dim">{entry.discipline ?? '—'}</td>
                <td className="dim">{entry.branch ?? '—'}</td>
                <td>
                  {entry.mark ? MARK_LABELS[entry.mark] : PAYROLL_ENTRY_KIND_LABELS[entry.kind]}
                </td>
                <td className="r num dim">{entry.duration_min ?? '—'}</td>
                <td className="r num dim">{describeCalc(entry.calc)}</td>
                <td className={`r num ${entry.amount < 0 ? 'bad-num' : ''}`}>
                  {entry.amount === 0 ? '0 ₸' : signedMoney(entry.amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">
        Всего {data.entries.length} {plural(data.entries.length, 'строка', 'строки', 'строк')} на{' '}
        {money(data.totals.total)}. Колонка «расчёт» — снимок, сохранённый при отметке: ставка и доля на момент
        занятия, а не сегодняшние.
      </p>
    </div>
  );
}

/**
 * Снимок расчёта одной строкой. Форма `calc` контрактом не закреплена
 * (задан только пример `{kind, mark, rate, share, amount}`), поэтому
 * читаем известные ключи и молчим об остальных, а не падаем на них.
 */
function describeCalc(calc: Record<string, unknown>): string {
  const rate = typeof calc.rate === 'number' ? calc.rate : null;
  const share = typeof calc.share === 'number' ? calc.share : null;
  if (rate === null) return '—';
  if (share === null || share === 1) return money(rate);
  return `${money(rate)} × ${Math.round(share * 100)}%`;
}
