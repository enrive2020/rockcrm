import { useMemo, useState } from 'react';
import {
  ApiError,
  api,
  type DirectoryRoom,
  type DirectoryTeacher,
  type LeadCard,
  type ScheduleResponse,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, money, plural, wallMinutes, wallTime } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { Dialog } from '../../components/Dialog';
import type { ToastMessage } from '../../components/Toasts';

/** Пробный урок в школе — 45 минут за 2 000 ₸: значения из демо-данных. */
const DEFAULT_DURATION = 45;
const DEFAULT_PRICE = 2000;

/**
 * Назначение пробного урока.
 *
 * Занятость видна до отправки: под формой стоит список занятий выбранного
 * преподавателя и кабинета на этот день, а пересечение с выбранным временем
 * подсвечивается. Но последнее слово за сервером — только он видит все
 * филиалы и параллельные брони, поэтому его `409` не считается ошибкой
 * интерфейса: он превращается в вопрос «поставить всё равно?».
 *
 * Преподаватели и кабинеты берутся из справочников `GET /teachers` и
 * `GET /rooms` (задача #1). Пока их нет на живом бэкенде, запрос отвечает
 * 404, и списки собираются из расписания — выбранный день плюс сегодняшний.
 * Запасной путь оставлен именно запасным: он даёт не справочник, а срез дня,
 * и преподаватель без занятий в этот день в нём не появляется.
 */
