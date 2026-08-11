-- ============================================================================
-- RockCRM · 003 · Абонементы, платежи, зарплаты
-- Ядро продукта. Здесь ломаются существующие решения на рынке.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Тариф — то, что школа продаёт. «Барабаны, 2 раза в неделю, 55 минут».
-- ---------------------------------------------------------------------------
CREATE TABLE subscription_plan (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  name           text NOT NULL,
  discipline_id  uuid REFERENCES discipline(id) ON DELETE SET NULL,
  format         text NOT NULL DEFAULT 'individual'
                 CHECK (format IN ('individual','pair','group','trial')),
  duration_min   smallint NOT NULL CHECK (duration_min IN (45, 55, 85)),
  lessons_count  smallint NOT NULL CHECK (lessons_count > 0),
  valid_days     smallint NOT NULL DEFAULT 31 CHECK (valid_days > 0),
  price          numeric(12,2) NOT NULL CHECK (price >= 0),
  is_active      boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, name)
);

COMMENT ON COLUMN subscription_plan.valid_days IS
  'Срок жизни абонемента в днях. Месячный = 31, полугодовой = 184.';

-- ---------------------------------------------------------------------------
-- Абонемент — проданный экземпляр тарифа.
--
-- Остаток занятий НЕ является источником правды: он вычисляется из журнала
-- subscription_entry и лежит здесь только как кэш для быстрых списков.
-- Источник правды — журнал. См. spec.md §4.1.
-- ---------------------------------------------------------------------------
CREATE TABLE subscription (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  student_id     uuid NOT NULL REFERENCES student(id) ON DELETE RESTRICT,
  family_id      uuid REFERENCES family(id) ON DELETE SET NULL,
  plan_id        uuid REFERENCES subscription_plan(id) ON DELETE SET NULL,

  -- Копия тарифа на момент продажи: тариф могут переименовать или изменить
  -- цену, а проданный абонемент должен остаться таким, каким его купили.
  lessons_total  smallint NOT NULL CHECK (lessons_total > 0),
  price          numeric(12,2) NOT NULL CHECK (price >= 0),
  discount_pct   numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
  promo_code     text,

  -- Копия правил школы на момент продажи. Если школа завтра решит, что прогул
  -- больше не сгорает, это не должно пересчитать вчерашние абонементы:
  -- проданный абонемент — договор на условиях, действовавших при покупке.
  rules          jsonb NOT NULL,

  valid_from     date NOT NULL DEFAULT current_date,
  valid_until    date NOT NULL,
  CONSTRAINT subscription_period_ck CHECK (valid_until >= valid_from),

  status         text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','frozen','exhausted','expired','cancelled')),

  -- Кэш, пересчитывается триггером от журнала. Никогда не правится вручную.
  lessons_balance smallint NOT NULL DEFAULT 0,
  makeups_balance smallint NOT NULL DEFAULT 0,

  sold_by        uuid REFERENCES app_user(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX subscription_student ON subscription (student_id, valid_until DESC);
-- Для алерта «осталось мало» и для ночной проверки истечения
CREATE INDEX subscription_running_low ON subscription (tenant_id, lessons_balance)
  WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- Журнал абонемента — источник правды.
--
-- Каждое движение занятий и отработок пишется отдельной строкой и никогда
-- не изменяется. Ошибочная операция гасится компенсирующей записью,
-- а не правкой существующей: спор с родителем «куда делось занятие»
-- разрешается открытием журнала, а не догадками.
-- ---------------------------------------------------------------------------
CREATE TABLE subscription_entry (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  subscription_id uuid NOT NULL REFERENCES subscription(id) ON DELETE CASCADE,

  kind            text NOT NULL CHECK (kind IN (
                    'purchase',      -- продажа: +N занятий
                    'charge',        -- занятие проведено или прогул: −1
                    'refund',        -- отмена отметки: +1
                    'makeup_grant',  -- корректная отмена: +1 отработка
                    'makeup_use',    -- отработка использована: −1 отработка
                    'makeup_expire', -- отработка сгорела по сроку
                    'freeze',        -- занятие внутри заморозки, без списания
                    'adjust',        -- ручная корректировка администратором
                    'transfer_in',   -- перенос остатка с прошлого абонемента
                    'transfer_out',
                    'expire'         -- остаток сгорел по окончании срока
                  )),

  lessons_delta   smallint NOT NULL DEFAULT 0,
  makeups_delta   smallint NOT NULL DEFAULT 0,
  -- Пустая запись бессмысленна и обычно означает ошибку в коде — кроме
  -- заморозки: она баланс не двигает, но обязана остаться в истории.
  -- Без исключения вид 'freeze' был бы объявлен и неприменим, а администратор,
  -- отвечая на «куда делось занятие», не увидел бы двух недель каникул.
  CONSTRAINT entry_nonzero_ck CHECK (
    lessons_delta <> 0 OR makeups_delta <> 0 OR kind = 'freeze'
  ),

  -- За что списали. Позволяет показать в журнале дату и преподавателя.
  attendance_id   uuid REFERENCES attendance(id) ON DELETE SET NULL,
  lesson_id       uuid REFERENCES lesson(id) ON DELETE SET NULL,
  -- Компенсирующая запись ссылается на ту, которую гасит
  reverses_id     bigint REFERENCES subscription_entry(id) ON DELETE SET NULL,

  reason          text,
  created_by      uuid REFERENCES app_user(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX subscription_entry_sub ON subscription_entry (subscription_id, created_at);
-- Одно занятие не может списаться дважды: защита от двойного клика
-- и от повторной обработки запроса.
CREATE UNIQUE INDEX subscription_entry_once ON subscription_entry (attendance_id, kind)
  WHERE attendance_id IS NOT NULL AND kind IN ('charge','makeup_grant');

-- ---------------------------------------------------------------------------
-- Пересчёт кэша остатка.
--
-- Считаем сумму заново, а не «баланс + дельта»: полный пересчёт не умеет
-- разъезжаться с журналом, а на пятидесяти записях абонемента стоит
-- ничтожно мало. Инкремент был бы быстрее и однажды разошёлся бы.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION subscription_recalc() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  sub_id uuid := COALESCE(NEW.subscription_id, OLD.subscription_id);
BEGIN
  UPDATE subscription s SET
    lessons_balance = COALESCE(t.lessons, 0),
    makeups_balance = COALESCE(t.makeups, 0),
    status = CASE
      WHEN s.status IN ('cancelled','frozen') THEN s.status
      WHEN COALESCE(t.lessons, 0) <= 0 THEN 'exhausted'
      WHEN s.valid_until < current_date THEN 'expired'
      ELSE 'active'
    END
  FROM (
    SELECT sum(lessons_delta)::smallint AS lessons,
           sum(makeups_delta)::smallint AS makeups
    FROM subscription_entry WHERE subscription_id = sub_id
  ) t
  WHERE s.id = sub_id;
  RETURN NULL;
END $$;

CREATE TRIGGER subscription_entry_recalc
  AFTER INSERT OR UPDATE OR DELETE ON subscription_entry
  FOR EACH ROW EXECUTE FUNCTION subscription_recalc();

-- Журнал неизменяем: правка и удаление падают с ошибкой, а не проходят тихо.
-- Определение deny_journal_mutation() и аварийный люк — в 001_core.sql.
CREATE TRIGGER subscription_entry_immutable
  BEFORE UPDATE OR DELETE ON subscription_entry
  FOR EACH STATEMENT EXECUTE FUNCTION deny_journal_mutation();

-- ---------------------------------------------------------------------------
-- Заморозка. Сдвигает срок действия на число замороженных дней;
-- занятия не сгорают. Лимит дней в году берётся из rules абонемента.
-- ---------------------------------------------------------------------------
CREATE TABLE subscription_hold (
  id              uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id       uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  subscription_id uuid NOT NULL REFERENCES subscription(id) ON DELETE CASCADE,
  period          daterange NOT NULL,
  reason          text,
  created_by      uuid REFERENCES app_user(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  -- Две заморозки одного абонемента не могут перекрываться, иначе срок
  -- продлится дважды за одни и те же дни.
  EXCLUDE USING gist (subscription_id WITH =, period WITH &&)
);

-- ---------------------------------------------------------------------------
-- Отработки поштучно: у каждой свой срок жизни, иначе «сгорает через 30 дней»
-- невозможно применить — непонятно, с какой даты считать.
-- ---------------------------------------------------------------------------
CREATE TABLE makeup_credit (
  id               uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id        uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  subscription_id  uuid NOT NULL REFERENCES subscription(id) ON DELETE CASCADE,
  student_id       uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  granted_for      uuid REFERENCES lesson(id) ON DELETE SET NULL,  -- какое занятие отменили
  expires_on       date NOT NULL,
  used_at          timestamptz,
  used_for         uuid REFERENCES lesson(id) ON DELETE SET NULL,  -- какое занятие отработали
  expired_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT makeup_single_outcome_ck CHECK (used_at IS NULL OR expired_at IS NULL)
);

CREATE INDEX makeup_open ON makeup_credit (tenant_id, expires_on)
  WHERE used_at IS NULL AND expired_at IS NULL;

-- ---------------------------------------------------------------------------
-- Платежи
-- ---------------------------------------------------------------------------
CREATE TABLE payment (
  id              uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id       uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  family_id       uuid REFERENCES family(id) ON DELETE SET NULL,
  subscription_id uuid REFERENCES subscription(id) ON DELETE SET NULL,
  amount          numeric(12,2) NOT NULL CHECK (amount <> 0),  -- отрицательная = возврат
  method          text NOT NULL CHECK (method IN ('kaspi','card','cash','transfer','other')),
  status          text NOT NULL DEFAULT 'succeeded'
                  CHECK (status IN ('pending','succeeded','failed','refunded')),
  -- Идентификатор в платёжной системе. Уникален — защищает от задвоения
  -- при повторном вебхуке от провайдера.
  external_id     text,
  paid_at         timestamptz NOT NULL DEFAULT now(),
  accepted_by     uuid REFERENCES app_user(id) ON DELETE SET NULL,
  note            text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX payment_external_uniq ON payment (tenant_id, external_id)
  WHERE external_id IS NOT NULL;
CREATE INDEX payment_family ON payment (family_id, paid_at DESC);

-- ---------------------------------------------------------------------------
-- Ставки преподавателей.
--
-- Ставка зависит от формата и длительности: 85 минут дороже 55, группа
-- считается иначе, чем индивидуальное. Часть школ платит фиксировано,
-- часть — процентом от стоимости занятия; поддерживаем оба варианта.
-- ---------------------------------------------------------------------------
CREATE TABLE teacher_rate (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  staff_id      uuid NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  discipline_id uuid REFERENCES discipline(id) ON DELETE CASCADE,  -- NULL = любое
  format        text NOT NULL DEFAULT 'individual'
                CHECK (format IN ('individual','pair','group','trial')),
  duration_min  smallint CHECK (duration_min IN (45, 55, 85)),     -- NULL = любая
  amount        numeric(12,2) CHECK (amount >= 0),
  percent       numeric(5,2) CHECK (percent BETWEEN 0 AND 100),
  CONSTRAINT rate_one_kind_ck CHECK (num_nonnulls(amount, percent) = 1),
  valid_from    date NOT NULL DEFAULT current_date,
  valid_until   date
);

CREATE INDEX teacher_rate_lookup ON teacher_rate (staff_id, format, duration_min);

-- ---------------------------------------------------------------------------
-- Период начисления. Закрытый период не пересчитывается: правки уходят
-- корректировкой в следующий, иначе выплаченная зарплата задним числом
-- поменяется, и сверить её станет невозможно.
-- ---------------------------------------------------------------------------
CREATE TABLE payroll_period (
  id         uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id  uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  period     daterange NOT NULL,
  closed_at  timestamptz,
  closed_by  uuid REFERENCES app_user(id) ON DELETE SET NULL,
  EXCLUDE USING gist (tenant_id WITH =, period WITH &&)
);

CREATE TABLE payroll_entry (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  period_id     uuid REFERENCES payroll_period(id) ON DELETE SET NULL,
  staff_id      uuid NOT NULL REFERENCES staff(id) ON DELETE RESTRICT,
  lesson_id     uuid REFERENCES lesson(id) ON DELETE SET NULL,
  attendance_id uuid REFERENCES attendance(id) ON DELETE SET NULL,
  kind          text NOT NULL DEFAULT 'lesson'
                CHECK (kind IN ('lesson','bonus','correction','deduction')),
  amount        numeric(12,2) NOT NULL,
  -- Как посчитали: {"rate": 4500, "format": "individual", "duration": 55,
  -- "mark": "no_show", "pay_on_no_show": true}. Без снимка через полгода
  -- невозможно объяснить преподавателю, откуда взялась сумма.
  calc          jsonb NOT NULL DEFAULT '{}'::jsonb,
  reverses_id   bigint REFERENCES payroll_entry(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX payroll_entry_staff ON payroll_entry (staff_id, created_at DESC);
-- Одно занятие оплачивается один раз
CREATE UNIQUE INDEX payroll_entry_once ON payroll_entry (attendance_id)
  WHERE attendance_id IS NOT NULL AND kind = 'lesson';
