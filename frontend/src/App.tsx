import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ROLE_LABELS,
  USE_MOCKS,
  api,
  type Branch,
  type Me,
  type ScheduleLesson,
  type ScheduleResponse,
} from './api';
import { useAsync } from './lib/useAsync';
import { useSession } from './lib/session';
import { accessFor, allowedBranches } from './lib/access';
import { useTheme } from './lib/useTheme';
import { initials, money, plural, signedMoney } from './lib/format';
import { TODAY } from './lib/today';
import { LoginScreen } from './features/auth/LoginScreen';
import { ScheduleScreen } from './features/schedule/ScheduleScreen';
import { AttendancePanel, type AppliedResult, type RevokedResult } from './features/attendance/AttendancePanel';
import { StudentsScreen } from './features/students/StudentsScreen';
import { StudentCardScreen } from './features/students/StudentCardScreen';
import { LeadsScreen } from './features/leads/LeadsScreen';
import { MoneyScreen } from './features/money/MoneyScreen';
import { MyPayrollScreen } from './features/money/MyPayrollScreen';
import { Toasts, type ToastMessage } from './components/Toasts';
import { ErrorState } from './components/States';
import { MARK_LABELS } from './api';

/**
 * Экраны переключаются состоянием, а не роутером: их четыре, ссылками
 * на карточку ученика пока не делятся, а react-router добавил бы
 * зависимость ради одного `switch`. Когда появятся ссылки «отправь мне
 * карточку», роутер станет оправдан.
 */
type View =
  | { screen: 'schedule' }
  | { screen: 'students' }
  | { screen: 'leads' }
  | { screen: 'money' }
  | { screen: 'mypay' }
  /** Откуда пришли — туда и вернёт «Назад»: в расписание, в поиск или в воронку. */
  | { screen: 'student'; id: string; from: 'schedule' | 'students' | 'leads' };

/**
 * Корень приложения — это развилка «вошли или нет», и ничего больше.
 *
 * Рабочее пространство смонтируется только после ответа `GET /auth/me`:
 * иначе первый же экран ушёл бы в API без сессии, получил 401 и показал
 * ошибку загрузки вместо формы входа.
 */
export default function App() {
  const session = useSession();

  if (session.status === 'loading') return <BootSplash />;
  if (session.status === 'anon' || session.me === null) {
    return (
      <LoginScreen
        notice={session.notice}
        error={session.error}
        onRetry={session.retry}
        onSignedIn={session.signIn}
      />
    );
  }

  return (
    // key по пользователю: вход другой ролью обязан начинать с чистого
    // состояния, иначе на экране останутся филиал и открытая карточка
    // предыдущего — то есть ровно те данные, которые новой роли не положены.
    <Workspace key={session.me.user_id} me={session.me} onSignOut={session.signOut} />
  );
}

/** Пока не известно, кто вошёл, показывать нечего — но и мигать пустотой нельзя. */
function BootSplash() {
  return (
    <div className="login">
      <div className="login-card" aria-busy="true">
        <div className="login-brand">
          <b>RockCRM</b>
        </div>
        <p className="sub">Проверяем сессию…</p>
      </div>
    </div>
  );
}

