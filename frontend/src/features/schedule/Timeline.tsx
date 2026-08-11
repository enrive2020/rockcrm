import type { CSSProperties } from 'react';
import type { ScheduleLesson, ScheduleResponse } from '../../api';
import { clockMinutes, todayIso, wallMinutes, wallTime } from '../../lib/format';

/**
 * Расписание как многодорожечный секвенсор: одна дорожка — один преподаватель,
 * общая шкала времени сверху. Так конфликт кабинета и «дыры» в дне видны
 * без чтения, а не после сравнения списков.
 */
export function Timeline({
  schedule,
  selectedId,
  onSelect,
  onOpenStudent,
}: {
  schedule: ScheduleResponse;
  selectedId: string | null;
  onSelect: (lesson: ScheduleLesson) => void;
  onOpenStudent: (studentId: string) => void;
}) {
  const openMin = clockMinutes(schedule.branch.opens_at);
  const closeMin = clockMinutes(schedule.branch.closes_at);
  const hours = Math.max(1, Math.ceil((closeMin - openMin) / 60));

  const ticks = Array.from({ length: hours }, (_, i) => openMin + i * 60);

  // Линию «сейчас» рисуем только на сегодняшней дате — иначе она врёт
  const nowOffset = schedule.date === todayIso() ? nowFraction(openMin, closeMin) : null;

  const style = { '--hours': hours } as CSSProperties;

  return (
    <div className="tl-wrap">
      <div className="tl-scroll">
        <div className="tl-inner" style={style}>
          <div className="ruler">
            <div className="gut">
              <span className="lbl">Преподаватель</span>
            </div>
            {ticks.map((minutes) => (
              <div className="tick" key={minutes}>
                {formatTick(minutes)}
              </div>
            ))}
          </div>

          {schedule.tracks.map((track) => (
            <div className="track" key={track.teacher.id}>
              <div className="gut">
                <i className="chan" style={{ '--ch': track.teacher.color } as CSSProperties} />
                <div>
                  <b>{track.teacher.name}</b>
                  <small>{track.teacher.disciplines.join(', ')}</small>
                </div>
              </div>
              <div className="lane">
                {nowOffset !== null && (
                  <div className="now" style={{ left: `calc(${nowOffset} * var(--hw))` }} aria-hidden="true" />
                )}
                {track.lessons.map((lesson) => (
                  <LessonBlock
                    key={lesson.id}
                    lesson={lesson}
                    color={track.teacher.color}
                    openMin={openMin}
                    selected={lesson.id === selectedId}
                    onSelect={onSelect}
                    onOpenStudent={onOpenStudent}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Блок занятия. Внутри две цели клика, поэтому это не одна кнопка:
 * клик по блоку открывает отметку — самое частое действие на ресепшене,
 * клик по имени ведёт в карточку ученика (требование контракта этапа 2).
 * Кнопка внутри кнопки недопустима, поэтому область отметки — отдельная
 * кнопка на весь блок, а имя лежит поверх неё.
 */
function LessonBlock({
  lesson,
  color,
  openMin,
  selected,
  onSelect,
  onOpenStudent,
}: {
  lesson: ScheduleLesson;
  color: string;
  openMin: number;
  selected: boolean;
  onSelect: (lesson: ScheduleLesson) => void;
  onOpenStudent: (studentId: string) => void;
}) {
  const start = (wallMinutes(lesson.starts_at) - openMin) / 60;
  const duration = lesson.duration_min / 60;
  const conflict = lesson.conflicts.length > 0;

  const classes = ['les', toneClass(lesson)];
  if (conflict) classes.push('clash');
  if (selected) classes.push('sel');

  const style = {
    '--s': Math.max(0, start),
    '--d': duration,
    '--ch': color,
  } as CSSProperties;

  const title = `${lesson.kind === 'trial' ? 'Пробный · ' : ''}${lesson.title}`;

  return (
    <div
      className={classes.filter(Boolean).join(' ')}
      style={style}
      title={conflict ? lesson.conflicts.map((c) => c.message).join('\n') : undefined}
    >
      <button
        className="les-hit"
        aria-pressed={selected}
        onClick={() => onSelect(lesson)}
        aria-label={`Отметить посещаемость: ${title}`}
        title="Открыть отметку посещаемости"
      />
      {conflict && <span className="flag">{lesson.conflicts[0].kind === 'room' ? 'Кабинет' : 'Педагог'}</span>}
      {/* У группы student_id нет — открывать нечего, имя остаётся текстом */}
      {lesson.student_id ? (
        <button className="les-name" onClick={() => onOpenStudent(lesson.student_id as string)} title="Открыть карточку ученика">
          {title}
        </button>
      ) : (
        <b>{title}</b>
      )}
      <small>
        {wallTime(lesson.starts_at)}–{wallTime(lesson.ends_at)} · {lesson.room.name}
      </small>
    </div>
  );
}

/** Фон занятия кодирует отметку, а не статус: администратору важен исход урока. */
function toneClass(lesson: ScheduleLesson): string {
  switch (lesson.attendance_mark) {
    case 'came':
    case 'late':
      return 'done';
    case 'no_show':
      return 'missed';
    case 'cancelled_early':
    case 'cancelled_late':
    case 'cancelled_teacher':
      return 'off';
    default:
      return lesson.kind === 'trial' ? 'trial' : '';
  }
}

const formatTick = (minutes: number): string =>
  `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;

/** Позиция линии «сейчас» в часах от открытия; null — вне рабочего дня. */
function nowFraction(openMin: number, closeMin: number): number | null {
  const now = new Date();
  const minutes = now.getHours() * 60 + now.getMinutes();
  if (minutes < openMin || minutes > closeMin) return null;
  return (minutes - openMin) / 60;
}
