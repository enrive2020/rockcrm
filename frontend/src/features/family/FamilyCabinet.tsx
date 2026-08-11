import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, type Me, type MeChild } from '../../api';
import { useAsync } from '../../lib/useAsync';
import { useTheme } from '../../lib/useTheme';
import { initials } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { ErrorState, ListSkeleton } from '../../components/States';
import { Toasts, type ToastMessage } from '../../components/Toasts';
import { HomeScreen } from './HomeScreen';
import { WeekScreen } from './WeekScreen';
import { ChildScreen } from './ChildScreen';
import { RescheduleDialog, type RescheduleTarget } from './RescheduleDialog';
import { RenewDialog, type RenewTarget } from './RenewDialog';
import { prettyPhone, schoolContacts, telHref, waHref } from './lib';

/**
 * Кабинет родителя — ОТДЕЛЬНЫЙ МАКЕТ, а не урезанный административный.
 *
 * Административный интерфейс построен вокруг другой работы: плотные таблицы,
 * фильтры, множественные действия, боковое меню на пять экранов. Родителю
 * это мешает — у него один вопрос за раз: когда вести, сколько осталось,
 * что задали. Поэтому здесь три экрана, крупный шрифт и ни одной таблицы.
 *
 * МОБИЛЬНЫЙ ВИД ПЕРВИЧЕН. Кабинет открывают с телефона в дверях, одной рукой,
 * а не за столом: навигация внизу — под большой палец, контакты школы
 * в шапке — на каждом экране, ширина колонки ограничена, чтобы на планшете
 * и ноутбуке строка не растягивалась до нечитаемой.
 *
 * Роль сюда приходит из `GET /auth/me` и не вычисляется на клиенте.
 */
export function FamilyCabinet({ me, onSignOut }: { me: Me; onSignOut: (everywhere?: boolean) => Promise<void> }) {
  const contacts = useMemo(() => schoolContacts(me), [me]);
  const children = useAsync<MeChild[]>(() => api.meChildren(), []);

  const [tab, setTab] = useState<'home' | 'week' | 'child'>('home');
  const [childId, setChildId] = useState<string | null>(null);

  // Заявки помним в состоянии кабинета, а не экрана: родитель отправляет
  // заявку с главного, а перечитывает расписание — и вторая кнопка «Продлить»
  // на том же ребёнке читалась бы как «первая не сработала».
  const [renewSent, setRenewSent] = useState<Set<string>>(new Set());
  const [rescheduleSent, setRescheduleSent] = useState<Set<string>>(new Set());
  const [renewFor, setRenewFor] = useState<RenewTarget | null>(null);
  const [rescheduleFor, setRescheduleFor] = useState<RescheduleTarget | null>(null);

  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const toastSeq = useRef(0);
  const pushToast = useCallback((title: string, message: string) => {
    setToasts((prev) => [...prev, { id: ++toastSeq.current, title, rows: [{ label: '', value: message }] }]);
  }, []);
  const dismissToast = useCallback((id: number) => setToasts((prev) => prev.filter((t) => t.id !== id)), []);

  const list = children.data ?? [];
  // Ребёнок по умолчанию — первый: экран «карточка» без выбранного ребёнка
  // показывал бы пустоту там, где у семьи из одного ребёнка выбора нет вовсе.
  const activeChild = childId && list.some((c) => c.student_id === childId) ? childId : list[0]?.student_id ?? null;

  const openChild = useCallback((studentId: string) => {
    setChildId(studentId);
    setTab('child');
  }, []);

  return (
    <div className="fam">
      <CabinetHeader me={me} contacts={contacts} onSignOut={onSignOut} />

      <main className="fam-body">
        {children.loading && <ListSkeleton rows={3} />}
        {children.error && <ErrorState error={children.error} onRetry={children.reload} title="Данные не загрузились" />}

        {!children.loading && !children.error && list.length === 0 && (
          <section className="card fam-card">
            <h2 className="fam-empty-h">Учеников пока нет</h2>
            <p className="hint">
              К вашему номеру не привязан ни один ученик. Это чинится в школе за минуту — позвоните
              {contacts.phone && <> по номеру {prettyPhone(contacts.phone)}</>}.
            </p>
          </section>
        )}

        {list.length > 0 && tab === 'home' && (
          <HomeScreen
            children={list}
            today={TODAY}
            contacts={contacts}
            renewSent={renewSent}
            rescheduleSent={rescheduleSent}
            onRenew={setRenewFor}
            onReschedule={setRescheduleFor}
            onOpenChild={openChild}
          />
        )}

        {list.length > 0 && tab === 'week' && (
          <WeekScreen
            today={TODAY}
            contacts={contacts}
            rescheduleSent={rescheduleSent}
            onReschedule={setRescheduleFor}
          />
        )}

        {list.length > 0 && tab === 'child' && activeChild && (
          <ChildScreen
            children={list}
            studentId={activeChild}
            onSelect={setChildId}
            renewSent={renewSent}
            onRenew={setRenewFor}
          />
        )}
      </main>

      {/* Навигация внизу: телефон держат одной рукой, и низ экрана — это
          то, куда большой палец дотягивается без перехвата. На широком экране
          она уезжает наверх — там низ окна к рукам не ближе. */}
      <nav className="fam-nav" aria-label="Разделы кабинета">
        <button aria-current={tab === 'home'} onClick={() => setTab('home')}>
          Главное
        </button>
        <button aria-current={tab === 'week'} onClick={() => setTab('week')}>
          Расписание
        </button>
        <button aria-current={tab === 'child'} onClick={() => setTab('child')} disabled={list.length === 0}>
          {list.length === 1 ? list[0].name : 'Дети'}
        </button>
      </nav>

      {rescheduleFor && (
        <RescheduleDialog
          lesson={rescheduleFor}
          onClose={() => setRescheduleFor(null)}
          onSent={(message) => {
            setRescheduleSent((prev) => new Set(prev).add(rescheduleFor.lesson_id));
            setRescheduleFor(null);
            pushToast('Заявка на перенос отправлена', message);
            // Расписание перечитывать незачем — занятие не двинулось: заявку
            // ещё рассматривают, и показать новое время сейчас значило бы соврать.
          }}
        />
      )}

      {renewFor && (
        <RenewDialog
          child={renewFor}
          contacts={contacts}
          onClose={() => setRenewFor(null)}
          onSent={(message) => {
            setRenewSent((prev) => new Set(prev).add(renewFor.student_id));
            setRenewFor(null);
            pushToast('Заявка на продление отправлена', message);
          }}
        />
      )}

      <Toasts items={toasts} onDismiss={dismissToast} shifted={false} />
    </div>
  );
}

