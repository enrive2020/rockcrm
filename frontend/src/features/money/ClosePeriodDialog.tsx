import { useState } from 'react';
import { ApiError, api, type PayrollSheet } from '../../api';
import { dateGen, money, periodTitle, plural, shiftDate, signedMoney } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { Dialog } from '../../components/Dialog';

/**
 * Закрытие периода — единственное необратимое действие экрана.
 *
 * Открытого периода не существует: строка заводится сразу закрытой, штампует
 * все непроштампованные начисления и обратной операции не имеет. Ошибка
 * чинится корректировкой в следующем периоде, а не переоткрытием —
 * и предупредить об этом нужно до нажатия, а не после.
 *
 * Поэтому диалог показывает три вещи: что именно будет закрыто (сколько
 * начислений и на какую сумму), что вернуть это нельзя, и что случится
 * с отметками, которые поставят задним числом.
 */
export function ClosePeriodDialog({
  from,
  to,
  sheet,
  onClose,
  onClosed,
}: {
  from: string;
  to: string;
  sheet: PayrollSheet;
  onClose: () => void;
  onClosed: (message: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  /** Галочка «понимаю, что отменить нельзя»: необратимое не должно закрываться одним кликом. */
  const [acknowledged, setAcknowledged] = useState(false);

  const totals = sheet.totals;
  const empty = totals.entries === 0;
  /**
   * Незакончившийся период закрыть нельзя: занятия, которые в нём ещё пройдут,
   * целиком уехали бы корректировкой. Сервер отвечает на это `422`, но
   * условие проверяемо на клиенте — гасим кнопку и объясняем причину,
   * как у продажи внутрь действующего периода во втором этапе.
   */
  const notOver = to >= TODAY;

  // Целый месяц называем месяцем и добавляем даты; произвольный отрезок
  // periodTitle и так печатает датами — повторять их дважды незачем
  const range = `${dateGen(from)} — ${dateGen(to)}`;
  const title = periodTitle(from, to);

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const response = await api.closePayrollPeriod(from, to);
      // Числа повторяем сервером: его формулировка и есть итог операции
      onClosed(response.message);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Непредвиденная ошибка интерфейса.'));
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Закрыть период"
      subtitle={title === range ? `${range} включительно` : `${title} · ${range} включительно`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Не закрывать
          </button>
          <button className="btn pri" onClick={submit} disabled={saving || notOver || empty || !acknowledged}>
            {saving ? 'Закрываем…' : 'Закрыть период'}
          </button>
        </>
      }
    >
      <div className="blk">
        <span className="lbl">Что будет закрыто</span>
        <div className="kv">
          <span>Начислений</span>
          <b className="num">{totals.entries}</b>
        </div>
        <div className="kv">
          <span>Преподавателей</span>
          <b className="num">{totals.teachers}</b>
        </div>
        <div className="kv">
          <span>Занятий</span>
          <b className="num">{totals.lessons}</b>
        </div>
        {totals.corrections !== 0 && (
          <div className="kv">
            <span>В том числе правки</span>
            <b className="num">{signedMoney(totals.corrections)}</b>
          </div>
        )}
        {totals.carried_over_entries > 0 && (
          <div className="kv">
            <span>Из прошлых месяцев</span>
            <b className="num">
              {signedMoney(totals.carried_over)} · {totals.carried_over_entries}{' '}
              {plural(totals.carried_over_entries, 'строка', 'строки', 'строк')}
            </b>
          </div>
        )}
        <div className="kv">
          <span>Сумма к выплате</span>
          <b className="num">{money(totals.total)}</b>
        </div>
      </div>

      {/* Предупреждение стоит ДО кнопки и рядом с суммой, а не в уведомлении
          после нажатия: после нажатия его читать уже поздно */}
      <div className="rule warn">
        <strong>Обратной операции нет</strong>
        <p className="warn-note">
          Переоткрыть закрытый период нельзя. Ошибка чинится корректировкой в следующем периоде: если после
          закрытия кто-то поставит отметку за эти дни, начисление придёт в следующую ведомость строкой «из прошлых
          месяцев», а не изменит эту сумму.
        </p>
      </div>

      {notOver && (
        <div className="rule warn" style={{ marginTop: 10 }}>
          <strong>Период ещё не закончился</strong>
          <p className="warn-note">
            Последний день периода — {dateGen(to)}, а сегодня {dateGen(TODAY)}. Занятия, которые в этом периоде ещё
            пройдут, целиком уехали бы корректировкой в следующий месяц. Закрыть его можно с {dateGen(shiftDate(to, 1))}.
          </p>
        </div>
      )}

      {empty && !notOver && (
        <div className="rule warn" style={{ marginTop: 10 }}>
          <strong>Закрывать нечего</strong>
          <p className="warn-note">За этот период нет ни одного начисления.</p>
        </div>
      )}

      {!notOver && !empty && (
        <label className="check" style={{ marginTop: 14 }}>
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>
            Понимаю: {money(totals.total)} по {totals.teachers}{' '}
            {plural(totals.teachers, 'преподавателю', 'преподавателям', 'преподавателям')} будут проштампованы, и
            отменить это нельзя.
          </span>
        </label>
      )}

      {error && (
        <div className="err-inline" role="alert">
          <strong>{error.message}</strong>
        </div>
      )}
    </Dialog>
  );
}
