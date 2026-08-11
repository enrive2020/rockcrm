import type { ScheduleLesson, ScheduleResponse } from '../../api';
import type { AsyncState } from '../../lib/useAsync';
import { longDate, plural, shiftDate, shortDate } from '../../lib/format';
import { EmptyDay, ErrorState, ScheduleSkeleton } from '../../components/States';
import { Timeline } from './Timeline';

export function ScheduleScreen({
  state,
  date,
  onDateChange,
  selectedId,
  onSelect,
  onOpenStudent,
}: {
  state: AsyncState<ScheduleResponse>;
  date: string;
  onDateChange: (date: string) => void;
  selectedId: string | null;
  onSelect: (lesson: ScheduleLesson) => void;
  onOpenStudent: (studentId: string) => void;
}) {
  const schedule = state.data;
  const summary = schedule?.summary;

  return (
    <section className="screen">
      <div className="tl-head">
        <div>
          <h1 className="h1">Расписание</h1>
          <p className="sub">
            {longDate(date)}
            {summary
              ? ` · ${summary.lessons} ${plural(summary.lessons, 'занятие', 'занятия', 'занятий')}` +
                (summary.trials ? ` · ${summary.trials} ${plural(summary.trials, 'пробное', 'пробных', 'пробных')}` : '')
              : ''}
          </p>
        </div>
        <div className="spacer" />
        <div className="daynav">
          <button onClick={() => onDateChange(shiftDate(date, -1))} title="Предыдущий день">
            ‹
          </button>
          <button className="today" onClick={() => onDateChange(date)} title="Обновить день">
            {shortDate(date)}
          </button>
          <button onClick={() => onDateChange(shiftDate(date, 1))} title="Следующий день">
            ›
          </button>
          <input
            type="date"
            value={date}
            onChange={(event) => event.target.value && onDateChange(event.target.value)}
            aria-label="Дата расписания"
          />
        </div>
      </div>

      <div className="legend">
        <span>
          <i style={{ background: 'var(--panel)', border: '1px solid var(--line)' }} />
          Запланировано
        </span>
        <span>
          <i style={{ background: 'var(--ok-wash)', border: '1px solid var(--ok)' }} />
          Проведено
        </span>
        <span>
          <i style={{ background: 'var(--bad-wash)', border: '1px solid var(--bad)' }} />
          Прогул
        </span>
        <span>
          <i style={{ background: 'var(--accent-wash)', border: '1px solid var(--accent-bright)' }} />
          Пробный урок
        </span>
        <span>Полоса слева — преподаватель · клик по занятию открывает отметку, клик по имени — карточку ученика</span>
      </div>

      {state.loading && <ScheduleSkeleton />}
      {!state.loading && state.error && (
        <ErrorState error={state.error} onRetry={state.reload} title="Расписание не загрузилось" />
      )}
      {!state.loading && !state.error && schedule && schedule.tracks.length === 0 && <EmptyDay date={longDate(date)} />}
      {!state.loading && !state.error && schedule && schedule.tracks.length > 0 && (
        <Timeline schedule={schedule} selectedId={selectedId} onSelect={onSelect} onOpenStudent={onOpenStudent} />
      )}
    </section>
  );
}
