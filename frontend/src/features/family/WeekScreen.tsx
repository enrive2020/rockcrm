import { useMemo, useState } from 'react';
import { api, type MeSchedule, type MeScheduleLesson } from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, shiftDate, wallTime, weekBounds } from '../../lib/format';
import { Dialog } from '../../components/Dialog';
import { ErrorState, ListSkeleton } from '../../components/States';
import {
  FAMILY_MARK_LABELS,
  dayHeading,
  joinDot,
  markTone,
  prettyPhone,
  telHref,
  waHref,
  type SchoolContacts,
} from './lib';
import type { RescheduleTarget } from './RescheduleDialog';

/**
 * Расписание на неделю — всех детей сразу, а не по одному.
 *
 * Родителю нужно знать, КОГДА ВЕСТИ КОГО: листать детей по очереди значит
 * собирать неделю в голове, а собирают её именно в тот момент, когда времени
 * на это нет. Поэтому один список по дням, у каждой строки — имя ребёнка.
 *
 * Неделя двигается в обе стороны: прошедшее занятие с отметкой — это ответ
 * на вопрос «он вообще ходил?», и вырезать прошлое из расписания значило бы
 * отвечать на него только звонком.
 */
export function WeekScreen({
  today,
  contacts,
  rescheduleSent,
  onReschedule,
}: {
  today: string;
  contacts: SchoolContacts;
  rescheduleSent: Set<string>;
  onReschedule: (lesson: RescheduleTarget) => void;
}) {
  const [anchor, setAnchor] = useState(today);
  const week = useMemo(() => weekBounds(anchor), [anchor]);
  const schedule = useAsync<MeSchedule>(() => api.meSchedule(week.from, week.to), [week.from, week.to]);
  const [opened, setOpened] = useState<MeScheduleLesson | null>(null);

  const days = useMemo(() => groupByDay(schedule.data?.lessons ?? []), [schedule.data]);

  return (
    <div className="fam-list">
      <div className="fam-weeknav">
        <button className="btn" onClick={() => setAnchor(shiftDate(anchor, -7))} aria-label="Предыдущая неделя">
          ‹
        </button>
        <div className="fam-weeknav-title">
          <b>
            {dateGen(week.from)} — {dateGen(week.to)}
          </b>
          {week.from <= today && today <= week.to ? <small>Эта неделя</small> : (
            <button className="fam-quiet-link" onClick={() => setAnchor(today)}>
              Вернуться к текущей неделе
            </button>
          )}
        </div>
        <button className="btn" onClick={() => setAnchor(shiftDate(anchor, 7))} aria-label="Следующая неделя">
          ›
        </button>
      </div>

      {schedule.loading && <ListSkeleton rows={4} />}
      {schedule.error && <ErrorState error={schedule.error} onRetry={schedule.reload} title="Расписание не загрузилось" />}

      {!schedule.loading && !schedule.error && days.length === 0 && (
        <section className="card fam-card">
          <h2 className="fam-empty-h">На этой неделе занятий нет</h2>
          <p className="hint">
            Возможно, каникулы или занятия стоят на другой неделе. Пролистайте вперёд или спросите в школе
            {contacts.phone && <> — {prettyPhone(contacts.phone)}</>}.
          </p>
        </section>
      )}

      {days.map(([date, lessons]) => (
        <section className="fam-day" key={date}>
          <h2 className={date === today ? 'fam-day-h now' : 'fam-day-h'}>{dayHeading(date, today)}</h2>
          <div className="card fam-card slim">
            {lessons.map((lesson) => (
              <LessonRow
                key={lesson.lesson_id}
                lesson={lesson}
                sent={Boolean(lesson.reschedule_request) || rescheduleSent.has(lesson.lesson_id)}
                onOpen={() => setOpened(lesson)}
              />
            ))}
          </div>
        </section>
      ))}

      {opened && (
        <LessonDialog
          lesson={opened}
          today={today}
          contacts={contacts}
          /* Заявка приходит вместе с занятием, и это главный источник: память
             вкладки живёт до перезагрузки, а заявка — до ответа администратора.
             Локальный набор нужен лишь затем, чтобы состояние сменилось сразу
             после отправки, не дожидаясь перечитывания расписания. */
          sent={Boolean(opened.reschedule_request) || rescheduleSent.has(opened.lesson_id)}
          onClose={() => setOpened(null)}
          onReschedule={() => {
            const lesson = opened;
            setOpened(null);
            onReschedule(lesson);
          }}
        />
      )}
    </div>
  );
}

/**
 * Строка занятия.
 *
 * ОТМЕНЁННОЕ ЗАНЯТИЕ ОСТАЁТСЯ НА МЕСТЕ и помечается отменой. Убрать его
 * из списка значило бы оставить родителя гадать, почему в среду пусто:
 * пустота читается как «я что-то перепутал», и заканчивается это поездкой
 * к закрытой двери.
 */
