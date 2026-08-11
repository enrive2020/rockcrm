import { useMemo, useState } from 'react';
import {
  ApiError,
  api,
  PAYMENT_METHODS,
  PAYMENT_METHOD_LABELS,
  type PaymentMethod,
  type Plan,
  type StudentCard,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, lessonsWord, money } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { Dialog } from '../../components/Dialog';
import type { ToastMessage } from '../../components/Toasts';
import { PlanPreviewRows, addDays, chargeOf } from './planPreview';

/**
 * Продажа абонемента и продление — одна форма: продление есть продажа
 * следующего абонемента.
 *
 * Главное здесь — предпросмотр итога, тот же принцип, что и с отметкой
 * посещаемости: администратор видит, сколько занятий, до какого числа
 * и сколько денег, ПРЕЖДЕ чем нажать.
 *
 * Разница с отметкой в одном: эндпоинта предпросмотра продажи контракт
 * не даёт, поэтому итог считается здесь — из полей тарифа (`lessons_count`,
 * `valid_days`, `price`) и введённой скидки. Это чистая арифметика по данным
 * сервера. Единственное, чего клиент знать не может, — действие промокода;
 * поэтому промокод помечен как «проверит сервер», а после ответа итог
 * показывается ещё раз, уже словами сервера.
 */
