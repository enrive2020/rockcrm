-- ============================================================================
-- RockCRM · 009 · Дата операции отделяется от системных часов
-- ADR-001 (docs/adr-001-operation-date.md) · задача #15
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Статус абонемента перестаёт зависеть от системных часов.
--
-- Триггер ставил `expired`, сравнивая valid_until с current_date. Из-за этого
-- простая вставка строки в журнал меняла статус соседней строки: абонемент,
-- проданный «в марте», объявлялся истёкшим в ту же секунду, следующая отметка
-- не находила, с чего списывать, и проходила мимо журнала. Так из 2900
-- отметок генератора истории 2500 не оставили следа — при зелёных проверках
-- сходимости, потому что ноль сходится с нулём.
--
-- Теперь триггер вычисляет статус ИЗ ФАКТОВ, не зная «сегодня». Перевод
-- в `expired` — работа ночной сверки sync_statuses(), которая уже написана
-- и уже вызывается по расписанию.
--
-- `expired` добавлен к сберегаемым статусам рядом с `cancelled` и `frozen`:
-- без этого списание задним числом с истёкшего абонемента вернуло бы ему
-- `active` — воскресило бы вставкой строки ровно так же неявно, как раньше
-- хоронило.
--
-- Чем платим: без запущенной сверки статусы отстают от календаря. Это уже
-- так после исправления заморозок, и это осознанный размен — сверку видно
-- и можно проверить, а неявный пересчёт при вставке не видно никак.
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
      WHEN s.status IN ('cancelled','frozen','expired') THEN s.status
      WHEN COALESCE(t.lessons, 0) <= 0 THEN 'exhausted'
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

COMMENT ON FUNCTION subscription_recalc() IS
  'Пересчёт остатка из журнала. Статус выводится из фактов и не зависит '
  'от системных часов: expired проставляет ночная сверка sync_statuses().';

-- ---------------------------------------------------------------------------
-- 2. Пометка «внесено задним числом».
--
-- Без неё восстановить картину при разборе спора невозможно: created_at
-- отвечает, когда появилась строка, но не отвечает, за какой день она внесена.
-- Администратор, объясняющий родителю журнал, обязан видеть разницу.
--
-- DEFAULT false, а не NULL: у всех записей до этой миграции дата операции
-- совпадала с днём записи по построению — другой возможности не было.
-- ---------------------------------------------------------------------------
ALTER TABLE subscription_entry
  ADD COLUMN IF NOT EXISTS backdated boolean NOT NULL DEFAULT false;
ALTER TABLE attendance
  ADD COLUMN IF NOT EXISTS backdated boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN subscription_entry.backdated IS
  'Операция внесена позже события: дата операции была раньше дня записи.';
COMMENT ON COLUMN attendance.backdated IS
  'Отметка внесена задним числом (ADR-001).';

-- Разбор «что внесли задним числом за месяц» — редкий, но обязательный
-- вопрос при сверке. Частичный: строк с пометкой единицы процентов.
CREATE INDEX IF NOT EXISTS subscription_entry_backdated
  ON subscription_entry (tenant_id, created_at DESC) WHERE backdated;
CREATE INDEX IF NOT EXISTS attendance_backdated
  ON attendance (tenant_id, marked_at DESC) WHERE backdated;

-- ---------------------------------------------------------------------------
-- 3. Правило школы `backdating_days`.
--
-- Окно правки: насколько глубоко в прошлое можно датировать операцию.
-- 30 дней — это «администратор болел две недели» плюс запас. Ноль означает
-- «только сегодня»: школа, которой ввод задним числом не нужен, отключает
-- возможность целиком.
--
-- Правило живёт в настройках школы и НЕ копируется в абонемент, в отличие
-- от остальных: это регламент работы администратора, а не условие договора
-- с родителем. Школа, ужесточившая окно сегодня, ужесточила его и для
-- вчерашних абонементов — и это ровно то, чего она хотела.
-- ---------------------------------------------------------------------------
ALTER TABLE tenant ALTER COLUMN default_rules SET DEFAULT '{
  "no_show_burns": true,
  "cancel_notice_hours": 24,
  "cancel_early_effect": "makeup",
  "teacher_cancel_effect": "makeup",
  "makeup_ttl_days": 30,
  "freeze_days_per_year": 14,
  "pay_teacher_on_no_show": true,
  "carry_over_lessons": 0,
  "allow_overlapping_subscriptions": false,
  "backdating_days": 30
}'::jsonb;

UPDATE tenant
   SET default_rules = default_rules || '{"backdating_days": 30}'::jsonb
 WHERE NOT (default_rules ? 'backdating_days');