/**
 * Шапка: школа, контакты и выход.
 *
 * ТЕЛЕФОН И WHATSAPP СТОЯТ НА КАЖДОМ ЭКРАНЕ. Оплаты в кабинете нет намеренно,
 * и без этой строки родитель, увидевший «абонемент заканчивается», упирается
 * в тупик: понял, а сделать ничего не может. Строка контактов и есть выход
 * из тупика, поэтому она в шапке, а не в подвале раздела «о школе».
 */
function CabinetHeader({
  me,
  contacts,
  onSignOut,
}: {
  me: Me;
  contacts: ReturnType<typeof schoolContacts>;
  onSignOut: (everywhere?: boolean) => Promise<void>;
}) {
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

  return (
    <header className="fam-top">
      <div className="fam-top-row">
        <div className="fam-top-brand">
          <b>{me.tenant.name}</b>
          <small>Кабинет · {me.name}</small>
        </div>

        <div className="fam-account" ref={box}>
          <button className="avatar fam-avatar" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Учётная запись">
            {initials(me.name)}
          </button>
          {open && (
            <div className="user-menu fam-menu" role="menu">
              <div className="user-menu-h">
                <b>{me.name}</b>
                <small>{me.tenant.name}</small>
              </div>
              <button role="menuitem" onClick={toggleTheme}>
                Сменить тему
              </button>
              <button
                role="menuitem"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  void onSignOut(false);
                }}
              >
                Выйти
              </button>
            </div>
          )}
        </div>
      </div>

      {contacts.phone && (
        <div className="fam-contacts">
          <a className="fam-contact" href={telHref(contacts.phone)}>
            Позвонить в школу
          </a>
          {contacts.whatsapp && (
            <a className="fam-contact" href={waHref(contacts.whatsapp)} target="_blank" rel="noreferrer">
              WhatsApp
            </a>
          )}
        </div>
      )}
    </header>
  );
}