export function SellDialog({
  student,
  onClose,
  onSold,
}: {
  student: StudentCard;
  onClose: () => void;
  onSold: (toast: Omit<ToastMessage, 'id'>) => void;
}) {
  const plans = useAsync<Plan[]>(() => api.plans(), []);
  const current = student.subscription;

  // Продление начинается на следующий день после конца текущего абонемента:
  // это самый частый случай, и он же единственный, который не даст 422
  const defaultStart = current ? addDays(current.valid_until, 1) : TODAY;

  const [planId, setPlanId] = useState<string | null>(null);
  const [startsOn, setStartsOn] = useState(defaultStart);
  const [discount, setDiscount] = useState(student.family?.discount_pct ?? 0);
  const [promo, setPromo] = useState('');
  const [method, setMethod] = useState<PaymentMethod>('kaspi');
  const [withPayment, setWithPayment] = useState(true);
  const [carryOver, setCarryOver] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Тарифы направления ученика — сверху: их продают в девяти случаях из десяти.
  // Фильтровать запросом нельзя: контракт принимает discipline_id, а карточка
  // отдаёт только название направления (см. отчёт о пробелах).
  const sorted = useMemo(() => {
    const all = plans.data ?? [];
    const mine = all.filter((p) => p.discipline === student.discipline);
    return [...mine, ...all.filter((p) => !mine.includes(p))];
  }, [plans.data, student.discipline]);

  // По умолчанию выбран тот же тариф, что у действующего абонемента: продление
  // на тех же условиях — девять продаж из десяти. Сопоставляем по названию,
  // потому что карточка не отдаёт plan_id проданного абонемента.
  const preferred = (current && sorted.find((p) => p.name === current.plan_name)) || sorted[0];
  const plan = sorted.find((p) => p.id === planId) ?? preferred;
  const carryLimit = current?.rules.carry_over_lessons ?? 0;
  const carried = carryOver && current ? Math.min(current.lessons_balance, carryLimit) : 0;

  const charged = plan ? chargeOf(plan, discount) : 0;
  const overlaps = Boolean(current && startsOn >= current.valid_from && startsOn <= current.valid_until);

  const submit = async () => {
    if (!plan) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.sellSubscription(student.id, {
        plan_id: plan.id,
        starts_on: startsOn,
        discount_pct: discount,
        ...(promo.trim() ? { promo_code: promo.trim() } : {}),
        ...(withPayment ? { payment: { amount: charged, method } } : {}),
        carry_over: carryOver,
      });
      const rows = [
        { label: 'Занятий', value: `${result.lessons_total}${result.carried_over ? ` (перенос ${result.carried_over})` : ''}` },
        { label: 'Действует до', value: dateGen(result.valid_until) },
        { label: 'К оплате', value: money(result.charged) },
      ];
      if (result.discount_pct > 0) rows.push({ label: 'Скидка', value: `−${result.discount_pct}%` });
      if (result.debt > 0) rows.push({ label: 'Долг', value: money(result.debt) });
      onSold({ title: `Абонемент продан · ${student.name}`, rows });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(0, 'unexpected', 'Продать абонемент не удалось. Повторите попытку.'),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Продажа абонемента"
      subtitle={`${student.name} · ${student.discipline}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Отмена
          </button>
          <button className="btn pri" onClick={submit} disabled={!plan || saving || overlaps}>
            {saving ? 'Продаём…' : withPayment ? `Продать и принять ${money(charged)}` : 'Продать в долг'}
          </button>
        </>
      }
    >
      {plans.loading && <div className="skeleton" style={{ height: 120 }} />}
      {plans.error && <div className="err-inline">{plans.error.message}</div>}

      {!plans.loading && !plans.error && (
        <>
          <div className="blk">
            <span className="lbl">Тариф</span>
            <select
              className="inp"
              value={plan?.id ?? ''}
              onChange={(event) => setPlanId(event.target.value)}
              aria-label="Тариф"
            >
              {sorted.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — {p.lessons_count} зан. · {p.valid_days} дн. · {money(p.price)}
                </option>
              ))}
            </select>
          </div>

          <div className="blk fields">
            <label className="field">
              <span>Дата начала</span>
              <input
                className="inp num"
                type="date"
                value={startsOn}
                onChange={(event) => event.target.value && setStartsOn(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Скидка, %</span>
              <input
                className="inp num"
                type="number"
                min={0}
                max={100}
                value={discount}
                onChange={(event) => setDiscount(Math.max(0, Math.min(100, Number(event.target.value) || 0)))}
              />
            </label>
            <label className="field">
              <span>Промокод</span>
              <input
                className="inp"
                type="text"
                value={promo}
                placeholder="необязательно"
                onChange={(event) => setPromo(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Способ оплаты</span>
              <select
                className="inp"
                value={method}
                disabled={!withPayment}
                onChange={(event) => setMethod(event.target.value as PaymentMethod)}
              >
                {PAYMENT_METHODS.map((m) => (
                  <option key={m} value={m}>
                    {PAYMENT_METHOD_LABELS[m]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="blk checks">
            <label className="check">
              <input type="checkbox" checked={withPayment} onChange={(event) => setWithPayment(event.target.checked)} />
              <span>Деньги приняты сейчас. Снимите галочку — абонемент оформится в долг.</span>
            </label>
            {current && (
              <label className={carryLimit > 0 ? 'check' : 'check off'}>
                <input
                  type="checkbox"
                  checked={carryOver}
                  disabled={carryLimit === 0}
                  onChange={(event) => setCarryOver(event.target.checked)}
                />
                <span>
                  Перенести остаток ({lessonsWord(current.lessons_balance)}).{' '}
                  {carryLimit === 0
                    ? 'Правило школы — перенос запрещён, остаток сгорит.'
                    : `Правило школы — до ${lessonsWord(carryLimit)}.`}
                </span>
              </label>
            )}
          </div>

          {/* Итог до подтверждения — то, ради чего сделан этот диалог */}
          <div className="blk">
            <span className="lbl">Что получится</span>
            <div className={overlaps ? 'rule warn' : 'rule'}>
              <strong>{plan ? plan.name : 'Выберите тариф'}</strong>
              {plan && (
                <PlanPreviewRows
                  plan={plan}
                  startsOn={startsOn}
                  discount={discount}
                  withPayment={withPayment}
                  method={method}
                  carried={carried}
                />
              )}
              {overlaps && current && (
                <p className="warn-note">
                  У ученика уже есть абонемент до {dateGen(current.valid_until)}. Продление начинается со следующего дня —{' '}
                  {dateGen(addDays(current.valid_until, 1))}.
                </p>
              )}
              {!overlaps && promo.trim() && (
                <p className="warn-note">
                  Промокод «{promo.trim()}» проверит сервер: итоговая сумма может стать меньше, чем показано выше.
                </p>
              )}
            </div>
          </div>

          {error && (
            <div className="err-inline" role="alert">
              <strong>{error.message}</strong>
            </div>
          )}
        </>
      )}
    </Dialog>
  );
}
