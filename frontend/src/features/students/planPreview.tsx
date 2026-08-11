import type { PaymentMethod, Plan } from '../../api';
import { PAYMENT_METHOD_LABELS } from '../../api';
import { dateGen, money } from '../../lib/format';

/**
 * Предпросмотр продажи абонемента. Живёт отдельным файлом, потому что
 * продажа есть в двух местах — в карточке ученика (этап 2) и в конверсии
 * заявки (этап 3), — и обе обязаны считать итог одинаково. Разные формулы
 * в двух диалогах — это разные суммы, названные администратором родителю.
 *
 * Считаем на клиенте только потому, что эндпоинта предпросмотра нет:
 * все числа берутся из полей тарифа, а промокод помечен как «проверит сервер».
 */

/** Сдвиг календарной даты без часовых поясов: срок абонемента — про дни. */
export const addDays = (date: string, days: number): string => {
  const [y, m, d] = date.split('-').map(Number);
  const at = new Date(Date.UTC(y, m - 1, d));
  at.setUTCDate(at.getUTCDate() + days);
  return at.toISOString().slice(0, 10);
};

/** Сумма к оплате после скидки. Промокод сюда не входит — его знает сервер. */
export const chargeOf = (plan: Plan, discountPct: number): number =>
  Math.round(plan.price * (1 - discountPct / 100));

/**
 * Последний день включительно: тариф на 31 день, начатый 1 сентября,
 * действует по 1 октября — так же считает пример в контракте.
 */
export const validUntilOf = (plan: Plan, startsOn: string): string => addDays(startsOn, plan.valid_days - 1);

export function PlanPreviewRows({
  plan,
  startsOn,
  discount,
  withPayment,
  method,
  carried = 0,
}: {
  plan: Plan;
  startsOn: string;
  discount: number;
  withPayment: boolean;
  method: PaymentMethod;
  carried?: number;
}) {
  const charged = chargeOf(plan, discount);

  return (
    <ul>
      <li>
        <span>Занятий</span>
        <em>
          {plan.lessons_count}
          {carried > 0 ? ` + ${carried} перенос` : ''}
        </em>
      </li>
      <li>
        <span>Действует</span>
        <em>
          {dateGen(startsOn)} — {dateGen(validUntilOf(plan, startsOn))}
        </em>
      </li>
      <li>
        <span>Цена тарифа</span>
        <em>{money(plan.price)}</em>
      </li>
      {discount > 0 && (
        <li>
          <span>Скидка {discount}%</span>
          <em>−{money(plan.price - charged)}</em>
        </li>
      )}
      <li>
        <span>К оплате</span>
        <em>{money(charged)}</em>
      </li>
      <li>
        <span>Оплата</span>
        <em>{withPayment ? PAYMENT_METHOD_LABELS[method] : 'в долг'}</em>
      </li>
    </ul>
  );
}
