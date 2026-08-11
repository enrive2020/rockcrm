-- ============================================================================
-- RockCRM · 002 · Расписание: серии, занятия, посещаемость
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Серия — правило «каждый вторник в 18:00 у Шарапова в Барабанной A».
-- Из неё материализуются конкретные занятия на горизонт вперёд.
-- ---------------------------------------------------------------------------
CREATE TABLE lesson_series (
  id             uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id      uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  branch_id      uuid NOT NULL REFERENCES branch(id) ON DELETE RESTRICT,
  teacher_id     uuid NOT NULL REFERENCES staff(id) ON DELETE RESTRICT,
  room_id        uuid NOT NULL REFERENCES room(id) ON DELETE RESTRICT,
  discipline_id  uuid REFERENCES discipline(id) ON DELETE SET NULL,

  -- Занятие индивидуальное либо групповое — ровно одно из двух
  student_id     uuid REFERENCES student(id) ON DELETE CASCADE,
  group_id       uuid REFERENCES study_group(id) ON DELETE CASCADE,
  CONSTRAINT series_target_ck CHECK (num_nonnulls(student_id, group_id) = 1),

  -- Повторение. Сознательно не полный RRULE: школьное расписание — это
  -- «по вторникам и четвергам в 18:00», недельная сетка закрывает все
  -- реальные случаи. Полный RRULE усложнил бы генерацию без пользы.
  weekday        smallint NOT NULL CHECK (weekday BETWEEN 1 AND 7),  -- ISO: 1=Пн
  starts_at_time time NOT NULL,
  duration_min   smallint NOT NULL CHECK (duration_min IN (45, 55, 85)),
  every_n_weeks  smallint NOT NULL DEFAULT 1 CHECK (every_n_weeks > 0),

  valid_from     date NOT NULL,
  valid_until    date,                 -- NULL = бессрочно, пока не остановят
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT series_period_ck CHECK (valid_until IS NULL OR valid_until >= valid_from)
);

-- Предикат вида `valid_until >= current_date` здесь невозможен: функции
-- в условии частичного индекса обязаны быть IMMUTABLE, иначе индекс начал бы
-- расходиться с таблицей на следующий же день. Дату фильтрует запрос.
CREATE INDEX series_lookup ON lesson_series (tenant_id, teacher_id, weekday, valid_until);

