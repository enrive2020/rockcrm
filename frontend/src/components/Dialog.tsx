import { useEffect, useRef, type ReactNode } from 'react';

/**
 * Модальное окно для операций с деньгами и сроками: продажа абонемента
 * и заморозка. Это не боковая панель отметки — продажа требует
 * сосредоточенности и не должна соседствовать с расписанием, по которому
 * легко случайно кликнуть.
 *
 * Своя реализация, а не <dialog>: нужна одинаковая отрисовка в обеих темах
 * и предсказуемое поведение прокрутки, а нативный ::backdrop ими не управляет.
 */
export function Dialog({
  title,
  subtitle,
  onClose,
  footer,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // Фокус уводим внутрь окна, иначе Tab уйдёт бродить по расписанию за ним
    panel.current?.querySelector<HTMLElement>('input, select, button')?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="ovl" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="dlg" role="dialog" aria-modal="true" aria-label={title} ref={panel}>
        <div className="insp-h">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="x" onClick={onClose} title="Закрыть (Esc)">
            ✕
          </button>
        </div>
        <div className="dlg-b">{children}</div>
        <div className="insp-f">{footer}</div>
      </div>
    </div>
  );
}
