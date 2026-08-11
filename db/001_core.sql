-- ============================================================================
-- RockCRM · 001 · Ядро: тенанты, изоляция, люди, филиалы
-- PostgreSQL 17+
-- ============================================================================

-- Единственная внешняя зависимость. Нужна для EXCLUDE по (uuid =, tstzrange &&):
-- обычный gist не умеет оператор равенства для uuid, а без него нельзя выразить
-- «в одном кабинете не могут пересекаться два занятия».
-- Входит в стандартный contrib, есть в официальном образе postgres.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- pgcrypto не нужен: gen_random_uuid() входит в ядро с PostgreSQL 13,
-- а пароли хеширует приложение.

-- ---------------------------------------------------------------------------
-- Идентификаторы: UUID v7
--
-- v4 случаен, поэтому вставка бьёт в случайные страницы B-дерева и раздувает
-- индексы. v7 начинается с таймстампа — новые строки ложатся в конец индекса,
-- как у bigserial, но при этом идентификатор безопасно отдавать наружу.
-- В PostgreSQL 18 есть встроенный uuidv7(); до перехода — своя реализация.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION uuid_v7() RETURNS uuid
LANGUAGE sql VOLATILE PARALLEL SAFE AS $$
  SELECT encode(
    set_bit(
      set_bit(
        overlay(
          uuid_send(gen_random_uuid())
          PLACING substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
          FROM 1 FOR 6
        ),
        52, 1),
      53, 1),
    'hex')::uuid;
$$;

-- ---------------------------------------------------------------------------
-- Текущий тенант берётся из параметра сессии, который приложение выставляет
-- сразу после выдачи соединения из пула:  SET LOCAL app.tenant_id = '...'
--
-- Второй аргумент true = не падать, если параметр не задан: тогда функция
-- вернёт NULL, политики RLS не пропустят ни одной строки, и забытый SET
-- превратится в пустую выборку, а не в утечку чужих данных.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.tenant_id', true), '')::uuid;
$$;

-- ---------------------------------------------------------------------------
-- Тенант — школа-клиент SaaS. Единственная таблица без tenant_id.
-- ---------------------------------------------------------------------------
CREATE TABLE tenant (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
  name          text NOT NULL,
  timezone      text NOT NULL DEFAULT 'Asia/Almaty',
  currency      char(3) NOT NULL DEFAULT 'KZT',
  locale        text NOT NULL DEFAULT 'ru',
  -- Правила школы по умолчанию. При продаже абонемента копируются в него,
  -- поэтому изменение здесь не пересчитывает уже проданное. См. spec.md §4.2.
  default_rules jsonb NOT NULL DEFAULT '{
    "no_show_burns": true,
    "cancel_notice_hours": 24,
    "cancel_early_effect": "makeup",
    "teacher_cancel_effect": "no_charge",
    "makeup_ttl_days": 30,
    "freeze_days_per_year": 14,
    "pay_teacher_on_no_show": true
  }'::jsonb,
  plan          text NOT NULL DEFAULT 'trial',
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tenant IS 'Школа-клиент. Граница изоляции всех данных.';

-- ---------------------------------------------------------------------------
-- Персона — человек. Отделена от ролей намеренно: взрослый ученик платит
-- сам за себя и одновременно может быть родителем другого ученика.
-- Хранить «ученика» и «родителя» отдельными сущностями с дублем контактов
-- означало бы два телефона одного человека и рассинхрон между ними.
-- ---------------------------------------------------------------------------
CREATE TABLE person (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  first_name     text NOT NULL,
  last_name      text,
  -- Телефон в E.164. Нормализуется приложением до записи: +7 (701) 555-24-18
  -- и 8 701 555 24 18 — один человек, иначе дубли в базе неизбежны.
  phone          text CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),
  email          text CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  birth_date     date,
  telegram_id    bigint,
  whatsapp_optin boolean NOT NULL DEFAULT false,
  -- Согласие на обработку ПДн. Для несовершеннолетних даёт представитель.
  pd_consent_at  timestamptz,
  note           text,
  archived_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Телефон уникален внутри школы, но только среди живых записей:
