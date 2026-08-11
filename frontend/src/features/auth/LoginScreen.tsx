import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, USE_MOCKS, api, type CodeSent, type Me } from '../../api';
import { TENANT_SLUG } from '../../lib/tenant';
import { useTheme } from '../../lib/useTheme';

/**
 * Экран входа.
 *
 * Два шага, потому что их два и на самом деле: сначала школа высылает код
 * на телефон, потом человек его вводит. Одна форма с двумя полями означала бы
 * пустое поле кода, которое непонятно чем заполнять.
 *
 * Пароль вынесен отдельной вкладкой и только для сотрудников: администратор
 * ресепшена входит по двадцать раз в день, и SMS на каждый вход — это не
 * безопасность, а счёт от оператора. Прислать код и пароль вместе нельзя
 * (сервер ответит 400), поэтому вкладки разведены по состоянию, а не
 * по двум полям одной формы.
 *
 * Это первое, что видит клиент школы, поэтому экран собран из тех же токенов,
 * что и остальной продукт: другой шрифт и другой акцент здесь читались бы
 * как чужая страница, на которой вводить свой номер не стоит.
 */

/**
 * Сколько ждать после 429. Сервер не присылает `Retry-After`, но во всех
 * четырёх своих формулировках называет одно и то же окно — четверть часа
 * (`auth.py`, `ATTEMPT_WINDOW`). Держим это число здесь одной константой,
 * чтобы оно не разъехалось по коду; машинного источника у него пока нет.
 */
const LOCK_MS = 15 * 60 * 1000;

type Mode = 'code' | 'password';

