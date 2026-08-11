-- ============================================================================
-- RockCRM · 010 · Настоящая авторизация: сессии, одноразовые коды, попытки входа
--
-- Заголовки X-Tenant-Id и X-User-Id были заглушкой этапа 1: их знает любой,
-- кто открыл вкладку разработчика, и подставить чужую школу было делом одной
-- строки в консоли. Тенант теперь приезжает из сессии, а сессия — из того,
-- что человек предъявил при входе.
--
-- Изоляция строк при этом не меняется ни на строчку: app.tenant_id по-прежнему
-- выставляется в начале каждой транзакции, просто значение для него берётся
-- не из заголовка, а из user_session.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Сессия — то, что предъявляет браузер вместо логина и пароля.
--
-- Хранится ХЕШЕМ, как ключ внешнего источника: утечка базы не должна давать
-- возможность войти под чужим именем. Открытый токен существует только
-- в куке браузера и в ответе на вход.
-- ---------------------------------------------------------------------------
CREATE TABLE user_session (
  id           uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenant(id)   ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,

  -- SHA-256 от 32 случайных байт. Медленный хеш здесь не нужен по той же
  -- причине, что и у api_key: перебирать нечего, а проверяется токен
  -- на каждом запросе и ищется по индексу.
  token_hash   text NOT NULL UNIQUE,

  issued_at    timestamptz NOT NULL DEFAULT now(),
  -- Момент последнего запроса. Нужен и человеку («последний вход с телефона»),
  -- и уборке протухших сессий.
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  -- Выход не удаляет строку, а гасит её: «кто и когда вышел» — такой же факт,
  -- как «кто и когда вошёл», и стирать половину пары незачем.
  revoked_at   timestamptz,

  user_agent   text,
  ip           inet,

  CHECK (expires_at > issued_at)
);

CREATE INDEX user_session_user ON user_session (tenant_id, user_id, issued_at DESC);
CREATE INDEX user_session_live ON user_session (expires_at) WHERE revoked_at IS NULL;

COMMENT ON TABLE user_session IS
  'Сессии людей. Ищутся по хешу токена ДО того, как известен тенант.';

-- ---------------------------------------------------------------------------
-- Политик уровня строк на user_session нет — ровно по той же причине,
-- что и у api_key (db/006): приложение ищет сессию ИМЕННО ЗАТЕМ, чтобы узнать
-- тенанта. Политика на current_tenant() отсекла бы строку раньше, чем тенант
-- станет известен, и войти не смог бы никто.
--
-- Защита строится на том, что в таблице нет ничего, чем можно воспользоваться:
-- лежит хеш, а не токен. Выдача и гашение — работа самого приложения,
-- поэтому права здесь полные, в отличие от api_key.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON user_session TO rockcrm_app;

-- ---------------------------------------------------------------------------
-- Одноразовый код входа.
--
-- Родители не помнят паролей — основной сценарий входа именно этот. Код
-- хранится хешем: дамп базы не должен давать войти в чужой кабинет, пока код
-- ещё жив. В отличие от токена сессии, здесь пространство перебора
-- шестизначное, поэтому хеш медленный (scrypt) — и сверх того есть счётчик
-- попыток: пять промахов гасят код, а не замедляют перебор.
-- ---------------------------------------------------------------------------
CREATE TABLE auth_code (
  id          uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id   uuid NOT NULL REFERENCES tenant(id)   ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,

  code_hash   text NOT NULL,
  expires_at  timestamptz NOT NULL,
  -- Счётчик промахов по ЭТОМУ коду. Ограничение попыток обязано жить рядом
  -- с самим кодом: лимит «на телефон» обходится запросом нового кода,
  -- а лимит на код — нет.
  attempts    smallint NOT NULL DEFAULT 0,
  -- Код одноразовый: использованный не подойдёт второй раз, даже пока не истёк.
  consumed_at timestamptz,
  ip          inet,
  created_at  timestamptz NOT NULL DEFAULT now(),

  CHECK (expires_at > created_at)
);

-- Проверка кода ищет последний живой код пользователя.
CREATE INDEX auth_code_live ON auth_code (tenant_id, user_id, created_at DESC);

ALTER TABLE auth_code ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_code FORCE ROW LEVEL SECURITY;
-- Здесь политика возможна и нужна: к моменту работы с кодом школа уже названа
-- (её слаг приходит в запросе входа), поэтому app.tenant_id выставлен.
CREATE POLICY tenant_isolation ON auth_code
  USING (tenant_id = current_tenant())
  WITH CHECK (tenant_id = current_tenant());

-- ---------------------------------------------------------------------------
-- Попытки входа — сырьё для ограничения перебора.
--
-- Отдельная таблица, а не счётчик в app_user: блокировать надо не учётную
-- запись, а того, кто ломится. Счётчик в учётной записи означал бы, что
-- посторонний может заблокировать вход владельцу школы, просто перебирая
-- его телефон, — то есть отказ в обслуживании по цене одного скрипта.
--
-- Хранится и удача, и промах: «в 3:40 ночи вошли с незнакомого адреса» —
-- это то, ради чего такую таблицу вообще заводят.
-- ---------------------------------------------------------------------------
CREATE TABLE auth_attempt (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('code_request','code_verify','password')),
  -- Логин ровно в том виде, в каком он приведён приложением (телефон в E.164).
  -- Учётной записи может не существовать вовсе — считать попытки надо и тогда,
  -- иначе перебор телефонов не ограничен ничем.
  subject    text NOT NULL,
  ip         inet,
  ok         boolean NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX auth_attempt_subject ON auth_attempt (tenant_id, kind, subject, created_at DESC);
CREATE INDEX auth_attempt_ip      ON auth_attempt (tenant_id, kind, ip, created_at DESC);

ALTER TABLE auth_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON auth_attempt
  USING (tenant_id = current_tenant())
  WITH CHECK (tenant_id = current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE ON auth_code, auth_attempt TO rockcrm_app;
GRANT USAGE, SELECT ON SEQUENCE auth_attempt_id_seq TO rockcrm_app;
