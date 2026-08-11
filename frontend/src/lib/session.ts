import { useCallback, useEffect, useState } from 'react';
import { ApiError, api, setUnauthorizedHandler, type Me } from '../api';

type Status = 'loading' | 'anon' | 'ready';

export interface SessionState {
  status: Status;
  me: Me | null;
  /**
   * Почему человек оказался на экране входа. Пусто — он просто открыл кабинет
   * первый раз; «сессия истекла» и «вы вышли» звучат по-разному, и молчание
   * вместо объяснения читается как «меня выкинуло непонятно почему».
   */
  notice: string | null;
  /** Сеть или сервер не ответили: это не «войдите», это «бэкенд лежит». */
  error: ApiError | null;
  signIn: (me: Me) => void;
  signOut: (everywhere?: boolean) => Promise<void>;
  retry: () => void;
}

/**
 * Сессия приложения.
 *
 * На старте — один запрос `GET /auth/me`: он и есть источник правды о роли,
 * филиалах и видимых учениках. Держать роль в localStorage нельзя по той же
 * причине, по которой там нет токена: всё, что лежит на клиенте, клиент
 * и назначил себе сам.
 *
 * 401 из любого другого запроса означает, что сессия кончилась или её погасили
 * «выйти на всех устройствах», — и тогда приложение обязано вернуться на вход,
 * а не показывать экран с ошибкой загрузки.
 */
export function useSession(): SessionState {
  const [status, setStatus] = useState<Status>('loading');
  const [me, setMe] = useState<Me | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let alive = true;
    setStatus('loading');
    setError(null);

    api
      .me()
      .then((user) => {
        if (!alive) return;
        setMe(user);
        setStatus('ready');
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setMe(null);
        if (err instanceof ApiError && err.status !== 401) {
          // Сервер не отвечает — форма входа тут не поможет, и притворяться,
          // что «вы не вошли», значило бы отправить человека набирать код
          // в мёртвый бэкенд.
          setError(err);
        }
        setStatus('anon');
      });

    return () => {
      alive = false;
    };
  }, [nonce]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe(null);
      setStatus('anon');
      setNotice('Сессия истекла или её завершили на другом устройстве. Войдите заново.');
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback((user: Me) => {
    setMe(user);
    setNotice(null);
    setError(null);
    setStatus('ready');
  }, []);

  const signOut = useCallback(async (everywhere = false) => {
    let message = everywhere ? 'Вы вышли из всех сеансов.' : 'Вы вышли.';
    try {
      // Текст берём от сервера: «вы вышли» и «вы вышли из всех сеансов» —
      // разные действия, и подтверждать надо то, которое произошло.
      const result = await api.logout(everywhere);
      message = result.message;
    } catch {
      // Сессия могла протухнуть раньше, чем нажали «Выйти». Локальное
      // состояние всё равно сбрасываем: держать экран владельца открытым
      // после нажатия «Выйти» — худший из вариантов.
    }
    setMe(null);
    setNotice(message);
    setStatus('anon');
  }, []);

  const retry = useCallback(() => setNonce((n) => n + 1), []);

  return { status, me, notice, error, signIn, signOut, retry };
}