-- ---------------------------------------------------------------------------
-- Занятие — материализованный экземпляр.
--
-- Почему материализуем, а не вычисляем из серии на лету: у занятия есть
-- собственное состояние — посещаемость, заметка, перенос, замена
-- преподавателя, начисленная зарплата. К вычисляемому событию это не привязать,
-- а история должна пережить любое изменение серии.
-- ---------------------------------------------------------------------------
CREATE TABLE lesson (
  id            uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id     uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  series_id     uuid REFERENCES lesson_series(id) ON DELETE SET NULL,  -- NULL = разовое
  branch_id     uuid NOT NULL REFERENCES branch(id) ON DELETE RESTRICT,
  teacher_id    uuid NOT NULL REFERENCES staff(id) ON DELETE RESTRICT,
  room_id       uuid NOT NULL REFERENCES room(id) ON DELETE RESTRICT,
  discipline_id uuid REFERENCES discipline(id) ON DELETE SET NULL,

  student_id    uuid REFERENCES student(id) ON DELETE CASCADE,
  group_id      uuid REFERENCES study_group(id) ON DELETE CASCADE,
  -- У пробного урока ещё нет ученика — есть лид. Ссылка появится в 004.
  lead_id       uuid,
  CONSTRAINT lesson_target_ck CHECK (num_nonnulls(student_id, group_id, lead_id) = 1),

  kind          text NOT NULL DEFAULT 'regular'
                CHECK (kind IN ('regular','trial','makeup','extra')),

  -- Время в UTC. Отображается в поясе филиала: школы в Алматы и Астане
  -- живут в разных поясах, а хранилище должно быть одно.
  starts_at     timestamptz NOT NULL,
  ends_at       timestamptz NOT NULL,
  CONSTRAINT lesson_time_ck CHECK (ends_at > starts_at),

  status        text NOT NULL DEFAULT 'planned'
                CHECK (status IN ('planned','held','cancelled')),

  -- Экземпляр отличается от своей серии (перенесён, заменён преподаватель).
  -- Регенерация расписания такие занятия не трогает.
  is_exception  boolean NOT NULL DEFAULT false,
  -- Осознанный овербукинг: администратор подтвердил, что ставит второе
  -- занятие в занятый кабинет. Снимает запрет ниже, но конфликт остаётся
  -- видимым в интерфейсе.
  overbook_ack  boolean NOT NULL DEFAULT false,

  cancelled_reason text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Двойная бронь запрещается базой, а не приложением.
--
-- Проверка «нет ли пересечения» в коде не спасает: два администратора,
-- сохраняющие занятие одновременно, оба увидят свободный слот и оба запишут.
-- Ограничение исключения снимает гонку целиком — второй COMMIT упадёт.
-- ---------------------------------------------------------------------------
ALTER TABLE lesson ADD CONSTRAINT lesson_room_no_overlap
  EXCLUDE USING gist (
    room_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  ) WHERE (status <> 'cancelled' AND NOT overbook_ack);

ALTER TABLE lesson ADD CONSTRAINT lesson_teacher_no_overlap
  EXCLUDE USING gist (
    teacher_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  ) WHERE (status <> 'cancelled' AND NOT overbook_ack);

-- Главный запрос интерфейса: занятия филиала за день
CREATE INDEX lesson_branch_day ON lesson (tenant_id, branch_id, starts_at)
  WHERE status <> 'cancelled';
CREATE INDEX lesson_teacher_day ON lesson (teacher_id, starts_at);
CREATE INDEX lesson_student ON lesson (student_id, starts_at DESC);

-- ---------------------------------------------------------------------------
-- Посещаемость. Отдельная строка на ученика: у группы из четырёх человек
-- один пришёл, другой прогулял, и абонемент каждого двигается по-своему.
-- ---------------------------------------------------------------------------
CREATE TABLE attendance (
  id          uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  lesson_id   uuid NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
  student_id  uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,

  mark        text NOT NULL CHECK (mark IN (
                'came',              -- пришёл
                'late',              -- опоздал
                'no_show',           -- прогул без предупреждения
                'cancelled_early',   -- отменил заранее, больше порога
                'cancelled_late',    -- отменил позже порога
                'cancelled_teacher'  -- отменил преподаватель или школа
              )),

  marked_by   uuid REFERENCES app_user(id) ON DELETE SET NULL,
  marked_at   timestamptz NOT NULL DEFAULT now(),
  -- Отмена ошибочной отметки: строка не удаляется, а гасится компенсирующими
  -- записями в журналах абонемента и зарплаты. См. spec.md §4.1.
  revoked_at  timestamptz,
  revoked_by  uuid REFERENCES app_user(id) ON DELETE SET NULL,

  UNIQUE (lesson_id, student_id)
);

CREATE INDEX attendance_student ON attendance (student_id, marked_at DESC)
  WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- Заметки к уроку и репертуар. То, чего нет ни в одной СНГ-CRM,
-- и то, ради чего в кабинет заходит родитель.
-- ---------------------------------------------------------------------------
CREATE TABLE lesson_note (
  id          uuid PRIMARY KEY DEFAULT uuid_v7(),
  tenant_id   uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  lesson_id   uuid NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
  student_id  uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  author_id   uuid REFERENCES staff(id) ON DELETE SET NULL,
  body        text NOT NULL,
  homework    text,
  -- Репертуар и техника: ['Nirvana — Smells Like Teen Spirit', 'Single Paradiddle']
  tags        text[] NOT NULL DEFAULT '{}',
  -- Виден ли родителю. Внутренние пометки преподавателя наружу не уходят.
  visible_to_family boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (lesson_id, student_id)
);

CREATE INDEX lesson_note_student ON lesson_note (student_id, created_at DESC);
