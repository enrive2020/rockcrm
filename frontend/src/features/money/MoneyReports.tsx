import {
  PAYMENT_METHOD_LABELS,
  api,
  type DebtsReport,
  type RevenueReport,
  type RevenueSlice,
  type RoomsReport,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, hoursFromMinutes, money, monthTitle, periodTitle, plural } from '../../lib/format';
import { CardSkeleton, ErrorState } from '../../components/States';

/**
 * Отчёт по выручке: филиалы, направления, месяцы и способы оплаты.
 *
 * Считается по поступившим деньгам, а не по проданным абонементам: продажа
 * в долг — обычное дело, и выручка, показывающая невыплаченное, отвечает
 * не на тот вопрос. Долги живут отдельным отчётом.
 */
export function RevenueReportCard({
  from,
  to,
  branchId,
}: {
  from: string;
  to: string;
  branchId: string | null;
}) {
  const report = useAsync<RevenueReport>(() => api.revenueReport(from, to, branchId), [from, to, branchId]);

  if (report.loading) return <CardSkeleton />;
  if (report.error) return <ErrorState error={report.error} onRetry={report.reload} title="Выручка не загрузилась" />;
  const data = report.data;
  if (!data) return null;

  return (
    <div className="col-stack">
      <div className="card">
        <span className="lbl">Выручка · {periodTitle(from, to)}</span>
        <div className="stat num">{money(data.total)}</div>
        <p className="trend">
          {data.payments} {plural(data.payments, 'платёж', 'платежа', 'платежей')} за период. Считается по поступившим
          деньгам: абонемент, оформленный в долг, сюда не попадает — он в отчёте по долгам.
        </p>
      </div>

      <div className="grid g2">
        <SliceCard title="По филиалам" rows={data.by_branch} total={data.total} />
        <SliceCard
          title="По направлениям"
          rows={data.by_discipline}
          total={data.total}
          note="Платёж, не привязанный к абонементу, попадает в «Не распределено» — иначе сумма разрезов не сошлась бы с итогом."
        />
      </div>

      <div className="grid g2">
        <div className="card">
          <span className="lbl">По месяцам</span>
          {data.by_month.length === 0 ? (
            <p className="trend">Платежей за период нет.</p>
          ) : (
            <div className="bars">
              {data.by_month.map((row) => {
                const max = Math.max(...data.by_month.map((m) => m.amount), 1);
                return (
                  <div className="bar-row" key={row.month}>
                    <span className="bar-name">{monthTitle(row.month)}</span>
                    <div className="bar-track">
                      <i style={{ width: `${Math.round((row.amount / max) * 100)}%` }} />
                    </div>
                    <span className="bar-val num">{money(row.amount)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="card">
          <span className="lbl">По способам оплаты</span>
          {data.by_method.length === 0 ? (
            <p className="trend">Платежей за период нет.</p>
          ) : (
            data.by_method.map((row) => (
              <div className="kv" key={row.method}>
                <span>
                  {PAYMENT_METHOD_LABELS[row.method] ?? row.method} · {row.payments}{' '}
                  {plural(row.payments, 'платёж', 'платежа', 'платежей')}
                </span>
                <b className="num">
                  {money(row.amount)} <span className="dim">{row.share_pct}%</span>
                </b>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function SliceCard({
  title,
  rows,
  total,
  note,
}: {
  title: string;
  rows: RevenueSlice[];
  total: number;
  note?: string;
}) {
  const sum = rows.reduce((acc, row) => acc + row.amount, 0);
  return (
    <div className="card">
      <span className="lbl">{title}</span>
      {rows.length === 0 ? (
        <p className="trend">Платежей за период нет.</p>
      ) : (
        <>
          {rows.map((row) => (
            <div className="kv" key={row.name}>
              <span className={row.id === null ? 'dim' : undefined}>{row.name}</span>
              <b className="num">
                {money(row.amount)} <span className="dim">{row.share_pct}%</span>
              </b>
            </div>
          ))}
          {/* Сумма разрезов обязана сходиться с итогом: несходящийся
              финансовый отчёт хуже отсутствующего */}
          <div className="kv">
            <span>Сумма разрезов</span>
            <b className={`num ${sum === total ? 'good' : 'bad-num'}`}>{money(sum)}</b>
          </div>
        </>
      )}
      {note && <p className="hint">{note}</p>}
    </div>
  );
}

/**
 * Загрузка кабинетов. Список отсортирован по загрузке — отчёт отвечает
 * на вопрос «где кончилось место», а не перечисляет кабинеты по алфавиту.
 */
export function RoomsReportCard({ from, to, branchId }: { from: string; to: string; branchId: string | null }) {
  const report = useAsync<RoomsReport>(() => api.roomsReport(from, to, branchId), [from, to, branchId]);

  if (report.loading) return <CardSkeleton />;
  if (report.error) return <ErrorState error={report.error} onRetry={report.reload} title="Загрузка не посчиталась" />;
  const data = report.data;
  if (!data) return null;

  return (
    <div className="col-stack">
      <div className="card">
        <span className="lbl">Загрузка кабинетов · {periodTitle(from, to)}</span>
        <div className="stat num">{data.utilization_pct}%</div>
        <p className="trend">
          Занято {hoursFromMinutes(data.busy_minutes)} из {hoursFromMinutes(data.capacity_minutes)}
        </p>
        <div className="bar">
          <i style={{ width: `${Math.min(100, data.utilization_pct)}%` }} />
        </div>
        {/* Процент загрузки без указания базы — число, которому нельзя верить,
            поэтому формула ёмкости стоит прямо под ним, словами сервера */}
        <p className="hint">{data.capacity_note}</p>
      </div>

      <div className="card">
        <span className="lbl">По филиалам</span>
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Филиал</th>
                <th className="r">Кабинетов</th>
                <th className="r">Рабочих дней</th>
                <th className="r">Занято</th>
                <th className="r">Ёмкость</th>
                <th className="r">Загрузка</th>
              </tr>
            </thead>
            <tbody>
              {data.branches.map((row) => (
                <tr key={row.branch_id}>
                  <td>{row.branch}</td>
                  <td className="r num">{row.rooms}</td>
                  <td className="r num dim">{row.open_days}</td>
                  <td className="r num">{hoursFromMinutes(row.busy_minutes)}</td>
                  <td className="r num dim">{hoursFromMinutes(row.capacity_minutes)}</td>
                  <td className="r num">{row.utilization_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <span className="lbl">По кабинетам · сверху то, где кончилось место</span>
        {data.rooms.length === 0 ? (
          <p className="trend">За период занятий не было.</p>
        ) : (
          <div className="bars">
            {data.rooms.map((room) => (
              <div className="bar-row" key={room.room_id}>
                <span className="bar-name">
                  {room.room}
                  <small className="dim"> · {room.branch}</small>
                </span>
                <div className="bar-track" title={`${hoursFromMinutes(room.busy_minutes)} из ${hoursFromMinutes(room.capacity_minutes)}`}>
                  <i style={{ width: `${Math.min(100, room.utilization_pct)}%` }} />
                </div>
                <span className="bar-val num">
                  {room.utilization_pct}% <span className="dim">· {room.lessons}</span>
                </span>
              </div>
            ))}
          </div>
        )}
        <p className="hint">Справа — загрузка и число занятий за период.</p>
      </div>
    </div>
  );
}

/**
 * Долги семей. Периода нет: долг — состояние на сейчас, а не за отрезок,
 * поэтому переключатель периода на этот отчёт не влияет и об этом сказано
 * прямо — иначе смена месяца без изменения цифр выглядит как поломка.
 */
export function DebtsReportCard() {
  const report = useAsync<DebtsReport>(() => api.debtsReport(50), []);

  if (report.loading) return <CardSkeleton />;
  if (report.error) return <ErrorState error={report.error} onRetry={report.reload} title="Долги не загрузились" />;
  const data = report.data;
  if (!data) return null;

  return (
    <div className="col-stack">
      <div className="card">
        <span className="lbl">Долги · состояние на сейчас</span>
        <div className="stat num bad-num">{money(data.total)}</div>
        <p className="trend">
          {data.families} {plural(data.families, 'семья', 'семьи', 'семей')} с непогашенным долгом. Период на этот
          отчёт не влияет: долг — состояние, а не сумма за отрезок.
        </p>
      </div>

      <div className="card">
        <span className="lbl">
          Кому звонить
          {/* Список ограничен сервером: молча показать 50 строк из 62 значило бы
              соврать про «все долги» — разницу называем прямо */}
          {data.items.length < data.families && ` · ${data.items.length} самых крупных из ${data.families}`}
        </span>
        {data.items.length === 0 ? (
          <p className="trend">Долгов нет — все абонементы оплачены.</p>
        ) : (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>Плательщик</th>
                  <th>Ученики</th>
                  <th className="r">Начислено</th>
                  <th className="r">Оплачено</th>
                  <th className="r">Долг</th>
                  <th className="r">Долг с</th>
                  <th className="r">Последний платёж</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <tr key={row.family_id}>
                    <td>
                      <b>{row.payer ?? 'Плательщик не указан'}</b>
                      {/* Телефон стоит прямо в строке: перезвонить можно,
                          не открывая карточку — тот же приём, что в поиске */}
                      {row.phone && <div className="phone num">{row.phone}</div>}
                    </td>
                    <td className="dim">{row.students.join(', ') || '—'}</td>
                    <td className="r num dim">{money(row.charged)}</td>
                    <td className="r num">{money(row.paid)}</td>
                    <td className="r num bad-num strong">{money(row.debt)}</td>
                    <td className="r num dim">{row.since_on ? dateGen(row.since_on) : '—'}</td>
                    <td className="r num dim">{row.last_paid_on ? dateGen(row.last_paid_on) : 'не платили'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="hint">{data.note}</p>
      </div>
    </div>
  );
}
