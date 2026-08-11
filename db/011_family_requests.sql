-- ============================================================================
-- RockCRM · 011 · Заявки из кабинета родителя
--
-- Родитель не двигает расписание сам: слот может быть занят, преподаватель
-- может быть занят, а перенос одного занятия иногда тянет за собой второе.
-- Поэтому из кабинета уходит заявка, а решение принимает администратор.
--
-- Таблица `task` для этого не годится: у неё нет ссылки на занятие, нет
-- желаемого времени и нет исхода рассмотрения — а без исхода нельзя ответить
-- родителю, почему перенос не состоялся.
-- ============================================================================

CREATE TABLE family_request (
  id           uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id    uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

  kind         text NOT NULL CHECK (kind IN ('reschedule', 'renew')),

  -- Кто просит. Не обязательно родитель: взрослый ученик платит за себя сам
  -- и обращается от своего имени.
  requested_by uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  student_id   uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  -- Занятие есть только у переноса; у продления его нет.
  lesson_id    uuid REFERENCES lesson(id) ON DELETE CASCADE,
  CONSTRAINT family_request_target_ck CHECK (
    (kind = 'reschedule' AND lesson_id IS NOT NULL) OR
    (kind = 'renew'      AND lesson_id IS NULL)
  ),

  reason       text,
  -- Удобные родителю варианты времени. Это пожелание, а не бронь:
  -- слот к моменту рассмотрения может быть занят.
  preferred    timestamptz[] NOT NULL DEFAULT '{}',

  status       text NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'accepted', 'declined')),
  -- Ответ администратора родителю. Отказ без объяснения хуже отказа:
  -- родитель всё равно позвонит, только уже раздражённым.
  answer       text,
  answered_by  uuid REFERENCES app_user(id) ON DELETE SET NULL,
  answered_at  timestamptz,
  -- Чем закончился перенос: на какое занятие переставили.
  moved_to     uuid REFERENCES lesson(id) ON DELETE SET NULL,

  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Две открытые заявки на одно занятие — почти всегда двойное нажатие,
-- а не второе намерение.
CREATE UNIQUE INDEX family_request_one_open
  ON family_request (lesson_id)
  WHERE kind = 'reschedule' AND status = 'pending' AND lesson_id IS NOT NULL;

-- Очередь администратора: что ещё не рассмотрено, старое сверху.
CREATE INDEX family_request_pending
  ON family_request (tenant_id, created_at) WHERE status = 'pending';
-- История в кабинете родителя.
CREATE INDEX family_request_student
  ON family_request (student_id, created_at DESC);

ALTER TABLE family_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE family_request FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON family_request
  USING (tenant_id = current_tenant())
  WITH CHECK (tenant_id = current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE ON family_request TO rockcrm_app;

COMMENT ON TABLE family_request IS
  'Заявки из кабинета родителя: перенос занятия и продление абонемента.';