-- архивный ученик не мешает завести того же человека заново.
CREATE UNIQUE INDEX person_phone_uniq ON person (tenant_id, phone)
  WHERE phone IS NOT NULL AND archived_at IS NULL;
CREATE INDEX person_tenant_name ON person (tenant_id, last_name, first_name);

-- ---------------------------------------------------------------------------
-- Учётная запись — доступ в систему. Есть не у каждой персоны: у ребёнка
-- обычно нет входа, а у родителя есть.
-- ---------------------------------------------------------------------------
CREATE TABLE app_user (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  person_id     uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  login         text NOT NULL,          -- телефон или email
  password_hash text,                   -- NULL = вход только по одноразовому коду
  role          text NOT NULL CHECK (role IN ('owner','admin','teacher','guardian','student')),
  is_active     boolean NOT NULL DEFAULT true,
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, login)
);

-- ---------------------------------------------------------------------------
-- Филиалы и кабинеты
-- ---------------------------------------------------------------------------
CREATE TABLE branch (
  id          uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  name        text NOT NULL,
  address     text,
  timezone    text,      -- NULL = берём пояс тенанта; отличается для филиалов в других городах
  opens_at    time NOT NULL DEFAULT '10:00',
  closes_at   time NOT NULL DEFAULT '21:00',
  archived_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE room (
  id          uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  branch_id   uuid NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
  name        text NOT NULL,
  capacity    smallint NOT NULL DEFAULT 1 CHECK (capacity > 0),
  -- Характеристики кабинета: {"drum_kit": true, "soundproof": true, "piano": false}.
  -- Занятие на барабанах нельзя поставить в кабинет без установки — проверяется
  -- при постановке в расписание.
  features    jsonb NOT NULL DEFAULT '{}'::jsonb,
  archived_at timestamptz,
  UNIQUE (branch_id, name)
);

-- ---------------------------------------------------------------------------
-- Направления обучения
-- ---------------------------------------------------------------------------
CREATE TABLE discipline (
  id           uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  name         text NOT NULL,
  min_age      smallint,                -- барабаны с 5, скрипка с 7
  -- Требования к кабинету: {"drum_kit": true}. Сверяется с room.features.
  room_reqs    jsonb NOT NULL DEFAULT '{}'::jsonb,
  sort_order   smallint NOT NULL DEFAULT 0,
  archived_at  timestamptz,
  UNIQUE (tenant_id, name)
);

-- ---------------------------------------------------------------------------
-- Сотрудники
-- ---------------------------------------------------------------------------
CREATE TABLE staff (
  id           uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  person_id    uuid NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
  kind         text NOT NULL CHECK (kind IN ('teacher','admin','owner')),
  color        text CHECK (color ~ '^#[0-9a-fA-F]{6}$'),  -- цвет дорожки в расписании
  hired_on     date,
  archived_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, person_id, kind)
);

-- Кто что преподаёт — определяет, кого предлагать при постановке занятия
CREATE TABLE staff_discipline (
  staff_id      uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  discipline_id uuid NOT NULL REFERENCES discipline(id) ON DELETE CASCADE,
  PRIMARY KEY (staff_id, discipline_id)
);

-- В каких филиалах работает сотрудник — ограничивает и расписание, и права
CREATE TABLE staff_branch (
  staff_id  uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  branch_id uuid NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
  PRIMARY KEY (staff_id, branch_id)
);