function Workspace({ me, onSignOut }: { me: Me; onSignOut: (everywhere?: boolean) => Promise<void> }) {
  const access = useMemo(() => accessFor(me), [me]);
  const [branchId, setBranchId] = useState<string | null>(null);
  const [date, setDate] = useState(TODAY);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Стартовый экран зависит от роли: родителю расписание филиала закрыто
  // (сервер ответит 403), и открывать кабинет на недоступном экране нельзя.
  const [view, setView] = useState<View>(() => ({ screen: access.schedule ? 'schedule' : 'students' }));
  // Запрос живёт здесь: вернувшись из карточки, администратор должен увидеть
  // тот же список, а не пустой экран поиска
  const [query, setQuery] = useState('');
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const toastSeq = useRef(0);

  // Справочник филиалов запрашивается только теми, кому он вообще положен:
  // `GET /branches` требует роль сотрудника, и у родителя этот запрос
  // вернул бы 403 — то есть красную полосу на пустом месте.
  const branches = useAsync<Branch[]>(() => api.branches(), [], access.branchSwitch);

  /**
   * Пустой `branch_ids` означает «все филиалы», а не «ни одного»: школа
   * из одного филиала связь не заполняет вовсе. Ошибка в эту сторону
   * оставила бы владельца без единого филиала — и без единого экрана.
   */
  const visibleBranches = useMemo(
    () => allowedBranches(branches.data ?? [], me),
    [branches.data, me],
  );

  // Первый филиал выбирается сам: экран без филиала бесполезен
  useEffect(() => {
    if (!branchId && visibleBranches.length > 0) setBranchId(visibleBranches[0].id);
  }, [visibleBranches, branchId]);

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

  /**
   * Отмена отметки: показываем то, что вернул сервер, и перечитываем день.
   * Панель при этом остаётся открытой — после исправления ошибки почти всегда
   * ставят правильную отметку тут же, и закрывать её значило бы заставить
   * администратора искать занятие в расписании заново.
   */
  const handleRevoked = useCallback(
    ({ participant, response }: RevokedResult) => {
      const reverted = response.reverted;
      const rows = [
        { label: 'Была отметка', value: MARK_LABELS[response.mark] },
        {
          label: 'Абонемент',
          value:
            reverted.lessons_delta === 0
              ? 'списания не было'
              : `+${reverted.lessons_delta}${reverted.lessons_after === null ? '' : ` → ${reverted.lessons_after}`}`,
        },
        { label: 'Преподавателю', value: signedMoney(reverted.teacher_amount) },
      ];
      if (reverted.makeups_delta !== 0) {
        rows.push({ label: 'Отработки', value: String(reverted.makeups_delta) });
      }
      rows.push({
        label: 'Занятие',
        value: response.lesson_status === 'planned' ? 'снова запланировано' : 'остаётся проведённым',
      });
      pushToast({ title: `Отметка отменена · ${participant.name}`, rows });
      schedule.reload();
    },
    [pushToast, schedule],
  );

  const handleSelect = useCallback((lesson: ScheduleLesson) => {
    setSelectedId((current) => (current === lesson.id ? null : lesson.id));
  }, []);

  /** Открытие карточки закрывает панель отметки: два контекста сразу — путаница. */
  const openStudent = useCallback((id: string, from: 'schedule' | 'students' | 'leads') => {
    setSelectedId(null);
    setView({ screen: 'student', id, from });
  }, []);

  const summary = schedule.data?.summary;
  const onSchedule = view.screen === 'schedule';
  const onLeads = view.screen === 'leads';
  // Филиал относится к расписанию и к воронке: заявка приходит в конкретный
  // филиал. Поиск ученика идёт по всей школе — родитель один, а детей
  // он может водить в оба.
  const withBranches = access.branchSwitch && (onSchedule || onLeads);

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <b>RockCRM</b>
          <span>{USE_MOCKS ? 'моки' : 'этап 5'}</span>
        </div>
        <nav className="nav">
          {/* Меню строится из ответа сервера. Пункт, который гарантированно
              вернёт 403, не рисуется вовсе: он выглядит поломкой продукта,
              а не границей прав. */}
          {(access.schedule || access.leads) && <div className="nav-h">Смена</div>}
          {access.schedule && (
            <button aria-current={onSchedule} onClick={() => setView({ screen: 'schedule' })}>
              <i className="k">1</i>
              <span>Расписание</span>
            </button>
          )}
          <button
            aria-current={view.screen === 'students' || view.screen === 'student'}
            onClick={() => setView({ screen: 'students' })}
          >
            <i className="k">2</i>
            <span>{access.ownStudentsOnly ? 'Мои ученики' : 'Ученики'}</span>
          </button>
          {access.leads && (
            <button aria-current={onLeads} onClick={() => setView({ screen: 'leads' })}>
              <i className="k">3</i>
              <span>Заявки</span>
            </button>
          )}
          {(access.money || access.myPayroll) && <div className="nav-h">Управление</div>}
          {access.money && (
            <button aria-current={view.screen === 'money'} onClick={() => setView({ screen: 'money' })}>
              <i className="k">4</i>
              <span>Деньги и ЗП</span>
            </button>
          )}
          {access.myPayroll && (
            <button aria-current={view.screen === 'mypay'} onClick={() => setView({ screen: 'mypay' })}>
              <i className="k">4</i>
              <span>Моя зарплата</span>
            </button>
          )}
        </nav>
        <UserMenu me={me} onSignOut={onSignOut} />
      </aside>

      <div className="main">
        <header className="topbar">
          {withBranches && (
            <div className="seg-ctl">
              {visibleBranches.map((branch) => (
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
          <span className="pill mute" title="Школа, в которой открыт кабинет">
            {me.tenant.name}
          </span>
          {USE_MOCKS && (
            <span className="pill acc" title="VITE_USE_MOCKS=true — данные из мок-сервера">
              <i className="dot" />
              Мок-режим
            </span>
          )}
        </header>

        {branches.error && withBranches ? (
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
          <StudentsScreen
            query={query}
            onQueryChange={setQuery}
            onOpen={(id) => openStudent(id, 'students')}
            ownOnly={access.ownStudentsOnly}
          />
        ) : view.screen === 'leads' ? (
          <LeadsScreen branchId={branchId} onOpenStudent={(id) => openStudent(id, 'leads')} onToast={pushToast} />
        ) : view.screen === 'money' ? (
          <MoneyScreen branches={visibleBranches} onToast={pushToast} access={access} />
        ) : view.screen === 'mypay' ? (
          <MyPayrollScreen me={me} />
        ) : (
          <StudentCardScreen
            key={view.id}
            studentId={view.id}
            onBack={() => setView({ screen: view.from })}
            onOpenStudent={(id) => openStudent(id, view.from)}
            onToast={pushToast}
            canSell={access.sell}
          />
        )}
      </div>

      <AttendancePanel
        lessonId={selectedId}
        onClose={() => setSelectedId(null)}
        onApplied={handleApplied}
        onRevoked={handleRevoked}
        onOpenStudent={(id) => openStudent(id, 'schedule')}
      />
      <Toasts items={toasts} onDismiss={dismissToast} shifted={selectedId !== null} />
    </div>
  );
}

/**
 * Кто вошёл и как выйти.
 *
 * «Выйти на всех устройствах» стоит рядом с обычным выходом, потому что
 * повод у него бытовой и срочный — потерянный телефон, уволенный
 * администратор, — и искать эту кнопку в настройках человек не станет.
 */
function UserMenu({ me, onSignOut }: { me: Me; onSignOut: (everywhere?: boolean) => Promise<void> }) {
  const [, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    const onClick = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open]);

  const leave = async (everywhere: boolean) => {
    setBusy(true);
    await onSignOut(everywhere);
  };

  return (
    <div className="rail-foot" ref={box}>
      <button className="who-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Учётная запись">
        <span className="avatar">{initials(me.name)}</span>
        <span className="who">
          <b>{me.name}</b>
          <span>{ROLE_LABELS[me.role]}</span>
        </span>
      </button>
      <button className="theme-btn" onClick={toggleTheme} title="Сменить тему">
        ◐
      </button>

      {open && (
        <div className="user-menu" role="menu">
          <div className="user-menu-h">
            <b>{me.name}</b>
            <small>
              {ROLE_LABELS[me.role]} · {me.tenant.name}
            </small>
          </div>
          <button role="menuitem" disabled={busy} onClick={() => leave(false)}>
            Выйти
          </button>
          <button role="menuitem" disabled={busy} onClick={() => leave(true)}>
            Выйти на всех устройствах
          </button>
        </div>
      )}
    </div>
  );
}