function LessonRow({ lesson, onOpen, sent }: { lesson: MeScheduleLesson; onOpen: () => void; sent: boolean }) {
  const cancelled = lesson.status === 'cancelled';
  return (
    <button className={cancelled ? 'fam-lesson off' : 'fam-lesson'} onClick={onOpen}>
      <span className="fam-lesson-time num">{wallTime(lesson.starts_at)}</span>
      <span className="fam-lesson-main">
        <b>{lesson.student_name}</b>
        <small>{joinDot(lesson.teacher ?? 'Преподаватель уточняется', lesson.room)}</small>
        {lesson.branch && <small>{lesson.branch}</small>}
      </span>
      <span className="fam-lesson-mark">
        {cancelled ? (
          <span className="pill bad">Отменено</span>
        ) : lesson.attendance ? (
          <span className={`pill ${markTone(lesson.attendance)}`}>{FAMILY_MARK_LABELS[lesson.attendance]}</span>
        ) : sent ? (
          /* Отправленная заявка видна прямо в списке: иначе, чтобы вспомнить,
             просил ли он уже о переносе, родитель открывает занятия по одному. */
          <span className="pill acc">Просили перенос</span>
        ) : lesson.kind === 'trial' ? (
          <span className="pill acc">Пробное</span>
        ) : null}
      </span>
    </button>
  );
}

/**
 * Карточка занятия. Заявка на перенос идёт отсюда: у родителя перед глазами
 * то самое занятие, о котором он просит, — иначе заявка уходит «на что-то
 * в среду», и администратор выясняет, на что именно.
 */
function LessonDialog({
  lesson,
  today,
  contacts,
  sent,
  onClose,
  onReschedule,
}: {
  lesson: MeScheduleLesson;
  today: string;
  contacts: SchoolContacts;
  sent: boolean;
  onClose: () => void;
  onReschedule: () => void;
}) {
  const date = lesson.starts_at.slice(0, 10);
  return (
    <Dialog
      title={lesson.student_name}
      subtitle={`${dayHeading(date, today)}, ${wallTime(lesson.starts_at)} — ${wallTime(lesson.ends_at)}`}
      onClose={onClose}
      footer={
        <button className="btn" onClick={onClose}>
          Закрыть
        </button>
      }
    >
      <div className="blk">
        <div className="kv">
          <span>Преподаватель</span>
          <b>{lesson.teacher ?? '—'}</b>
        </div>
        <div className="kv">
          <span>Где</span>
          <b>{joinDot(lesson.branch, lesson.room) || '—'}</b>
        </div>
        <div className="kv">
          <span>Длительность</span>
          <b>{lesson.duration_min} мин</b>
        </div>
        {lesson.attendance && (
          <div className="kv">
            <span>Отметка</span>
            <b>{FAMILY_MARK_LABELS[lesson.attendance]}</b>
          </div>
        )}
      </div>

      {lesson.status === 'cancelled' ? (
        <p className="fam-dlg-note">
          Занятие отменено. О замене договоритесь со школой — по абонементу оно, как правило, не списывается.
        </p>
      ) : sent ? (
        <p className="fam-sent">Заявка на перенос уже отправлена. Администратор ответит сообщением.</p>
      ) : lesson.can_request_reschedule ? (
        <button className="btn pri fam-btn" onClick={onReschedule}>
          Попросить о переносе
        </button>
      ) : (
        /* Флаг считает сервер по правилам школы. Кнопку, которая гарантированно
           вернёт 422, не рисуем — вместо неё выход, который сработает. */
        <div className="fam-warn">
          <b>Перенести уже нельзя</b>
          <p>
            {/* Причина называется по календарю, а не по часам: «сегодня» кабинета
                в мок-режиме отличается от часов браузера, и сравнение моментов
                времени на клиенте соврало бы. Дату сервер и клиент видят одну. */}
            {date < today
              ? 'Занятие уже прошло.'
              : date === today
                ? 'Занятие сегодня — заявку школа уже не примет.'
                : 'До занятия осталось меньше времени, чем требуют правила школы.'}{' '}
            Если случилось непредвиденное — позвоните, администратор решит на месте.
          </p>
          {contacts.phone && (
            <div className="fam-contact-row">
              <a className="btn fam-btn" href={telHref(contacts.phone)}>
                {prettyPhone(contacts.phone)}
              </a>
              {contacts.whatsapp && (
                <a className="btn fam-btn" href={waHref(contacts.whatsapp)} target="_blank" rel="noreferrer">
                  WhatsApp
                </a>
              )}
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** Занятия по дням, дни по возрастанию. Пустые дни не рисуются: неделя
 *  из семи заголовков ради трёх занятий — это шум, а не расписание. */
function groupByDay(lessons: MeScheduleLesson[]): [string, MeScheduleLesson[]][] {
  const days = new Map<string, MeScheduleLesson[]>();
  for (const lesson of lessons) {
    const date = lesson.starts_at.slice(0, 10);
    const list = days.get(date);
    if (list) list.push(lesson);
    else days.set(date, [lesson]);
  }
  return [...days.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}
