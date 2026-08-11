-- ============================================================================
-- RockCRM · 004 · Заявки, воронка, уведомления
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Заявка. Приходит из Telegram-бота, формы сайта, WhatsApp или заводится
-- вручную. До покупки абонемента человека ещё нет в ученической базе —
-- иначе она забьётся теми, кто не дошёл до занятий.
-- ---------------------------------------------------------------------------
CREATE TABLE lead (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

  name           text NOT NULL,
  phone          text CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),
  student_name   text,          -- когда заявку оставляет родитель за ребёнка
  student_age    smallint CHECK (student_age BETWEEN 3 AND 99),
  discipline_id  uuid REFERENCES discipline(id) ON DELETE SET NULL,
  branch_id      uuid REFERENCES branch(id) ON DELETE SET NULL,

  stage          text NOT NULL DEFAULT 'new' CHECK (stage IN (
                   'new',            -- новая
                   'contacting',     -- дозвон
                   'trial_booked',   -- пробный назначен
                   'trial_held',     -- пробный проведён
                   'won',            -- абонемент куплен
                   'lost'            -- отказ
                 )),
  -- Причина отказа обязательна: без неё воронка не показывает, что чинить.
  lost_reason    text CHECK (lost_reason IN (
                   'price','location','schedule','competitor',
                   'no_answer','not_ready','other'
                 )),
  CONSTRAINT lead_lost_reason_ck CHECK (stage <> 'lost' OR lost_reason IS NOT NULL),

  source         text NOT NULL DEFAULT 'other' CHECK (source IN (
                   'telegram_bot','site_form','whatsapp','instagram',
                   'referral','walk_in','phone','other'
                 )),
  utm            jsonb NOT NULL DEFAULT '{}'::jsonb,
  promo_code     text,
  -- Идентификатор заявки в LeadHub. Уникален: повторная доставка того же
  -- лида при ретрае не создаёт дубль.
  external_id    text,

  assigned_to    uuid REFERENCES app_user(id) ON DELETE SET NULL,
  next_action_at timestamptz,          -- «перезвонить в 18:00»
  contact_attempts smallint NOT NULL DEFAULT 0,

  -- Что получилось из заявки. Заполняется при конверсии.
  person_id      uuid REFERENCES person(id) ON DELETE SET NULL,
  student_id     uuid REFERENCES student(id) ON DELETE SET NULL,

  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX lead_external_uniq ON lead (tenant_id, external_id)
  WHERE external_id IS NOT NULL;
CREATE INDEX lead_board ON lead (tenant_id, stage, created_at DESC);
CREATE INDEX lead_followup ON lead (tenant_id, next_action_at)
  WHERE stage NOT IN ('won','lost');

-- Пробный урок ссылается на заявку, а не на ученика
ALTER TABLE lesson ADD CONSTRAINT lesson_lead_fk
  FOREIGN KEY (lead_id) REFERENCES lead(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- История стадий. Без неё нельзя посчитать ни конверсию между этапами,
-- ни сколько дней заявка провисела на дозвоне.
-- ---------------------------------------------------------------------------
CREATE TABLE lead_stage_history (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  lead_id     uuid NOT NULL REFERENCES lead(id) ON DELETE CASCADE,
  from_stage  text,
  to_stage    text NOT NULL,
  changed_by  uuid REFERENCES app_user(id) ON DELETE SET NULL,
  changed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX lead_stage_history_lead ON lead_stage_history (lead_id, changed_at);

-- ---------------------------------------------------------------------------
-- Исходящие уведомления — очередь, а не прямой вызов провайдера.
--
-- Та же логика, что в LeadHub: сообщение сначала ложится на диск в одной
-- транзакции с бизнес-операцией, и только потом отдельный воркер идёт
-- в WhatsApp. Если провайдер лежит, напоминание не теряется, а ждёт;
-- если транзакция откатилась, сообщение не уйдёт вообще.
-- ---------------------------------------------------------------------------
CREATE TABLE notification (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  person_id     uuid REFERENCES person(id) ON DELETE CASCADE,

  channel       text NOT NULL CHECK (channel IN ('whatsapp','telegram','sms','email')),
  template      text NOT NULL,        -- 'lesson_reminder', 'subscription_low', ...
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  to_address    text NOT NULL,

  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','sending','sent','failed','cancelled')),
  send_after    timestamptz NOT NULL DEFAULT now(),
  attempts      smallint NOT NULL DEFAULT 0,
  last_error    text,
  sent_at       timestamptz,

  -- Ключ идемпотентности: 'lesson_reminder:<lesson_id>'. Не даёт отправить
  -- одно напоминание дважды, сколько бы раз ни отработал планировщик.
  dedup_key     text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX notification_dedup ON notification (tenant_id, dedup_key)
  WHERE dedup_key IS NOT NULL;
-- Выборка воркера: что пора слать
CREATE INDEX notification_due ON notification (send_after)
  WHERE status = 'queued';

-- ---------------------------------------------------------------------------
-- Задачи администратору: «продать продление», «перезвонить», «долг».
-- Порождаются автоматически и закрываются человеком.
-- ---------------------------------------------------------------------------
CREATE TABLE task (
  id           uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN (
                 'renew_subscription','collect_debt','call_lead',
                 'makeup_expiring','churn_risk','custom'
               )),
  title        text NOT NULL,
  assigned_to  uuid REFERENCES app_user(id) ON DELETE SET NULL,
  student_id   uuid REFERENCES student(id) ON DELETE CASCADE,
  lead_id      uuid REFERENCES lead(id) ON DELETE CASCADE,
  due_at       timestamptz,
  done_at      timestamptz,
  done_by      uuid REFERENCES app_user(id) ON DELETE SET NULL,
  dedup_key    text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX task_dedup ON task (tenant_id, dedup_key)
  WHERE dedup_key IS NOT NULL AND done_at IS NULL;
CREATE INDEX task_open ON task (tenant_id, assigned_to, due_at) WHERE done_at IS NULL;