export function LoginScreen({
  notice,
  error,
  onRetry,
  onSignedIn,
}: {
  notice: string | null;
  error: ApiError | null;
  onRetry: () => void;
  onSignedIn: (me: Me) => void;
}) {
  const [, toggleTheme] = useTheme();
  const [mode, setMode] = useState<Mode>('code');
  const [login, setLogin] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [sent, setSent] = useState<CodeSent | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);

  /** Момент протухания кода и момент снятия блокировки — оба в мс эпохи. */
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const codeInput = useRef<HTMLInputElement>(null);

  // Один таймер на оба отсчёта: их видно только пока они идут.
  useEffect(() => {
    if (expiresAt === null && lockedUntil === null) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [expiresAt, lockedUntil]);

  const locked = lockedUntil !== null && lockedUntil > now;
  const codeAlive = expiresAt !== null && expiresAt > now;
  const step: 'login' | 'code' = mode === 'code' && sent !== null ? 'code' : 'login';

  /**
   * 429 — это не «код не тот», и путать их нельзя: после 401 человек набирает
   * следующий код, после 429 любой набор бесполезен ещё четверть часа.
   * Поэтому 429 гасит и отправку, и «выслать заново».
   */
  function handleFailure(err: unknown) {
    const problem = err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Непредвиденная ошибка интерфейса.');
    setFailure(problem);
    if (problem.status === 429) {
      setLockedUntil(Date.now() + LOCK_MS);
      setNow(Date.now());
    }
  }

  async function requestCode(again: boolean) {
    if (busy || locked) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await api.requestCode(TENANT_SLUG, login.trim());
      setSent(result);
      setExpiresAt(Date.now() + result.expires_in * 1000);
      setNow(Date.now());
      if (again) setCode('');
      // Фокус в поле кода: человек только что смотрел на телефон,
      // и вернуться в браузер он должен сразу на нужное поле.
      setTimeout(() => codeInput.current?.focus(), 0);
    } catch (err) {
      handleFailure(err);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || locked) return;

    if (step === 'login' && mode === 'code') {
      await requestCode(false);
      return;
    }

    setBusy(true);
    setFailure(null);
    try {
      // Ровно одно из двух: код и пароль вместе сервер отвергает как 400.
      const payload =
        mode === 'code'
          ? { tenant: TENANT_SLUG, login: login.trim(), code: code.trim() }
          : { tenant: TENANT_SLUG, login: login.trim(), password };
      const result = await api.login(payload);
      onSignedIn(result.user);
    } catch (err) {
      handleFailure(err);
      if (mode === 'code') setCode('');
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: Mode) {
    setMode(next);
    setFailure(null);
    setCode('');
    setPassword('');
    setSent(null);
    setExpiresAt(null);
  }

  const canSubmit =
    !busy &&
    !locked &&
    (step === 'login'
      ? mode === 'code'
        ? login.trim().length >= 4
        : login.trim().length >= 4 && password.length > 0
      : code.trim().length === 6);

  return (
    <div className="login">
      <button className="theme-btn login-theme" onClick={toggleTheme} title="Сменить тему">
        ◐
      </button>

      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <b>RockCRM</b>
          <span>{TENANT_SLUG}</span>
        </div>

        <h1 className="h1">{step === 'code' ? 'Введите код' : 'Вход в кабинет'}</h1>
        <p className="sub">
          {step === 'code' ? (
            <>
              Шесть цифр отправлены на <b className="num">{sent?.to}</b>
            </>
          ) : mode === 'code' ? (
            'Введите номер телефона — школа вышлет одноразовый код.'
          ) : (
            'Вход по паролю — для сотрудников школы.'
          )}
        </p>

        {/* Экран входа обязан объяснять, почему человек здесь оказался:
            «вы вышли» и «сессию завершили на другом устройстве» — разные
            события, и молчание вместо объяснения читается как поломка. */}
        {notice && step === 'login' && !failure && <p className="login-notice">{notice}</p>}

        {error && (
          <div className="err-inline">
            {error.message}
            <button type="button" className="btn slim" style={{ marginTop: 8 }} onClick={onRetry}>
              Повторить
            </button>
          </div>
        )}

        {step === 'login' && (
          <div className="seg-ctl login-tabs">
            <button type="button" aria-pressed={mode === 'code'} onClick={() => switchMode('code')}>
              По коду
            </button>
            <button type="button" aria-pressed={mode === 'password'} onClick={() => switchMode('password')}>
              По паролю
            </button>
          </div>
        )}

        {step === 'login' ? (
          <>
            <label className="field wide">
              <span>Телефон</span>
              <input
                className="inp"
                type="tel"
                autoFocus
                autoComplete="username"
                inputMode="tel"
                placeholder="+7 701 555 24 18"
                value={login}
                onChange={(event) => setLogin(event.target.value)}
              />
            </label>
            {mode === 'password' && (
              <label className="field wide">
                <span>Пароль</span>
                <input
                  className="inp"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
            )}
          </>
        ) : (
          <label className="field wide">
            <span>Код из сообщения</span>
            <input
              ref={codeInput}
              className="inp code-inp num"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              placeholder="______"
              value={code}
              // Только цифры: сервер всё равно не примет остальное,
              // а вставка «код: 018398» — обычное дело.
              onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
            />
          </label>
        )}

        {failure && (
          <div className="err-inline">
            {failure.message}
            {failure.status === 429 && locked && (
              <p style={{ margin: '6px 0 0' }}>Повторить можно через {formatLeft(lockedUntil - now)}.</p>
            )}
          </div>
        )}

        <button className="btn pri wide" type="submit" disabled={!canSubmit}>
          {busy
            ? 'Отправляем…'
            : step === 'code'
              ? 'Войти'
              : mode === 'password'
                ? 'Войти'
                : 'Выслать код'}
        </button>

        {step === 'code' && (
          <div className="login-foot">
            <span className={codeAlive ? 'dim' : 'bad-num'}>
              {locked
                ? 'Отправка кодов приостановлена'
                : codeAlive
                  ? `Код действует ещё ${formatLeft(expiresAt - now)}`
                  : 'Срок кода истёк — запросите новый'}
            </span>
            <button
              type="button"
              className="btn slim"
              // Гасим на время блокировки: кнопка, которая гарантированно
              // вернёт 429, только злит — человек жмёт её, и лимит копится.
              disabled={busy || locked || codeAlive}
              onClick={() => requestCode(true)}
              title={
                locked
                  ? 'Слишком много попыток — подождите 15 минут'
                  : codeAlive
                    ? 'Прежний код ещё действует'
                    : undefined
              }
            >
              Выслать заново
            </button>
          </div>
        )}

        {step === 'code' && (
          <button
            type="button"
            className="link login-back"
            onClick={() => {
              setSent(null);
              setCode('');
              setExpiresAt(null);
              setFailure(null);
            }}
          >
            Изменить номер
          </button>
        )}

        <p className="hint">
          {USE_MOCKS
            ? 'Мок-режим: бэкенда нет, код 123456, пароль rockschool. Роль сессии задаётся VITE_MOCK_ROLE.'
            : 'Код приходит сообщением. Если его нет — проверьте номер: он должен быть тем, что записан в школе.'}
        </p>
      </form>
    </div>
  );
}

/** «4:59» — минуты и секунды: «299 секунд» человек в уме не переводит. */
function formatLeft(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}