-- ---------------------------------------------------------------------------
-- Семья — группа персон с общим плательщиком.
-- Нужна ради скидки за второго ребёнка, единого долга и кабинета родителя,
-- который видит сразу всех своих детей.
-- ---------------------------------------------------------------------------
CREATE TABLE family (
  id         uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id  uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  name       text,                     -- «Сагындык»; для взрослого ученика = его ФИО
  payer_id   uuid REFERENCES person(id) ON DELETE SET NULL,
  discount_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE family_member (
  family_id uuid NOT NULL REFERENCES family(id) ON DELETE CASCADE,
  person_id uuid NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  relation  text NOT NULL CHECK (relation IN ('payer','guardian','student')),
  PRIMARY KEY (family_id, person_id, relation)
);

-- ---------------------------------------------------------------------------
-- Ученик — учебный профиль персоны. Отдельная таблица, потому что у персоны
-- может не быть учебной роли (родитель-плательщик), а у ученика есть данные,
-- которых нет у остальных: уровень, дата старта, основной преподаватель.
-- ---------------------------------------------------------------------------
CREATE TABLE student (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  person_id      uuid NOT NULL REFERENCES person(id) ON DELETE RESTRICT,
  family_id      uuid REFERENCES family(id) ON DELETE SET NULL,
  branch_id      uuid REFERENCES branch(id) ON DELETE SET NULL,
  discipline_id  uuid REFERENCES discipline(id) ON DELETE SET NULL,
  main_teacher_id uuid REFERENCES staff(id) ON DELETE SET NULL,
  level          text,
  started_on     date NOT NULL DEFAULT current_date,
  -- Причина ухода заполняется при архивации и кормит отчёт об оттоке
  churn_reason   text,
  archived_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, person_id, discipline_id)
);

CREATE INDEX student_active ON student (tenant_id, branch_id) WHERE archived_at IS NULL;
CREATE INDEX student_family ON student (family_id) WHERE archived_at IS NULL;

-- ---------------------------------------------------------------------------
-- Групповые занятия
-- ---------------------------------------------------------------------------
CREATE TABLE study_group (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  name          text NOT NULL,
  discipline_id uuid REFERENCES discipline(id) ON DELETE SET NULL,
  max_size      smallint NOT NULL DEFAULT 6 CHECK (max_size > 1),
  archived_at   timestamptz
);

CREATE TABLE group_member (
  group_id   uuid NOT NULL REFERENCES study_group(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  joined_on  date NOT NULL DEFAULT current_date,
  left_on    date,
  PRIMARY KEY (group_id, student_id)
);

-- ---------------------------------------------------------------------------
-- Аудит. Пишется на всё, что двигает деньги или баланс абонемента.
-- Строки только добавляются: правки и удаления запрещены политикой ниже.
-- ---------------------------------------------------------------------------
-- ---------------------------------------------------------------------------
-- Запрет на изменение журнальных таблиц.
--
-- Одних политик RLS мало: без политики на UPDATE запрос не падает, а меняет
-- ноль строк. Приложение получает «успех» и спокойно идёт дальше с неверным
-- предположением. Явная ошибка превращает тихую порчу логики в громкий сбой.
--
-- Триггер обязан быть уровня ОПЕРАТОРА, а не строки: RLS отсекает строки
-- раньше, чем срабатывают строковые триггеры, поэтому под ролью приложения
-- строковый триггер не вызвался бы ни разу и запрет остался бы декларацией.
--
-- Аварийный люк — на случай удаления тенанта, когда каскад обязан пройти:
--   SET LOCAL app.allow_purge = 'on';
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION deny_journal_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF coalesce(current_setting('app.allow_purge', true), '') = 'on' THEN
    RETURN NULL;   -- для триггера уровня оператора возвращаемое значение игнорируется
  END IF;
  RAISE EXCEPTION 'Таблица % только на добавление: % запрещён', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'restrict_violation',
          HINT = 'Ошибочную запись гасят компенсирующей, а не правят задним числом';
END $$;

CREATE TABLE audit_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  actor_id    uuid REFERENCES app_user(id) ON DELETE SET NULL,
  action      text NOT NULL,           -- 'attendance.mark', 'subscription.adjust', ...
  entity      text NOT NULL,
  entity_id   uuid,
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_lookup ON audit_log (tenant_id, entity, entity_id, created_at DESC);

CREATE TRIGGER audit_log_immutable
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION deny_journal_mutation();
