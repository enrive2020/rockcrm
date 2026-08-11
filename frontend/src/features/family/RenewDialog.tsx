import { useState } from 'react';
import { ApiError, api } from '../../api';
import { Dialog } from '../../components/Dialog';
import { prettyPhone, telHref, waHref, type SchoolContacts } from './lib';

/** Диалог открывается и с главного экрана, и из карточки ребёнка — а это
 *  два разных ресурса. Общее у них ровно эти три поля. */
export interface RenewTarget {
  student_id: string;
  full_name: string;
  discipline: string | null;
}

/**
 * Заявка на продление.
 *
 * Родитель не оплачивает в кабинете: приём платежей через провайдера —
 * отдельная интеграция, и кнопка «Оплатить», которая на самом деле никуда
 * не ведёт, хуже её отсутствия. Поэтому здесь заявка администратору,
 * а рядом — телефон и WhatsApp: пока оплаты нет, кабинет обязан довести
 * до того, кто её примет, и не оставить родителя в тупике.
 */
export function RenewDialog({
  child,
  contacts,
  onClose,
  onSent,
}: {
  child: RenewTarget;
  contacts: SchoolContacts;
  onClose: () => void;
  onSent: (message: string) => void;
}) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestRenew(child.student_id, { comment: comment.trim() });
      onSent(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Заявка не ушла. Попробуйте ещё раз.'));
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Продлить абонемент"
      subtitle={child.discipline ? `${child.full_name} · ${child.discipline}` : child.full_name}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Отмена
          </button>
          <button className="btn pri" onClick={send} disabled={busy}>
            {busy ? 'Отправляем…' : 'Отправить заявку'}
          </button>
        </>
      }
    >
      <p className="fam-dlg-note">
        Оплата принимается в школе — администратор подберёт абонемент, назовёт сумму и пришлёт способ оплаты. Заявка
        нужна, чтобы он позвонил первым.
      </p>

      <div className="field wide fam-field">
        <span>Пожелания (необязательно)</span>
        <textarea
          className="inp fam-area"
          rows={3}
          value={comment}
          placeholder="Например: хотим 2 раза в неделю с сентября"
          onChange={(event) => setComment(event.target.value)}
        />
      </div>

      {/* Быстрее заявки: если родитель уже стоит на ресепшене или спешит,
          звонок решает вопрос сразу, и прятать эту возможность нельзя. */}
      {contacts.phone && (
        <div className="fam-field">
          <span className="lbl">Или свяжитесь сразу</span>
          <div className="fam-contact-row">
            <a className="btn fam-btn" href={telHref(contacts.phone)}>
              Позвонить {prettyPhone(contacts.phone)}
            </a>
            {contacts.whatsapp && (
              <a className="btn fam-btn" href={waHref(contacts.whatsapp)} target="_blank" rel="noreferrer">
                Написать в WhatsApp
              </a>
            )}
          </div>
        </div>
      )}

      {error && <p className="err-inline">{error.message}</p>}
    </Dialog>
  );
}