export function TrialDialog({
  lead,
  onClose,
  onBooked,
}: {
  lead: LeadCard;
  onClose: () => void;
  onBooked: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const branchId = lead.branch?.id ?? null;
  const [date, setDate] = useState(TODAY);
  const [time, setTime] = useState('13:00');
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [price, setPrice] = useState(DEFAULT_PRICE);
  const [teacherId, setTeacherId] = useState<string | null>(null);
  const [roomId, setRoomId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  /** Сервер вернул 409 «занято» — предлагаем подтвердить овербукинг. */
  const [overbook, setOverbook] = useState(false);

  const day = useAsync<ScheduleResponse>(
    () => api.schedule(branchId as string, date),
    [branchId, date],
    branchId !== null,
  );
  const reference = useAsync<ScheduleResponse>(
    () => api.schedule(branchId as string, TODAY),
    [branchId],
    branchId !== null,
  );

  const teacherBook = useAsync<DirectoryTeacher[]>(() => api.teachers(branchId), [branchId], branchId !== null);
  const roomBook = useAsync<DirectoryRoom[]>(() => api.rooms(branchId), [branchId], branchId !== null);
  /** Справочники отсутствуют или пусты — работаем по расписанию, но говорим об этом. */
  const fromSchedule = (teacherBook.data ?? []).length === 0 || (roomBook.data ?? []).length === 0;

  const teachers = useMemo(() => {
    if (teacherBook.data && teacherBook.data.length > 0) {
      return teacherBook.data.map((t) => ({ id: t.id, name: t.name }));
    }
    const map = new Map<string, string>();
    for (const schedule of [day.data, reference.data]) {
      for (const track of schedule?.tracks ?? []) map.set(track.teacher.id, track.teacher.name);
    }
    return [...map.entries()].map(([id, name]) => ({ id, name }));
  }, [teacherBook.data, day.data, reference.data]);

  const rooms = useMemo(() => {
    if (roomBook.data && roomBook.data.length > 0) {
      return roomBook.data.map((r) => ({ id: r.id, name: r.name }));
    }
    const map = new Map<string, string>();
    for (const schedule of [day.data, reference.data]) {
      for (const track of schedule?.tracks ?? []) {
        for (const lesson of track.lessons) map.set(lesson.room.id, lesson.room.name);
      }
    }
    return [...map.entries()].map(([id, name]) => ({ id, name }));
  }, [roomBook.data, day.data, reference.data]);

  const teacher = teachers.find((t) => t.id === teacherId) ?? teachers[0];
  const room = rooms.find((r) => r.id === roomId) ?? rooms[0];

  const startMinutes = Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
  const endMinutes = startMinutes + duration;

  /** Занятия выбранных преподавателя и кабинета на этот день. */
  const busy = useMemo(() => {
    const lessons = (day.data?.tracks ?? []).flatMap((track) =>
      track.lessons.map((lesson) => ({ lesson, teacherId: track.teacher.id, teacherName: track.teacher.name })),
    );
    return lessons
      .filter((item) => item.teacherId === teacher?.id || item.lesson.room.id === room?.id)
      .map((item) => {
        const from = wallMinutes(item.lesson.starts_at);
        const to = wallMinutes(item.lesson.ends_at);
        return {
          id: item.lesson.id,
          title: item.lesson.title,
          room: item.lesson.room.name,
          teacher: item.teacherName,
          from,
          to,
          text: `${wallTime(item.lesson.starts_at)}–${wallTime(item.lesson.ends_at)}`,
          overlaps: startMinutes < to && from < endMinutes,
        };
      })
      .sort((a, b) => a.from - b.from);
  }, [day.data, teacher?.id, room?.id, startMinutes, endMinutes]);

  const clash = busy.find((slot) => slot.overlaps);

  /**
   * Смещение филиала берём из ISO-строк расписания, а не пишем цифрой:
   * захардкоженный пояс однажды сдвинет все пробные на час.
   */
  const offset = useMemo(() => {
    const anyLesson = (day.data ?? reference.data)?.tracks[0]?.lessons[0];
    const match = anyLesson ? /([+-]\d{2}:\d{2})$/.exec(anyLesson.starts_at) : null;
    if (match) return match[1];
    const minutes = -new Date().getTimezoneOffset();
    const sign = minutes >= 0 ? '+' : '-';
    const abs = Math.abs(minutes);
    return `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
  }, [day.data, reference.data]);

  const submit = async (ack: boolean) => {
    if (!teacher || !room) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.bookTrial(lead.id, {
        teacher_id: teacher.id,
        room_id: room.id,
        starts_at: `${date}T${time}:00${offset}`,
        duration_min: duration,
        price,
        overbook_ack: ack,
      });
      onBooked({
        title: `Пробный назначен · ${lead.student_name ?? lead.name}`,
        rows: [
          { label: 'Когда', value: `${dateGen(date)}, ${wallTime(result.starts_at)}` },
          { label: 'Преподаватель', value: result.teacher },
          { label: 'Кабинет', value: result.room },
          {
            label: 'Напоминание',
            value: result.notification_queued ? 'в очереди, уйдёт за сутки' : 'не поставлено',
          },
        ],
      });
    } catch (err) {
      const apiError =
        err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Пробный назначить не удалось. Повторите.');
      setError(apiError);
      // 409 — не отказ, а вопрос: слот занят, ставить ли поверх
      setOverbook(apiError.status === 409);
    } finally {
      setSaving(false);
    }
  };

  const ready = Boolean(teacher && room && branchId);

  return (
    <Dialog
      title={lead.trial ? 'Перенос пробного урока' : 'Назначение пробного урока'}
      subtitle={`${lead.student_name ?? lead.name}${lead.discipline ? ` · ${lead.discipline.name}` : ''}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Отмена
          </button>
          {overbook ? (
            <button className="btn pri" onClick={() => submit(true)} disabled={saving}>
              {saving ? 'Ставим…' : 'Поставить всё равно'}
            </button>
          ) : (
            <button className="btn pri" onClick={() => submit(false)} disabled={!ready || saving}>
              {saving ? 'Назначаем…' : 'Назначить пробный'}
            </button>
          )}
        </>
      }
    >
      {!branchId && (
        <div className="err-inline">
          У заявки не указан филиал — пробный ставить некуда. Укажите филиал в заявке и повторите.
        </div>
      )}

      {(day.loading || reference.loading || teacherBook.loading || roomBook.loading) && (
        <div className="skeleton" style={{ height: 120 }} />
      )}
      {day.error && <div className="err-inline">{day.error.message}</div>}

      {branchId && !day.loading && !day.error && !teacherBook.loading && !roomBook.loading && (
        <>
          <div className="blk fields">
            <label className="field">
              <span>Дата</span>
              <input
                className="inp num"
                type="date"
                value={date}
                onChange={(event) => {
                  if (!event.target.value) return;
                  setDate(event.target.value);
                  setOverbook(false);
                  setError(null);
                }}
              />
            </label>
            <label className="field">
              <span>Время начала</span>
              <input
                className="inp num"
                type="time"
                value={time}
                step={300}
                onChange={(event) => {
                  if (!event.target.value) return;
                  setTime(event.target.value);
                  setOverbook(false);
                  setError(null);
                }}
              />
            </label>
            <label className="field">
              <span>Преподаватель</span>
              <select
                className="inp"
                value={teacher?.id ?? ''}
                onChange={(event) => {
                  setTeacherId(event.target.value);
                  setOverbook(false);
                }}
              >
                {teachers.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Кабинет</span>
              <select
                className="inp"
                value={room?.id ?? ''}
                onChange={(event) => {
                  setRoomId(event.target.value);
                  setOverbook(false);
                }}
              >
                {rooms.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Длительность, мин</span>
              <select className="inp num" value={duration} onChange={(event) => setDuration(Number(event.target.value))}>
                {[45, 55, 85].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Стоимость пробного, ₸</span>
              <input
                className="inp num"
                type="number"
                min={0}
                step={500}
                value={price}
                onChange={(event) => setPrice(Math.max(0, Number(event.target.value) || 0))}
              />
            </label>
          </div>

          {fromSchedule && (
            <p className="hint" style={{ marginTop: -8 }}>
              Справочники преподавателей и кабинетов недоступны — списки собраны из расписания филиала. Преподавателя
              без занятий в эти дни в них не будет.
            </p>
          )}

          <div className="blk">
            <span className="lbl">Занятость на {dateGen(date)}</span>
            {busy.length === 0 ? (
              <div className="rule">
                У {teacher?.name ?? 'преподавателя'} и кабинета «{room?.name ?? '—'}» в этот день занятий нет — слот
                свободен.
              </div>
            ) : (
              <ul className="slots">
                {busy.map((slot) => (
                  <li key={slot.id} className={slot.overlaps ? 'busy' : ''}>
                    <span className="num">{slot.text}</span>
                    <b>{slot.title}</b>
                    <small>
                      {slot.teacher} · {slot.room}
                    </small>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="blk">
            <span className="lbl">Что получится</span>
            <div className={clash ? 'rule warn' : 'rule'}>
              <strong>
                {dateGen(date)}, {time}–{minutesToTime(endMinutes)}
              </strong>
              <ul>
                <li>
                  <span>Преподаватель</span>
                  <em>{teacher?.name ?? '—'}</em>
                </li>
                <li>
                  <span>Кабинет</span>
                  <em>{room?.name ?? '—'}</em>
                </li>
                <li>
                  <span>Длительность</span>
                  <em>
                    {duration} {plural(duration, 'минута', 'минуты', 'минут')}
                  </em>
                </li>
                <li>
                  <span>Стоимость</span>
                  <em>{price > 0 ? money(price) : 'бесплатно'}</em>
                </li>
              </ul>
              <p className="warn-note">
                {clash
                  ? `Слот пересекается: ${clash.text} — «${clash.title}» (${clash.room}). Сервер попросит подтвердить овербукинг.`
                  : 'Занятие попадёт в расписание, заявка перейдёт в «Пробный назначен», напоминание за сутки встанет в очередь.'}
              </p>
            </div>
          </div>

          {error && (
            <div className="err-inline" role="alert">
              <strong>{error.message}</strong>
              {overbook && (
                <p style={{ margin: '6px 0 0' }}>
                  Кнопка справа поставит занятие поверх занятого слота — это осознанный овербукинг, он останется виден
                  в расписании красной пометкой.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </Dialog>
  );
}

const minutesToTime = (minutes: number): string =>
  `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
