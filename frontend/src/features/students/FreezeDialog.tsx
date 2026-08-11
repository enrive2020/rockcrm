import { useState } from 'react';
import { ApiError, api, type StudentSubscription } from '../../api';
import { dateGen, daysBetween, daysWord, lessonsWord } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { Dialog } from '../../components/Dialog';
import type { ToastMessage } from '../../components/Toasts';

const addDays = (date: string, days: number): string => {
  const [y, m, d] = date.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
};

/**
 * Заморозка на каникулы. Сдвигает срок действия на число замороженных дней;
 * занятия внутри интервала отменяются без списания.
 *
 * Показываем три вещи до подтверждения: сколько дней уйдёт, сколько
 * останется от годового лимита и сколько занятий будет отменено. Последнее —
 * оценка по темпу абонемента (занятий на день × дней заморозки): эндпоинта
 * предпросмотра заморозки контракт не даёт, а промолчать нельзя — отменённые
 * занятия при снятии заморозки не восстанавливаются. Точное число приходит
 * в ответе сервера и показывается в уведомлении.
 */
export function FreezeDialog({
  studentName,
  subscription,
  onClose,
  onFrozen,
}: {
  studentName: string;
  subscription: StudentSubscription;
  onClose: () => void;
  onFrozen: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const [from, setFrom] = useState(TODAY);
  const [to, setTo] = useState(addDays(TODAY, 7));
  const [reason, setReason] = useState('каникулы');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const days = daysBetween(from, to);
  const left = subscription.freeze_days_left;
  const inPast = from < TODAY;
  const overLimit = days > left;
  const badPeriod = days <= 0;
  const blocked = inPast || overLimit || badPeriod;

  const periodDays = Math.max(1, daysBetween(subscription.valid_from, subscription.valid_until));
  const cancelled = Math.min(
    subscription.lessons_balance,
    Math.round((subscription.lessons_total / periodDays) * Math.max(0, days)),
  );
  const validUntilAfter = addDays(subscription.valid_until, Math.max(0, days));

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await api.createHold(subscription.id, { from, to, reason: reason.trim() });
      onFrozen({
        title: `Абонемент заморожен · ${studentName}`,
        rows: [
          { label: 'Период', value: `${dateGen(result.valid_until_before)} → ${dateGen(result.valid_until_after)}` },
          { label: 'Дней', value: String(result.days) },
          { label: 'Отменено занятий', value: String(result.lessons_cancelled) },
          { label: 'Лимит', value: `осталось ${daysWord(result.freeze_days_left)}` },
        ],
      });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(0, 'unexpected', 'Заморозить абонемент не удалось. Повторите попытку.'),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Заморозка абонемента"
      subtitle={`${studentName} · ${subscription.plan_name}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Отмена
          </button>
          <button className="btn pri" onClick={submit} disabled={saving || blocked}>
            {saving ? 'Замораживаем…' : `Заморозить на ${daysWord(Math.max(0, days))}`}
          </button>
        </>
      }
    >
      <div className="blk fields">
        <label className="field">
          <span>С какого дня</span>
          <input
            className="inp num"
            type="date"
            value={from}
            onChange={(event) => event.target.value && setFrom(event.target.value)}
          />
        </label>
        <label className="field">
          <span>По какой (не включая)</span>
          <input
            className="inp num"
            type="date"
            value={to}
            onChange={(event) => event.target.value && setTo(event.target.value)}
          />
        </label>
        <label className="field wide">
          <span>Причина</span>
          <input
            className="inp"
            type="text"
            value={reason}
            placeholder="каникулы, болезнь, отъезд"
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      </div>

      <div className="blk">
        <span className="lbl">Лимит заморозки</span>
        <div className="meter" aria-hidden="true">
          {Array.from({ length: subscription.rules.freeze_days_per_year }, (_, i) => {
            // Использованные дни — сплошные, выбранные сейчас — штриховкой:
            // видно, сколько лимита съест именно эта заморозка
            const used = i < subscription.freeze_days_used;
            const now = !used && i < subscription.freeze_days_used + Math.max(0, days);
            return <i key={i} className={used ? 'on' : now ? 'next' : ''} />;
          })}
        </div>
        <div className="stu-abon-foot">
          <span>
            Использовано {daysWord(subscription.freeze_days_used)} из {subscription.rules.freeze_days_per_year} за год
          </span>
          <span className={overLimit ? 'num bad-num' : 'num'}>осталось {left}</span>
        </div>
      </div>

      <div className="blk">
        <span className="lbl">Что получится</span>
        <div className={blocked ? 'rule warn' : 'rule'}>
          <strong>
            {badPeriod ? 'Интервал пустой' : `Заморозка на ${daysWord(days)}`}
          </strong>
          {!badPeriod && (
            <ul>
              <li>
                <span>Период</span>
                <em>
                  {dateGen(from)} — {dateGen(addDays(to, -1))}
                </em>
              </li>
              <li>
                <span>Срок действия</span>
                <em>
                  {dateGen(subscription.valid_until)} → {dateGen(validUntilAfter)}
                </em>
              </li>
              <li>
                <span>Остаток лимита</span>
                <em>{daysWord(Math.max(0, left - days))}</em>
              </li>
              <li>
                <span>Занятия</span>
                <em>≈ {cancelled} отменится</em>
              </li>
            </ul>
          )}
          <p className="warn-note">
            {badPeriod && 'Конец заморозки должен быть позже начала — выберите дату справа больше даты слева.'}
            {inPast && `Заморозка не может начинаться в прошлом: выберите дату не раньше ${dateGen(TODAY)}.`}
            {overLimit &&
              !inPast &&
              `Лимит школы — ${daysWord(subscription.rules.freeze_days_per_year)} в год, осталось ${daysWord(left)}. Сократите интервал.`}
            {!blocked &&
              `Занятия внутри интервала будут отменены без списания — ориентировочно ${lessonsWord(cancelled)}. При снятии заморозки они не восстановятся: их нужно будет поставить в расписание заново. Точное число вернёт сервер.`}
          </p>
        </div>
      </div>

      {error && (
        <div className="err-inline" role="alert">
          <strong>{error.message}</strong>
        </div>
      )}
    </Dialog>
  );
}
