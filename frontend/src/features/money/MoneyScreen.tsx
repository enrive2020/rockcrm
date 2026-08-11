import { useState } from 'react';
import {
  api,
  type Branch,
  type MoneySummary,
  type PayrollPeriodRow,
  type PayrollSheet as PayrollSheetData,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import {
  dateGen,
  money,
  monthBounds,
  monthTitle,
  periodTitle,
  plural,
  shiftMonth,
} from '../../lib/format';
import { TODAY } from '../../lib/today';
import { CardSkeleton, ErrorState } from '../../components/States';
import type { ToastMessage } from '../../components/Toasts';
import { PayrollSheet } from './PayrollSheet';
import { DebtsReportCard, RevenueReportCard, RoomsReportCard } from './MoneyReports';

type Tab = 'payroll' | 'revenue' | 'rooms' | 'debts';

const TABS: { id: Tab; label: string }[] = [
  { id: 'payroll', label: 'Ведомость' },
  { id: 'revenue', label: 'Выручка' },
  { id: 'rooms', label: 'Кабинеты' },
  { id: 'debts', label: 'Долги' },
];

/**
 * Экран «Деньги и ЗП».
 *
 * Сверху — сводка: выручка со сравнением, загрузка кабинетов, ведомость
 * и блок «требует внимания». Ниже вкладками — ведомость и отчёты. Порядок
 * повторяет порядок вопросов владельца: сколько заработали → куда упёрлись →
 * сколько отдать → к кому идти.
 *
 * Филиал переключается здесь, а не в верхней панели: у отчётов есть режим
 * «все филиалы», которого нет ни у расписания, ни у воронки, — там занятие
 * всегда происходит в конкретном филиале.
 */
export function MoneyScreen({
  branches,
  onToast,
}: {
  branches: Branch[];
  onToast: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const [tab, setTab] = useState<Tab>('payroll');
  // Период по умолчанию — текущий месяц: ведомость закрывают помесячно
  const [period, setPeriod] = useState(() => monthBounds(TODAY));
  const [branchId, setBranchId] = useState<string | null>(null);

  const { from, to } = period;

  const summary = useAsync<MoneySummary>(() => api.moneySummary(from, to, branchId), [from, to, branchId]);
  const payroll = useAsync<PayrollSheetData>(() => api.payroll(from, to, branchId), [from, to, branchId]);
  const periods = useAsync<PayrollPeriodRow[]>(() => api.payrollPeriods(12), []);

  /** После закрытия периода перечитываем всё: ведомость сменила состояние. */
  const handleClosed = (message: string) => {
    onToast({ title: 'Период закрыт', rows: [{ label: 'Сервер', value: message }] });
    payroll.reload();
    periods.reload();
    summary.reload();
  };

  const thisMonth = monthBounds(TODAY);
  const prevMonth = monthBounds(shiftMonth(TODAY, -1));

  return (
    <section className="screen">
      <div className="tl-head">
        <div>
          <h1 className="h1">Деньги и зарплаты</h1>
          <p className="sub">
            {periodTitle(from, to)} ·{' '}
            {branchId ? branches.find((b) => b.id === branchId)?.name ?? 'филиал' : 'все филиалы'} · всё считается
            из отметок посещаемости
          </p>
        </div>
        <div className="spacer" />
        <div className="seg-ctl">
          {TABS.map((item) => (
            <button key={item.id} aria-pressed={tab === item.id} onClick={() => setTab(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="money-ctl">
        <div className="seg-ctl">
          <button
            aria-pressed={from === thisMonth.from && to === thisMonth.to}
            onClick={() => setPeriod(thisMonth)}
          >
            {monthTitle(TODAY)}
          </button>
          <button
            aria-pressed={from === prevMonth.from && to === prevMonth.to}
            onClick={() => setPeriod(prevMonth)}
          >
            {monthTitle(prevMonth.from)}
          </button>
        </div>
        <label className="filter">
          с
          <input
            className="inp slim num"
            type="date"
            value={from}
            onChange={(event) => event.target.value && setPeriod((p) => ({ ...p, from: event.target.value }))}
          />
        </label>
        <label className="filter">
          по
          <input
            className="inp slim num"
            type="date"
            value={to}
            onChange={(event) => event.target.value && setPeriod((p) => ({ ...p, to: event.target.value }))}
          />
        </label>
        <span className="dim" title="Период включает и первый, и последний день">
          включительно
        </span>
        <div className="spacer" />
        <div className="seg-ctl">
          <button aria-pressed={branchId === null} onClick={() => setBranchId(null)}>
            Все филиалы
          </button>
          {branches.map((branch) => (
            <button key={branch.id} aria-pressed={branchId === branch.id} onClick={() => setBranchId(branch.id)}>
              {branch.name}
            </button>
          ))}
        </div>
      </div>

      <div className="pad">
        {summary.loading && <CardSkeleton />}
        {!summary.loading && summary.error && (
          <ErrorState error={summary.error} onRetry={summary.reload} title="Сводка не загрузилась" />
        )}
        {!summary.loading && !summary.error && summary.data && (
          <SummaryRow data={summary.data} from={from} to={to} />
        )}

        <div className="grid g-side" style={{ marginTop: 14 }}>
          <div className="col-stack">
            {tab === 'payroll' &&
              (payroll.loading ? (
                <CardSkeleton />
              ) : payroll.error ? (
                <ErrorState error={payroll.error} onRetry={payroll.reload} title="Ведомость не загрузилась" />
              ) : payroll.data ? (
                <PayrollSheet
                  sheet={payroll.data}
                  from={from}
                  to={to}
                  branchId={branchId}
                  onClosed={handleClosed}
                />
              ) : null)}
            {tab === 'revenue' && <RevenueReportCard from={from} to={to} branchId={branchId} />}
            {tab === 'rooms' && <RoomsReportCard from={from} to={to} branchId={branchId} />}
            {tab === 'debts' && <DebtsReportCard />}
          </div>

          <div className="col-stack">
            {summary.data && <AttentionCard data={summary.data} />}
            <PeriodsCard state={periods} />
          </div>
        </div>
      </div>
    </section>
  );
}

/** Четыре числа шапки. Отток — заглушка: см. комментарий в `ChurnStub`. */
function SummaryRow({ data, from, to }: { data: MoneySummary; from: string; to: string }) {
  const revenue = data.revenue;
  const rooms = data.rooms;
  const payroll = data.payroll;
  const share = revenue.amount > 0 ? Math.round((payroll.total / revenue.amount) * 100) : null;

  return (
    <div className="grid g4">
      <div className="card">
        <span className="lbl">Выручка · {periodTitle(from, to)}</span>
        <div className="stat num">{money(revenue.amount)}</div>
        <p className="trend">
          {/* change_pct === null означает «прошлого периода нет»: «+100%» от нуля
              читается как рост, хотя означает лишь появление первых данных */}
          {revenue.change_pct === null ? (
            <span className="dim">Сравнить не с чем — за прошлый период данных нет</span>
          ) : (
            <>
              <b className={revenue.change_pct >= 0 ? 'good' : 'bad-num'}>
                {revenue.change_pct >= 0 ? '↑' : '↓'} {Math.abs(revenue.change_pct)}%
              </b>{' '}
              к прошлому периоду · {money(revenue.previous)}
            </>
          )}
        </p>
        {revenue.previous_period && (
          <p className="hint">
            Сравнение с {dateGen(revenue.previous_period.from)} — {dateGen(revenue.previous_period.to)}
          </p>
        )}
      </div>

      <div className="card">
        <span className="lbl">Загрузка кабинетов</span>
        <div className="stat num">{rooms.utilization_pct}%</div>
        <p className="trend">
          {rooms.busiest ? (
            <>
              {rooms.busiest.room} — <b>{rooms.busiest.utilization_pct}%</b>, самый занятый
            </>
          ) : (
            <span className="dim">За период занятий не было</span>
          )}
        </p>
        <div className="bar">
          <i style={{ width: `${Math.min(100, rooms.utilization_pct)}%` }} />
        </div>
      </div>

      <div className="card">
        <span className="lbl">Ведомость</span>
        <div className="stat num">{money(payroll.total)}</div>
        <p className="trend">
          {payroll.lessons} {plural(payroll.lessons, 'занятие', 'занятия', 'занятий')} ·{' '}
          {payroll.closed ? 'период закрыт' : 'период открыт, суммы ещё изменятся'}
        </p>
        {share !== null && (
          <p className="hint">
            {share}% выручки периода. Начисление идёт от каждого проведённого занятия, а не от оклада.
          </p>
        )}
      </div>

      <ChurnStub />
    </div>
  );
}

/**
 * Место под отток занято, но числа нет.
 *
 * Бэкенд считает отток по абонементам, а не по ученикам, и завышает его
 * в разы (issue #25): ученик, у которого за полгода кончилось четыре
 * абонемента, считается ушедшим четырежды. Показать такое число хуже,
 * чем не показать ничего: на него начнут принимать решения. Убирать карточку
 * тоже нельзя — тогда о дырке забудут, и вернуть её будет некому.
 */
function ChurnStub() {
  return (
    <div className="card stub">
      <span className="lbl">Отток</span>
      <div className="stat num dim">—</div>
      <p className="trend">Число неверно, поэтому не выводится</p>
      <p className="hint">
        Отток считается по абонементам, а не по ученикам: закончившийся и продлённый абонемент учитывается как уход,
        и цифра завышена в разы. Появится здесь после исправления на стороне сервера (issue&nbsp;#25).
      </p>
    </div>
  );
}

/** Блок «требует внимания» — то, ради чего экран открывают с утра. */
function AttentionCard({ data }: { data: MoneySummary }) {
  const a = data.attention;
  return (
    <div className="card">
      <span className="lbl">Требует внимания</span>
      <div className="kv">
        <span>Долги по оплате</span>
        <b className={`num ${a.debt_families > 0 ? 'bad-num' : 'dim'}`}>
          {a.debt_families > 0 ? `${a.debt_families} · ${money(a.debt_amount)}` : 'нет'}
        </b>
      </div>
      <div className="kv">
        <span>Абонементы кончаются</span>
        <b className={`num ${a.subscriptions_running_low > 0 ? 'acc-num' : 'dim'}`}>
          {a.subscriptions_running_low > 0
            ? `${a.subscriptions_running_low} ${plural(a.subscriptions_running_low, 'ученик', 'ученика', 'учеников')}`
            : 'нет'}
        </b>
      </div>
      <div className="kv">
        <span>Открытые отработки</span>
        <b className="num">{a.makeups_open}</b>
      </div>
      <div className="kv">
        <span>Заморожено сейчас</span>
        <b className="num">{a.frozen_now}</b>
      </div>
      <p className="hint">
        Долг считается той же формулой, что в карточке семьи: начислено по абонементам минус поступившие платежи.
      </p>
    </div>
  );
}

/**
 * Закрытые периоды. Нужны затем же, зачем журнал абонемента: увидеть, что
 * месяц уже выплачен и кем, прежде чем спорить о сумме.
 */
function PeriodsCard({
  state,
}: {
  state: { data: PayrollPeriodRow[] | null; loading: boolean; error: unknown };
}) {
  return (
    <div className="card">
      <span className="lbl">Закрытые периоды</span>
      {state.loading && <p className="trend">Загружаем…</p>}
      {!state.loading && state.data && state.data.length === 0 && (
        <p className="trend">Ни один период ещё не закрыт.</p>
      )}
      {!state.loading &&
        state.data?.map((row) => (
          <div className="kv" key={row.id}>
            <span>
              {monthTitle(row.period.from)}
              <small className="dim">
                {' '}
                · {row.entries} {plural(row.entries, 'начисление', 'начисления', 'начислений')}
              </small>
              {row.closed_by && <small className="dim"> · {row.closed_by}</small>}
            </span>
            <b className="num">{money(row.total)}</b>
          </div>
        ))}
      <p className="hint">
        Открытого периода не существует: строка заводится сразу закрытой. Переоткрыть её нельзя — ошибка чинится
        корректировкой в следующем периоде.
      </p>
    </div>
  );
}
