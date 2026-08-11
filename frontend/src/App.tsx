import { useCallback, useEffect, useRef, useState } from 'react';
import { USE_MOCKS, api, type Branch, type ScheduleLesson, type ScheduleResponse } from './api';
import { useAsync } from './lib/useAsync';
import { useTheme } from './lib/useTheme';
import { initials, money, plural, todayIso } from './lib/format';
import { ScheduleScreen } from './features/schedule/ScheduleScreen';
import { AttendancePanel, type AppliedResult } from './features/attendance/AttendancePanel';
import { Toasts, type ToastMessage } from './components/Toasts';
import { ErrorState } from './components/States';
import { MARK_LABELS } from './api';

/** День из прототипа. В мок-режиме открываем его, иначе — сегодня. */
const DEMO_DATE = '2026-08-12';

export default function App() {
  const [, toggleTheme] = useTheme();
  const [branchId, setBranchId] = useState<string | null>(null);
  const [date, setDate] = useState(() => (USE_MOCKS ? DEMO_DATE : todayIso()));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const toastSeq = useRef(0);

  const branches = useAsync<Branch[]>(() => api.branches(), []);

  // Первый филиал выбирается сам: экран без филиала бесполезен
  useEffect(() => {
    if (!branchId && branches.data && branches.data.length > 0) setBranchId(branches.data[0].id);
  }, [branches.data, branchId]);

  const schedule = useAsync<ScheduleResponse>(
    () => api.schedule(branchId as string, date),
    [branchId, date],
    branchId !== null,
  );

  const pushToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    setToasts((prev) => [...prev, { ...toast, id: ++toastSeq.current }]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  /** После подтверждения показываем применённые последствия и перечитываем день. */
  const handleApplied = useCallback(
    (results: AppliedResult[]) => {
      for (const { participant, response } of results) {
        const rows = [
          { label: 'Отметка', value: MARK_LABELS[response.mark] },
          {
            label: 'Абонемент',
            value:
              response.applied.lessons_delta === 0
                ? 'без списания'
                : `${response.applied.lessons_delta} → ${response.applied.lessons_after}`,
          },
          { label: 'Преподавателю', value: response.applied.teacher_amount > 0 ? money(response.applied.teacher_amount) : '0 ₸' },
        ];
        if (response.applied.makeups_delta !== 0) {
          rows.push({ label: 'Отработки', value: `+${response.applied.makeups_delta}` });
        }
        for (const alert of response.alerts) rows.push({ label: 'Внимание', value: alert.message });
        pushToast({ title: `Отмечено · ${participant.name}`, rows });
      }
      schedule.reload();
      setSelectedId(null);
    },
    [pushToast, schedule],
  );

  const handleSelect = useCallback((lesson: ScheduleLesson) => {
    setSelectedId((current) => (current === lesson.id ? null : lesson.id));
  }, []);

  const summary = schedule.data?.summary;

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <b>RockCRM</b>
          <span>{USE_MOCKS ? 'моки' : 'этап 1'}</span>
        </div>
        <nav className="nav">
          <div className="nav-h">Смена</div>
          <button aria-current="true">
            <i className="k">1</i>
            <span>Расписание</span>
          </button>
          {/* Остальные экраны — вне границ этапа, кнопки оставлены как ориентир прототипа */}
          <button disabled title="Вне границ этапа 1">
            <i className="k">2</i>
            <span>Ученики</span>
          </button>
          <button disabled title="Вне границ этапа 1">
            <i className="k">3</i>
            <span>Заявки</span>
          </button>
          <div className="nav-h">Управление</div>
          <button disabled title="Вне границ этапа 1">
            <i className="k">4</i>
            <span>Деньги и ЗП</span>
          </button>
        </nav>
        <div className="rail-foot">
          <div className="avatar">{initials('Айгерим Дюсенова')}</div>
          <div className="who">
            <b>Айгерим Дюсенова</b>
            <span>Администратор</span>
          </div>
          <button className="theme-btn" onClick={toggleTheme} title="Сменить тему">
            ◐
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="seg-ctl">
            {(branches.data ?? []).map((branch) => (
              <button
                key={branch.id}
                aria-pressed={branch.id === branchId}
                onClick={() => {
                  setBranchId(branch.id);
                  setSelectedId(null);
                }}
              >
                {branch.name}
              </button>
            ))}
            {branches.loading && <button disabled>Филиалы загружаются…</button>}
          </div>

          {summary && (
            <span className="pill mute">
              <i className="dot" />
              Загрузка кабинетов {summary.room_utilization_pct}%
            </span>
          )}
          <div className="spacer" />
          {summary && summary.conflicts > 0 && (
            <span className="pill bad">
              <i className="dot" />
              {summary.conflicts} {plural(summary.conflicts, 'конфликт', 'конфликта', 'конфликтов')} кабинета
            </span>
          )}
          {USE_MOCKS && (
            <span className="pill acc" title="VITE_USE_MOCKS=true — данные из мок-сервера">
              <i className="dot" />
              Мок-режим
            </span>
          )}
        </header>

        {branches.error ? (
          <ErrorState error={branches.error} onRetry={branches.reload} title="Филиалы не загрузились" />
        ) : (
          <ScheduleScreen
            state={schedule}
            date={date}
            onDateChange={(next) => {
              setDate(next);
              setSelectedId(null);
            }}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        )}
      </div>

      <AttendancePanel lessonId={selectedId} onClose={() => setSelectedId(null)} onApplied={handleApplied} />
      <Toasts items={toasts} onDismiss={dismissToast} shifted={selectedId !== null} />
    </div>
  );
}
