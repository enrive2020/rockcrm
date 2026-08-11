import type { ApiError } from '../api';
import { USE_MOCKS } from '../api';

/** Скелет расписания: та же сетка, что у настоящих дорожек — экран не прыгает. */
export function ScheduleSkeleton() {
  return (
    <div className="tl-wrap" aria-busy="true" aria-label="Расписание загружается">
      {[0, 1, 2, 3].map((row) => (
        <div className="sk-row" key={row}>
          <div className="skeleton" style={{ width: 176, height: 30 }} />
          <div className="skeleton" style={{ width: `${18 + ((row * 13) % 30)}%`, height: 34 }} />
          <div className="skeleton" style={{ width: `${14 + ((row * 7) % 22)}%`, height: 34 }} />
        </div>
      ))}
    </div>
  );
}

/** Пустой день — не ошибка, поэтому тон нейтральный и подсказка конкретная. */
export function EmptyDay({ date }: { date: string }) {
  return (
    <div className="state">
      <span className="lbl">Занятий нет</span>
      <h3>{date} — пустой день</h3>
      <p>В этом филиале на выбранную дату занятий не запланировано. Проверьте другую дату или другой филиал.</p>
    </div>
  );
}

/**
 * Ошибка показывает `message` от сервера дословно: контракт требует,
 * чтобы сообщение объясняло, что произошло и что делать.
 */
export function ErrorState({
  error,
  onRetry,
  title = 'Данные не загрузились',
}: {
  error: ApiError;
  onRetry?: () => void;
  title?: string;
}) {
  // Прокси dev-сервера отвечает 500, когда бэкенда нет вовсе, — для человека
  // это тот же случай, что и отсутствие сети: подсказка нужна в обоих
  const backendMissing = error.code === 'network_error' || error.status >= 500;

  return (
    <div className="state" role="alert">
      <span className="lbl">Ошибка · {error.code}</span>
      <h3>{title}</h3>
      <p>{error.message}</p>
      {!USE_MOCKS && backendMissing && (
        <p>
          Мок-режим выключен. Чтобы работать без бэкенда, задайте <code>VITE_USE_MOCKS=true</code> и перезапустите
          dev-сервер.
        </p>
      )}
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}
