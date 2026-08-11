import { useEffect } from 'react';

export interface ToastRow {
  label: string;
  value: string;
}

export interface ToastMessage {
  id: number;
  title: string;
  rows: ToastRow[];
  tone?: 'ok' | 'bad';
}

/**
 * Итог операции показывается тем же списком «строка → значение», что и
 * предпросмотр: администратор сравнивает обещанное и применённое одним взглядом.
 */
export function Toasts({
  items,
  onDismiss,
  shifted,
}: {
  items: ToastMessage[];
  onDismiss: (id: number) => void;
  /** Панель открыта — уведомления сдвигаются, чтобы её не перекрывать. */
  shifted: boolean;
}) {
  return (
    <div className={shifted ? 'toasts' : 'toasts wide'} aria-live="polite">
      {items.map((t) => (
        <Toast key={t.id} item={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function Toast({ item, onDismiss }: { item: ToastMessage; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(item.id), 6500);
    return () => clearTimeout(timer);
  }, [item.id, onDismiss]);

  return (
    <div className={item.tone === 'bad' ? 'toast bad' : 'toast'}>
      <b>{item.title}</b>
      <ul>
        {item.rows.map((row, i) => (
          <li key={i}>
            <span>{row.label}</span>
            <em>{row.value}</em>
          </li>
        ))}
      </ul>
    </div>
  );
}
