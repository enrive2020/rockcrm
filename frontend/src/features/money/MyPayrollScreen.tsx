import { useState } from 'react';
import type { Me } from '../../api';
import { monthBounds, monthTitle, periodTitle, shiftMonth } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { TeacherDetail } from './PayrollSheet';

/**
 * «Моя зарплата» — экран преподавателя.
 *
 * Ведомости по школе у него нет: `GET /payroll` требует владельца, и рисовать
 * пункт, который вернёт 403, нельзя. Но §2 прямо разрешает ему видеть свою ЗП,
 * и `GET /payroll/teachers/{staff_id}` со своим `staff_id` отвечает ему тем же,
 * чем владельцу, — расшифровкой занятие за занятием.
 *
 * Таблица берётся ровно та же, что раскрывается в ведомости владельца: спор
 * о сумме разрешается сверкой двух экранов, и они обязаны показывать одно
 * и то же поле из одного и того же ответа.
 */
export function MyPayrollScreen({ me }: { me: Me }) {
  // Период по умолчанию — текущий месяц: зарплату считают помесячно
  const [period, setPeriod] = useState(() => monthBounds(TODAY));
  const { from, to } = period;

  const thisMonth = monthBounds(TODAY);
  const prevMonth = monthBounds(shiftMonth(TODAY, -1));

  // Кнопки в меню нет без `staff_id` (см. `accessFor`), но экран обязан
  // объясниться сам: пустая таблица читалась бы как «вам ничего не начислили».
  if (!me.staff_id) {
    return (
      <section className="screen">
        <div className="tl-head">
          <div>
            <h1 className="h1">Моя зарплата</h1>
            <p className="sub">
              Учётная запись не связана с карточкой сотрудника — начисления показать не из чего. Это чинится
              в школе, а не в интерфейсе.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="screen">
      <div className="tl-head">
        <div>
          <h1 className="h1">Моя зарплата</h1>
          <p className="sub">
            {periodTitle(from, to)} · начисления считаются от каждого проведённого занятия, а не от оклада
          </p>
        </div>
      </div>

      <div className="money-ctl">
        <div className="seg-ctl">
          <button aria-pressed={from === thisMonth.from && to === thisMonth.to} onClick={() => setPeriod(thisMonth)}>
            {monthTitle(TODAY)}
          </button>
          <button aria-pressed={from === prevMonth.from && to === prevMonth.to} onClick={() => setPeriod(prevMonth)}>
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
      </div>

      <div className="pad">
        <div className="card">
          <div className="card-head">
            <span className="lbl">Начисления · {periodTitle(from, to)}</span>
          </div>
          <TeacherDetail staffId={me.staff_id} from={from} to={to} branchId={null} />
        </div>
      </div>
    </section>
  );
}
