-- ============================================================================
-- RockCRM · 005 · Изоляция тенантов
--
-- Изоляция строится в базе, а не в приложении. Забытый `WHERE tenant_id = ...`
-- в одном из сотен запросов не должен показывать одной школе учеников другой:
-- цена такой ошибки — конец продукта, поэтому она не должна зависеть
-- от внимательности разработчика.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Роль приложения. Ключевой момент: она НЕ владелец таблиц и НЕ суперюзер —
-- иначе PostgreSQL пропустит её мимо политик.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rockcrm_app') THEN
    CREATE ROLE rockcrm_app LOGIN;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO rockcrm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rockcrm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rockcrm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rockcrm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO rockcrm_app;

-- ---------------------------------------------------------------------------
-- Таблицы с собственным tenant_id: политика проверяет его напрямую.
--
-- FORCE нужен, чтобы политика действовала и на владельца таблиц: без него
-- миграции и фоновые задачи, идущие под владельцем, видели бы всё.
-- WITH CHECK закрывает вторую половину задачи — запретить запись строки
-- в чужой тенант, а не только чтение.
-- ---------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'person','app_user','branch','room','discipline','staff','family',
    'student','study_group','lesson_series','lesson','attendance','lesson_note',
    'subscription_plan','subscription','subscription_hold','makeup_credit',
    'payment','teacher_rate','payroll_period','payroll_entry',
    'lead','lead_stage_history','notification','task'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I
         USING (tenant_id = current_tenant())
         WITH CHECK (tenant_id = current_tenant())', t);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Связующие таблицы без собственного tenant_id.
--
-- Их защищает подзапрос к родителю: он сам находится под политикой, поэтому
-- вернёт строку только своего тенанта. Дублировать tenant_id в связках
-- не стали — лишняя колонка, которую нужно поддерживать в согласии
-- с родителем, а рассогласование здесь означало бы дыру в изоляции.
-- ---------------------------------------------------------------------------
ALTER TABLE family_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE family_member FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON family_member
  USING (EXISTS (SELECT 1 FROM family f WHERE f.id = family_id))
  WITH CHECK (EXISTS (SELECT 1 FROM family f WHERE f.id = family_id));

ALTER TABLE group_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE group_member FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON group_member
  USING (EXISTS (SELECT 1 FROM study_group g WHERE g.id = group_id))
  WITH CHECK (EXISTS (SELECT 1 FROM study_group g WHERE g.id = group_id));

ALTER TABLE staff_discipline ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_discipline FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON staff_discipline
  USING (EXISTS (SELECT 1 FROM staff s WHERE s.id = staff_id))
  WITH CHECK (EXISTS (SELECT 1 FROM staff s WHERE s.id = staff_id));

ALTER TABLE staff_branch ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_branch FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON staff_branch
  USING (EXISTS (SELECT 1 FROM staff s WHERE s.id = staff_id))
  WITH CHECK (EXISTS (SELECT 1 FROM staff s WHERE s.id = staff_id));

-- ---------------------------------------------------------------------------
-- Журналы — только на добавление.
--
-- Политики есть на SELECT и INSERT; для UPDATE и DELETE политик нет, а при
-- включённом RLS отсутствие политики means запрет. Историю денег и баланса
-- нельзя переписать даже из приложения: ошибка гасится компенсирующей
-- записью, как в бухгалтерии.
-- ---------------------------------------------------------------------------
ALTER TABLE subscription_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON subscription_entry FOR SELECT
  USING (tenant_id = current_tenant());
CREATE POLICY tenant_append ON subscription_entry FOR INSERT
  WITH CHECK (tenant_id = current_tenant());

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON audit_log FOR SELECT
  USING (tenant_id = current_tenant());
CREATE POLICY tenant_append ON audit_log FOR INSERT
  WITH CHECK (tenant_id = current_tenant());

-- ---------------------------------------------------------------------------
-- Таблица тенантов: приложение читает её до того, как узнало тенанта
-- (например, чтобы найти школу по поддомену), поэтому политика по строке
-- здесь не годится. Ограничиваемся правами: читать можно, менять нельзя —
-- заведение и настройка школ идут отдельным административным каналом.
-- ---------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE ON tenant FROM rockcrm_app;
