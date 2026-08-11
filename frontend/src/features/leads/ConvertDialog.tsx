import { useMemo, useState } from 'react';
import {
  ApiError,
  PAYMENT_METHODS,
  PAYMENT_METHOD_LABELS,
  api,
  type LeadCard,
  type PaymentMethod,
  type Plan,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, money } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { Dialog } from '../../components/Dialog';
import type { ToastMessage } from '../../components/Toasts';
import { PlanPreviewRows, chargeOf, validUntilOf } from '../students/planPreview';

/**
 * Конверсия заявки в ученика — самая тяжёлая операция этапа: одной
 * транзакцией появляются персона, ученик, семья и, если выбран тариф,
 * проданный абонемент.
 *
 * Абонемент необязателен: ученика можно завести сегодня, а деньги принять
 * завтра — заявка всё равно уходит в «Абонемент куплен», потому что
 * ученик уже есть. Предпросмотр итога — тот же, что в продаже из карточки
 * ученика: один и тот же компонент, чтобы суммы не разошлись.
 */
export function ConvertDialog({
  lead,
  onClose,
  onConverted,
}: {
  lead: LeadCard;
  onClose: () => void;
  onConverted: (toast: Omit<ToastMessage, 'id'>, studentId: string) => void;
}) {
  const plans = useAsync<Plan[]>(() => api.plans(), []);

  // Имя из заявки разбираем на части: «Сауле Ким» — это фамилия семьи,
  // и ребёнок по умолчанию носит ту же
  const [payerFirst, payerLast] = splitName(lead.name);
  const [studentFirst, studentLast] = splitName(lead.student_name ?? lead.name, payerLast);

  const adult = (lead.student_age ?? 0) >= 18;

  const [withPayer, setWithPayer] = useState(!adult);
  const [payer, setPayer] = useState({ first: payerFirst, last: payerLast, phone: lead.phone ?? '' });
  const [student, setStudent] = useState({ first: studentFirst, last: studentLast, birth: '' });
  const [withSubscription, setWithSubscription] = useState(true);
  const [planId, setPlanId] = useState<string | null>(null);
  const [startsOn, setStartsOn] = useState(TODAY);
  const [discount, setDiscount] = useState(0);
  const [promo, setPromo] = useState(lead.promo_code ?? '');
  const [method, setMethod] = useState<PaymentMethod>('kaspi');
  const [withPayment, setWithPayment] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Тарифы направления заявки — сверху: продавать будут именно их
  const sorted = useMemo(() => {
    const all = plans.data ?? [];
    const mine = all.filter((p) => p.discipline === lead.discipline?.name);
    return [...mine, ...all.filter((p) => !mine.includes(p))];
  }, [plans.data, lead.discipline?.name]);

  const plan = sorted.find((p) => p.id === planId) ?? sorted[0];
  const charged = plan ? chargeOf(plan, discount) : 0;

  const namesFilled = student.first.trim() !== '' && student.last.trim() !== '';
  const payerFilled = !withPayer || (payer.first.trim() !== '' && payer.phone.trim() !== '');

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await api.convertLead(lead.id, {
        ...(withPayer
          ? { payer: { first_name: payer.first.trim(), last_name: payer.last.trim(), phone: payer.phone.trim() } }
          : {}),
        student: {
          first_name: student.first.trim(),
          last_name: student.last.trim(),
          ...(student.birth ? { birth_date: student.birth } : {}),
          discipline_id: lead.discipline?.id ?? null,
          branch_id: lead.branch?.id ?? null,
        },
        ...(withSubscription && plan
          ? {
              subscription: {
                plan_id: plan.id,
                starts_on: startsOn,
                discount_pct: discount,
                ...(promo.trim() ? { promo_code: promo.trim() } : {}),
                ...(withPayment ? { payment: { amount: charged, method } } : {}),
              },
            }
          : {}),
      });

      const rows = [
        { label: 'Ученик', value: `${student.first} ${student.last}`.trim() },
        { label: 'Стадия заявки', value: 'Абонемент куплен' },
      ];
      if (result.subscription_id && plan) {
        rows.push({ label: 'Абонемент', value: `${plan.lessons_count} зан. до ${dateGen(validUntilOf(plan, startsOn))}` });
        rows.push({ label: withPayment ? 'Принято' : 'К оплате', value: money(charged) });
      } else {
        rows.push({ label: 'Абонемент', value: 'не продан — продайте из карточки ученика' });
      }
      onConverted({ title: `Ученик оформлен · ${student.first} ${student.last}`.trim(), rows }, result.student_id);
    } catch (err) {
      setError(
        err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Оформить ученика не удалось. Повторите попытку.'),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Оформление ученика"
      subtitle={`${lead.name}${lead.discipline ? ` · ${lead.discipline.name}` : ''}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Отмена
          </button>
          <button className="btn pri" onClick={submit} disabled={saving || !namesFilled || !payerFilled}>
            {saving
              ? 'Оформляем…'
              : withSubscription && plan
                ? `Оформить и принять ${money(withPayment ? charged : 0)}`
                : 'Оформить без абонемента'}
          </button>
        </>
      }
    >
      <div className="blk">
        <span className="lbl">Ученик</span>
        <div className="fields">
          <label className="field">
            <span>Имя</span>
            <input
              className="inp"
              value={student.first}
              onChange={(event) => setStudent({ ...student, first: event.target.value })}
            />
          </label>
          <label className="field">
            <span>Фамилия</span>
            <input
              className="inp"
              value={student.last}
              onChange={(event) => setStudent({ ...student, last: event.target.value })}
            />
          </label>
          <label className="field">
            <span>Дата рождения</span>
            <input
              className="inp num"
              type="date"
              value={student.birth}
              onChange={(event) => setStudent({ ...student, birth: event.target.value })}
            />
          </label>
          <div className="field">
            <span>Из заявки</span>
            <p className="static">
              {lead.student_age ? `${lead.student_age} лет` : 'возраст не указан'} ·{' '}
              {lead.discipline?.name ?? 'направление не указано'} · {lead.branch?.name ?? 'филиал не указан'}
            </p>
          </div>
        </div>
      </div>

      <div className="blk">
        <label className="check">
          <input type="checkbox" checked={withPayer} onChange={(event) => setWithPayer(event.target.checked)} />
          <span>
            Платит родитель. Снимите галочку для взрослого ученика — он сам себе плательщик, семья создастся из
            одного человека.
          </span>
        </label>
        {withPayer && (
          <div className="fields" style={{ marginTop: 10 }}>
            <label className="field">
              <span>Имя плательщика</span>
              <input
                className="inp"
                value={payer.first}
                onChange={(event) => setPayer({ ...payer, first: event.target.value })}
              />
            </label>
            <label className="field">
              <span>Фамилия</span>
              <input
                className="inp"
                value={payer.last}
                onChange={(event) => setPayer({ ...payer, last: event.target.value })}
              />
            </label>
            <label className="field wide">
              <span>Телефон</span>
              <input
                className="inp num"
                value={payer.phone}
                placeholder="+7 701 555 00 03"
                onChange={(event) => setPayer({ ...payer, phone: event.target.value })}
              />
            </label>
          </div>
        )}
        <p className="hint">
          Если человек с таким телефоном уже есть, сервер переиспользует его: второй профиль на того же родителя
          отнял бы у семьи скидку за второго ребёнка.
        </p>
      </div>

      <div className="blk">
        <label className="check">
          <input
            type="checkbox"
            checked={withSubscription}
            onChange={(event) => setWithSubscription(event.target.checked)}
          />
          <span>Продать абонемент сразу. Без галочки ученик заведётся, а абонемент продадите из его карточки.</span>
        </label>
      </div>

      {withSubscription && (
        <>
          {plans.loading && <div className="skeleton" style={{ height: 100 }} />}
          {plans.error && <div className="err-inline">{plans.error.message}</div>}
          {!plans.loading && !plans.error && (
            <>
              <div className="blk">
                <span className="lbl">Тариф</span>
                <select className="inp" value={plan?.id ?? ''} onChange={(event) => setPlanId(event.target.value)}>
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

              <div className="blk">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={withPayment}
                    onChange={(event) => setWithPayment(event.target.checked)}
                  />
                  <span>Деньги приняты сейчас. Снимите галочку — абонемент оформится в долг.</span>
                </label>
              </div>

              <div className="blk">
                <span className="lbl">Что получится</span>
                <div className="rule">
                  <strong>{plan ? plan.name : 'Выберите тариф'}</strong>
                  {plan && (
                    <PlanPreviewRows
                      plan={plan}
                      startsOn={startsOn}
                      discount={discount}
                      withPayment={withPayment}
                      method={method}
                    />
                  )}
                  {promo.trim() && (
                    <p className="warn-note">
                      Промокод «{promo.trim()}» проверит сервер: итоговая сумма может стать меньше показанной.
                    </p>
                  )}
                </div>
              </div>
            </>
          )}
        </>
      )}

      {error && (
        <div className="err-inline" role="alert">
          <strong>{error.message}</strong>
        </div>
      )}
    </Dialog>
  );
}

/** «Сауле Ким» → ['Сауле', 'Ким']; одиночное имя получает фамилию семьи. */
function splitName(full: string, fallbackLast = ''): [string, string] {
  const parts = full.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return ['', fallbackLast];
  if (parts.length === 1) return [parts[0], fallbackLast];
  return [parts[0], parts.slice(1).join(' ')];
}
