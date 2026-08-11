import type { MeChild } from '../../api';
import { initials, wallTime } from '../../lib/format';
import { SubscriptionBlock, dayHeading, joinDot, prettyPhone, telHref } from './lib';
import type { RescheduleTarget } from './RescheduleDialog';
import type { RenewTarget } from './RenewDialog';
import type { SchoolContacts } from './lib';

/**
 * Главный экран кабинета.
 *
 * Порядок блоков — это порядок вопросов родителя: КОГДА вести → СКОЛЬКО
 * осталось → что делать, если кончается. Всё остальное (история, репертуар,
 * прошлые занятия) живёт в карточке ребёнка: на главном экране оно отодвинуло
 * бы вниз ответ на вопрос, ради которого кабинет и открыли.
 *
 * Детей столько, сколько их есть, и каждый со своим именем: в семье с двумя
 * детьми «ближайшее занятие» без имени бесполезно — вести-то надо кого-то
 * конкретного.
 */
export function HomeScreen({
  children,
  today,
  contacts,
  renewSent,
  rescheduleSent,
  onRenew,
  onReschedule,
  onOpenChild,
}: {
  children: MeChild[];
  today: string;
  contacts: SchoolContacts;
  renewSent: Set<string>;
  rescheduleSent: Set<string>;
  onRenew: (target: RenewTarget) => void;
  onReschedule: (lesson: RescheduleTarget) => void;
  onOpenChild: (studentId: string) => void;
}) {
  return (
    <div className="fam-list">
      {children.map((child) => (
        <section className="card fam-card" key={child.student_id}>
          <header className="fam-child-h">
            <span className="chip big" aria-hidden="true">
              {initials(child.full_name)}
            </span>
            <div>
              <h2>{child.name}</h2>
              <p>{joinDot(child.discipline, child.teacher?.name)}</p>
            </div>
          </header>

          <NextLesson
            child={child}
            today={today}
            contacts={contacts}
            sent={child.next_lesson ? rescheduleSent.has(child.next_lesson.lesson_id) : false}
            onReschedule={onReschedule}
          />

          <SubscriptionBlock
            subscription={child.subscription}
            renewSent={renewSent.has(child.student_id)}
            onRenew={() => onRenew(child)}
          />

          <button className="btn fam-btn" onClick={() => onOpenChild(child.student_id)}>
            Занятия, домашнее задание и репертуар
          </button>
        </section>
      ))}
    </div>
  );
}

/**
 * Ближайшее занятие — крупно: когда, где, к кому.
 *
 * Время набрано самым большим кеглем на экране, потому что это и есть ответ
 * на главный вопрос. Дата рядом словами («Завтра, 14 августа»), а не только
 * числом: в дверях никто не пересчитывает даты.
 */
function NextLesson({
  child,
  today,
  contacts,
  sent,
  onReschedule,
}: {
  child: MeChild;
  today: string;
  contacts: SchoolContacts;
  sent: boolean;
  onReschedule: (lesson: RescheduleTarget) => void;
}) {
  const next = child.next_lesson;

  if (!next) {
    return (
      <div className="fam-next fam-next-none">
        <span className="lbl">Ближайшее занятие</span>
        <p className="fam-next-empty">Занятий пока не назначено</p>
        <p className="hint">
          Расписание составляет администратор. Если занятия должны быть, спросите в школе
          {contacts.phone && <> — {prettyPhone(contacts.phone)}</>}.
        </p>
      </div>
    );
  }

  const date = next.starts_at.slice(0, 10);

  return (
    <div className="fam-next">
      <span className="lbl">Ближайшее занятие</span>
      <p className="fam-next-day">{dayHeading(date, today)}</p>
      <p className="fam-next-time num">{wallTime(next.starts_at)}</p>
      <p className="fam-next-where">{joinDot(child.teacher?.name ?? 'Преподаватель уточняется', next.room)}</p>
      {child.branch && <p className="fam-next-addr">{child.branch.address}</p>}

      {sent ? (
        <p className="fam-sent">Заявка на перенос отправлена. Администратор ответит сообщением.</p>
      ) : (
        /* Можно ли ещё переносить, решает сервер: порог отмены — правило школы,
           а `GET /me/children` этого флага не отдаёт (пробел 39). Кнопку
           показываем, отказ приходит текстом сервера прямо в заявке — с порогом
           и с тем, что делать вместо. Прятать кнопку по своей догадке значило бы
           запретить перенос там, где школа его разрешает. */
        <button
          className="btn fam-btn"
          onClick={() => onReschedule({ lesson_id: next.lesson_id, starts_at: next.starts_at, student_name: child.name })}
        >
          Попросить о переносе
        </button>
      )}

      {contacts.phone && (
        <a className="fam-quiet-link" href={telHref(contacts.phone)}>
          Не получается прийти — позвонить в школу
        </a>
      )}
    </div>
  );
}
