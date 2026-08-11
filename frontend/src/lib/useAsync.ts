import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api';

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** Повторить запрос: кнопка «Повторить» на экране ошибки и обновление после отметки. */
  reload: () => void;
}

/**
 * Загрузка данных с отменой устаревших ответов.
 *
 * Библиотека вроде TanStack Query здесь избыточна: три запроса, кэш не нужен,
 * а гонку ответов закрывает счётчик поколений. Меньше зависимостей — меньше
 * того, что придётся обновлять и объяснять.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[], enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);
  // Поколение запроса: ответ на устаревший запрос игнорируется,
  // иначе быстрое переключение дней покажет вчерашнее расписание.
  const generation = useRef(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const current = ++generation.current;
    setLoading(true);
    setError(null);

    loaderRef
      .current()
      .then((result) => {
        if (generation.current !== current) return;
        setData(result);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (generation.current !== current) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, 'unexpected', 'Непредвиденная ошибка интерфейса. Обновите страницу.'),
        );
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return { data, error, loading, reload };
}
