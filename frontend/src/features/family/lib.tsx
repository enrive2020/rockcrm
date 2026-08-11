import type { AttendanceMark, Me, MeSubscription } from '../../api';
import { dateGen, lessonsWord, longDate, plural, relativeDay } from '../../lib/format';

/* ==========================================================================
   Общее для кабинета родителя: контакты школы, остаток абонемента и подписи.

   Кабинет собран из тех же токенов, что и административный интерфейс, но
   с другим ритмом: крупнее, спокойнее, меньше элементов на экране. Родитель
   открывает его одной рукой в дверях, а не изучает за столом.
   ========================================================================== */

export interface SchoolContacts {
  phone: string | null;
  whatsapp: string | null;
}

/**
 * Телефон и WhatsApp школы.
 *
 * Оплаты в кабинете нет намеренно — приём платежей через провайдера
 * отдельная интеграция, и обещать её, пока её нет, хуже, чем не обещать.
 * Ровно поэтому кабинет ОБЯЗАН вести к тому, кто платёж примет, и контакты
 * стоят в шапке на каждом экране, а не прячутся в подвале «о школе».
 *
 * Источник — сессия (`tenant.contacts`): в мультитенантном продукте телефон
 * школы не может лежать в сборке фронтенда, иначе все школы получат один.
 * Контракты его не описывают (пробел 36), поэтому до появления поля на живом
 * бэкенде работает запасной вариант из `VITE_SCHOOL_*` — тоже не насовсем.
 */
export function schoolContacts(me: Me): SchoolContacts {
  const phone = me.tenant.contacts?.phone || import.meta.env.VITE_SCHOOL_PHONE || null;
  const whatsapp = me.tenant.contacts?.whatsapp || import.meta.env.VITE_SCHOOL_WHATSAPP || phone;
  return { phone, whatsapp };
}

export const telHref = (phone: string): string => `tel:${phone.replace(/[^\d+]/g, '')}`;
/** wa.me принимает только цифры: плюс и пробелы ломают ссылку молча. */
export const waHref = (phone: string): string => `https://wa.me/${phone.replace(/\D/g, '')}`;

/** «+77273550101» → «+7 727 355 01 01»: номер читают и набирают глазами. */
export function prettyPhone(phone: string): string {
  const digits = phone.replace(/\D/g, '');
  if (digits.length !== 11) return phone;
  return `+${digits[0]} ${digits.slice(1, 4)} ${digits.slice(4, 7)} ${digits.slice(7, 9)} ${digits.slice(9)}`;
}

/**
 * Подписи отметок для родителя.
 *
 * Они намеренно не те, что у администратора: «Прогул без предупреждения» —
 * формулировка правила списания, и в истории собственного ребёнка она звучит
 * как обвинение. Родителю нужен факт, а не название пункта регламента.
 */
export const FAMILY_MARK_LABELS: Record<AttendanceMark, string> = {
  came: 'Был на занятии',
  late: 'Опоздал',
  no_show: 'Не пришёл',
  cancelled_early: 'Отменили заранее',
  cancelled_late: 'Отменили в последний момент',
  cancelled_teacher: 'Отменила школа',
};

/** Пришёл или нет — это разный цвет строки, и на глаз он читается быстрее слова. */
export const markTone = (mark: AttendanceMark | null): 'ok' | 'bad' | 'mute' => {
  if (mark === 'came' || mark === 'late') return 'ok';
  if (mark === 'no_show') return 'bad';
  return 'mute';
};

export const monthsWord = (n: number): string => `${n} ${plural(n, 'месяц', 'месяца', 'месяцев')}`;

/**
 * «Барабаны · Дмитрий Шарапов». Пустые части выбрасываются, а не превращаются
 * в висящую точку: направление и преподаватель у бэкенда допускают `null`,
 * и строка «· Дмитрий Шарапов» выглядит как потерянное поле.
 */
export const joinDot = (...parts: (string | null | undefined)[]): string =>
  parts.filter((part) => part && part.trim()).join(' · ');

/**
 * Заголовок дня: «Завтра, 14 августа».
 *
 * Одного «Завтра» мало — родитель сверяется с календарём в телефоне,
 * — а одной даты мало для беглого взгляда в дверях. Поэтому оба.
 */
export const dayHeading = (date: string, today: string): string => {
  const relative = relativeDay(date, today);
  return relative === longDate(date) ? relative : `${relative}, ${dateGen(date)}`;
};

/**
 * Остаток абонемента.
 *
 * Счётчик сегментами — тот же, что в панели отметки и в карточке ученика:
 * число занятий читается, а не оценивается на глаз. Предупреждение приходит
 * готовым (`ends_soon`) и не пересчитывается здесь: порог «мало» — правило
 * школы, и зашитая на клиенте двойка разъехалась бы с сервером.
 */
export function SubscriptionBlock({
  subscription,
  onRenew,
  renewSent,
}: {
  subscription: MeSubscription | null;
  onRenew: () => void;
  /** Заявка уже ушла — вторая кнопка «Продлить» выглядела бы как «не сработало». */
  renewSent: boolean;
}) {
  if (!subscription) {
    return (
      <div className="fam-abon">
        <span className="lbl">Абонемент</span>
        <div className="fam-abon-none">
          <b>Действующего абонемента нет</b>
          <p>Занятия сейчас не оплачены. Чтобы продолжить, нужно продление — школа подберёт удобный вариант.</p>
        </div>
        <RenewButton onRenew={onRenew} sent={renewSent} />
      </div>
    );
  }

  const { lessons_balance: balance, lessons_total: total, makeups_balance: makeups } = subscription;

  return (
    <div className="fam-abon">
      <span className="lbl">Абонемент</span>
      <p className="fam-abon-count">
        <b className="num">{balance}</b>
        <span>из {total} занятий осталось</span>
      </p>
      <div className="meter big" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => (
          <i key={i} className={i < balance ? 'on' : ''} />
        ))}
      </div>
      <p className="fam-abon-foot">
        Действует до {dateGen(subscription.valid_until)}
        {makeups > 0 && <> · {makeups} {plural(makeups, 'отработка', 'отработки', 'отработок')} в запасе</>}
      </p>

      {subscription.ends_soon && (
        <div className="fam-warn">
          <b>Абонемент заканчивается</b>
          <p>
            Осталось {lessonsWord(balance)}, срок — до {dateGen(subscription.valid_until)}. Чтобы занятия не прервались,
            продлите заранее: расписание держится за учеником, пока абонемент действует.
          </p>
          <RenewButton onRenew={onRenew} sent={renewSent} />
        </div>
      )}
    </div>
  );
}

function RenewButton({ onRenew, sent }: { onRenew: () => void; sent: boolean }) {
  if (sent) {
    return <p className="fam-sent">Заявка на продление отправлена. Администратор свяжется с вами.</p>;
  }
  return (
    <button className="btn pri fam-btn" onClick={onRenew}>
      Продлить абонемент
    </button>
  );
}
