import { useCallback, useEffect, useRef, useState } from 'react';
import { USE_MOCKS, api, type Branch, type ScheduleLesson, type ScheduleResponse } from './api';
import { useAsync } from './lib/useAsync';
import { useTheme } from './lib/useTheme';
import { initials, money, plural } from './lib/format';
import { TODAY } from './lib/today';
import { ScheduleScreen } from './features/schedule/ScheduleScreen';
import { AttendancePanel, type AppliedResult } from './features/attendance/AttendancePanel';
import { StudentsScreen } from './features/students/StudentsScreen';
import { StudentCardScreen } from './features/students/StudentCardScreen';
import { Toasts, type ToastMessage } from './components/Toasts';
import { ErrorState } from './components/States';
import { MARK_LABELS } from './api';

/**
 * Экраны переключаются состоянием, а не роутером: их два с половиной,
 * ссылками на карточку ученика пока не делятся, а react-router добавил бы
 * зависимость ради одного `switch`. Когда появятся ссылки «отправь мне
 * карточку», роутер станет оправдан.
 */
type View =
  | { screen: 'schedule' }
  | { screen: 'students' }
  /** Откуда пришли — туда и вернёт «Назад»: в расписание или в поиск. */
  | { screen: 'student'; id: string; from: 'schedule' | 'students' };

export default function App() {
  const [, toggleTheme] = useTheme();
  const [branchId, setBranchId] = useState<string | null>(null);
  const [date, setDate] = useState(TODAY);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<View>({ screen: 'schedule' });
  // Запрос живёт в App: вернувшись из карточки, администратор должен увидеть
  // тот же список, а не пустой экран поиска
  const [query, setQuery] = useState('');
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
    branchId !== null && view.screen === 'schedule',
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

  /** Открытие карточки закрывает панель отметки: два контекста сразу — путаница. */
  const openStudent = useCallback(
    (id: string, from: 'schedule' | 'students') => {
      setSelectedId(null);
      setView({ screen: 'student', id, from });
    },
    [],
  );

  const summary = schedule.data?.summary;
  const onSchedule = view.screen === 'schedule';

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <b>RockCRM</b>
          <span>{USE_MOCKS ? 'моки' : 'этап 2'}</span>
        </div>
        <nav className="nav">
          <div className="nav-h">Смена</div>
          <button aria-current={onSchedule} onClick={() => setView({ screen: 'schedule' })}>
            <i className="k">1</i>
            <span>Расписание</span>
          </button>
          <button aria-current={view.screen !== 'schedule'} onClick={() => setView({ screen: 'students' })}>
            <i className="k">2</i>
            <span>Ученики</span>
          </button>
          {/* Остальные экраны — вне границ этапа, кнопки оставлены как ориентир прототипа */}
          <button disabled title="Вне границ этапа 2">
            <i className="k">3</i>
            <span>Заявки</span>
          </button>
          <div className="nav-h">Управление</div>
          <button disabled title="Вне границ этапа 2">
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
          {/* Филиал относится к расписанию: поиск ученика идёт по всей школе,
              потому что родитель звонит один, а детей может водить в оба филиала */}
          {onSchedule && (
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
          )}

          {onSchedule && summary && (
            <span className="pill mute">
              <i className="dot" />
              Загрузка кабинетов {summary.room_utilization_pct}%
            </span>
          )}
          <div className="spacer" />
          {onSchedule && summary && summary.conflicts > 0 && (
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

        {branches.error && onSchedule ? (
          <ErrorState error={branches.error} onRetry={branches.reload} title="Филиалы не загрузились" />
        ) : view.screen === 'schedule' ? (
          <ScheduleScreen
            state={schedule}
            date={date}
            onDateChange={(next) => {
              setDate(next);
              setSelectedId(null);
            }}
            selectedId={selectedId}
            onSelect={handleSelect}
            onOpenStudent={(id) => openStudent(id, 'schedule')}
          />
        ) : view.screen === 'students' ? (
          <StudentsScreen query={query} onQueryChange={setQuery} onOpen={(id) => openStudent(id, 'students')} />
        ) : (
          <StudentCardScreen
            key={view.id}
            studentId={view.id}
            onBack={() => setView(view.from === 'schedule' ? { screen: 'schedule' } : { screen: 'students' })}
            onOpenStudent={(id) => openStudent(id, view.from)}
            onToast={pushToast}
          />
        )}
      </div>

      <AttendancePanel
        lessonId={selectedId}
        onClose={() => setSelectedId(null)}
        onApplied={handleApplied}
        onOpenStudent={(id) => openStudent(id, 'schedule')}
      />
      <Toasts items={toasts} onDismiss={dismissToast} shifted={selectedId !== null} />
    </div>
  );
}
