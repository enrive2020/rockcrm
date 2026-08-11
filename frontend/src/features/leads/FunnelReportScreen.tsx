import { useState } from 'react';
import {
  LOST_REASON_LABELS,
  SOURCE_LABELS,
  STAGE_LABELS,
  api,
  type FunnelReport,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, plural } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { CardSkeleton, EmptyState, ErrorState } from '../../components/States';

/** Месяц назад — период по умолчанию: столько живёт средняя заявка. */
const monthAgo = (date: string): string => {
  const [y, m, d] = date.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCMonth(at.getUTCMonth() - 1);
  return at.toISOString().slice(0, 10);
};

/**
 * Отчёт по воронке — ради него этап и затевался: он отвечает на вопрос,
 * какой источник приводит учеников, а какой — только звонки.
 *
 * Конверсия считается сервером из истории стадий, а не из текущих колонок:
 * заявка, дошедшая до покупки, уже не лежит в «пробный проведён».
 */
export function FunnelReportScreen() {
  const [from, setFrom] = useState(monthAgo(TODAY));
  const [to, setTo] = useState(TODAY);
  const report = useAsync<FunnelReport>(() => api.funnel(from, to), [from, to]);
  const data = report.data;

  return (
    <div className="pad">
      <div className="fields" style={{ maxWidth: 420, marginBottom: 16 }}>
        <label className="field">
          <span>Период с</span>
          <input
            className="inp num"
            type="date"
            value={from}
            onChange={(event) => event.target.value && setFrom(event.target.value)}
          />
        </label>
        <label className="field">
          <span>по</span>
          <input
            className="inp num"
            type="date"
            value={to}
            onChange={(event) => event.target.value && setTo(event.target.value)}
          />
        </label>
      </div>

      {report.loading && <CardSkeleton />}
      {!report.loading && report.error && (
        <ErrorState error={report.error} onRetry={report.reload} title="Отчёт не загрузился" />
      )}

      {!report.loading && !report.error && data && (
        <>
          <div className="grid g-side">
            <div className="col-stack">
              <div className="card">
                <span className="lbl">
                  Конверсия по стадиям · {dateGen(data.period.from)} — {dateGen(data.period.to)}
                </span>
                {data.stages.every((row) => row.entered === 0) ? (
                  <p className="trend">За период не было ни одного перехода — расширьте период.</p>
                ) : (
                  <div className="funnel">
                    {data.stages.map((row) => {
                      const max = Math.max(...data.stages.map((s) => s.entered), 1);
                      return (
                        <div className="funnel-row" key={row.stage}>
                          <span className="funnel-name">{STAGE_LABELS[row.stage]}</span>
                          <div className="funnel-bar">
                            <i style={{ width: `${Math.round((row.entered / max) * 100)}%` }} />
                            <b className="num">{row.entered}</b>
                          </div>
                          <span className="funnel-conv num">
                            {row.stage === 'won' ? '—' : `${row.conversion_pct}%`}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                <p className="hint">
                  Слева — сколько заявок вошло в стадию за период, справа — доля тех, кто ушёл дальше. Считается
                  по истории переходов, поэтому выигранные заявки учтены в каждой пройденной стадии.
                </p>
              </div>

              <div className="card">
                <span className="lbl">Источники</span>
                {data.sources.length === 0 ? (
                  <p className="trend">За период заявок не было.</p>
                ) : (
                  <div className="tbl-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Источник</th>
                          <th className="r">Заявок</th>
                          <th className="r">Пробных</th>
                          <th className="r">Купили</th>
                          <th className="r">Конверсия</th>
                          <th className="r">Дней до оплаты</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.sources.map((row) => (
                          <tr key={row.source}>
                            <td>{SOURCE_LABELS[row.source]}</td>
                            <td className="r num">{row.leads}</td>
                            <td className="r num">{row.trials}</td>
                            <td className="r num">{row.won}</td>
                            <td className={`r num ${row.conversion_pct >= 40 ? 'good' : row.conversion_pct === 0 ? 'dim' : ''}`}>
                              {row.conversion_pct}%
                            </td>
                            <td className="r num dim">{row.avg_days_to_won || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            <div className="col-stack">
              <div className="card">
                <span className="lbl">Средний путь до оплаты</span>
                <div className="stat num">{data.avg_days_to_won}</div>
                <p className="trend">
                  {plural(Math.round(data.avg_days_to_won), 'день', 'дня', 'дней')} от заявки до покупки абонемента
                </p>
              </div>

              <div className="card">
                <span className="lbl">Причины отказов</span>
                {data.lost_reasons.length === 0 ? (
                  <p className="trend">Отказов за период нет.</p>
                ) : (
                  data.lost_reasons.map((row) => (
                    <div className="kv" key={row.reason}>
                      <span>{LOST_REASON_LABELS[row.reason]}</span>
                      <b className="num">{row.count}</b>
                    </div>
                  ))
                )}
                <p className="hint">
                  Причина обязательна при отказе именно поэтому: без неё непонятно, чинить цену, расписание или дозвон.
                </p>
              </div>
            </div>
          </div>

          {data.sources.length === 0 && data.stages.every((row) => row.entered === 0) && (
            <EmptyState label="Пусто" title="За период данных нет">
              Выберите более широкий период — демо-данные лежат на июль–август 2026.
            </EmptyState>
          )}
        </>
      )}
    </div>
  );
}
