import { useState } from 'react';
import { ApiError, api, type MeScheduleLesson } from '../../api';
import { Dialog } from '../../components/Dialog';
import { longDate, wallTime } from '../../lib/format';

/**
 * Занятие для заявки. Главный экран знает о ближайшем занятии только
 * `next_lesson` — три поля, — и требовать от него полную форму значило бы
 * запрашивать расписание ради кнопки.
 */
export type RescheduleTarget = Pick<MeScheduleLesson, 'lesson_id' | 'starts_at' | 'student_name'>;

/**
 * Заявка на перенос — из карточки занятия.
 *
 * Это ЗАЯВКА, а не перенос: слот может быть занят, преподаватель может быть
 * занят, и решение принимает администратор. Поэтому здесь нет ни выбора
 * свободного времени, ни подтверждения «перенесено» — только причина
 * и удобное время, то есть ровно то, что администратору нужно для звонка.
 *
 * Причина обязательна: заявка без неё превращается в «перезвоните мне»,
 * и администратор всё равно звонит выяснять — то есть кабинет не сэкономил
 * ни одного звонка, ради чего он и делался.
 */
export function RescheduleDialog({
  lesson,
  onClose,
  onSent,
}: {
  lesson: RescheduleTarget;
  onClose: () => void;
  onSent: (message: string) => void;
}) {
  const [reason, setReason] = useState('');
  const [slots, setSlots] = useState<{ date: string; time: string }[]>([
    { date: '', time: '' },
    { date: '', time: '' },
  ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const setSlot = (index: number, patch: Partial<{ date: string; time: string }>) =>
    setSlots((prev) => prev.map((slot, i) => (i === index ? { ...slot, ...patch } : slot)));

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestReschedule(lesson.lesson_id, {
        reason: reason.trim(),
        preferred: slots
          .filter((slot) => slot.date && slot.time)
          .map((slot) => `${slot.date}T${slot.time}:00${offsetOf(lesson.starts_at)}`),
      });
      onSent(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Заявка не ушла. Попробуйте ещё раз.'));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Попросить о переносе"
      subtitle={`${lesson.student_name} · ${longDate(lesson.starts_at.slice(0, 10))}, ${wallTime(lesson.starts_at)}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn pri" onClick={send} disabled={busy || reason.trim().length === 0}>
            {busy ? 'Отправляем…' : 'Отправить заявку'}
          </button>
        </>
      }
    >
      <p className="fam-dlg-note">
        Расписание меняет администратор: время может быть занято другим учеником или преподавателем. Заявка уйдёт ему,
        ответ придёт сообщением.
      </p>

      <div className="field wide fam-field">
        <span>Что случилось</span>
        <textarea
          className="inp fam-area"
          rows={3}
          value={reason}
          placeholder="Например: уезжаем к бабушке на выходные"
          onChange={(event) => setReason(event.target.value)}
        />
      </div>

      <div className="fam-field">
        <span className="lbl">Когда удобно вместо этого</span>
        {/* Необязательно: родитель может и не знать своих планов, а пустое
            обязательное поле выгонит его звонить — то есть ровно туда,
            откуда кабинет должен был увести. */}
        <p className="hint" style={{ marginTop: 0 }}>
          Необязательно. Если время известно, администратору не придётся перезванивать.
        </p>
        {slots.map((slot, index) => (
          <div className="fam-slot" key={index}>
            <input
              className="inp"
              type="date"
              value={slot.date}
              aria-label={`Удобная дата ${index + 1}`}
              onChange={(event) => setSlot(index, { date: event.target.value })}
            />
            <input
              className="inp"
              type="time"
              value={slot.time}
              aria-label={`Удобное время ${index + 1}`}
              onChange={(event) => setSlot(index, { time: event.target.value })}
            />
          </div>
        ))}
      </div>

      {error && <p className="err-inline">{error.message}</p>}
    </Dialog>
  );
}

/**
 * Смещение берётся из строки самого занятия, а не пишется цифрой.
 * Захардкоженный «+06:00» однажды сдвинет все заявки на час — ровно в тот
 * день, когда школа откроет филиал в другом поясе.
 */
function offsetOf(isoString: string): string {
  const match = /([+-]\d{2}:\d{2})$/.exec(isoString);
  return match ? match[1] : 'Z';
}
