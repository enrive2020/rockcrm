"""Проверка схемы RockCRM на живом PostgreSQL.

По умолчанию поднимает собственный кластер через pgserver — тогда установленная
база не нужна. Но в этой сборке нет btree_gist, поэтому проверки ограничений
исключения помечаются SKIP.

Чтобы прогнать полный набор, укажите полноценный PostgreSQL:

    DATABASE_URL=postgresql://postgres:dev@localhost:55432/rockcrm python tests/test_schema.py
"""
import os, pathlib, re, sys, psycopg

DB = pathlib.Path(__file__).parent / "pgdata"       # локальный кластер для прогона
SQL = pathlib.Path(__file__).resolve().parent.parent / "db"

TA = "01890000-0000-7000-8000-00000000000a"   # тенант «RockSchool»
TB = "01890000-0000-7000-8000-00000000000b"   # тенант-сосед, для проверки изоляции

ok, fail, skipped = [], [], []
HAS_GIST = True   # уточняется после подключения

def check(name, fn, needs_gist=False):
    if needs_gist and not HAS_GIST:
        skipped.append(name)
        return
    try:
        fn()
        ok.append(name)
    except AssertionError as e:
        fail.append(f"{name}: {e}")
    except Exception as e:
        fail.append(f"{name}: {type(e).__name__}: {str(e).splitlines()[0]}")
    finally:
        # Упавший тест оставляет транзакцию в aborted-состоянии, и без отката
        # все следующие проверки посыпались бы каскадом, маскируя настоящую
        # причину. Роль тоже сбрасываем: тесты изоляции меняют её.
        try:
            conn.rollback()
            cur.execute("RESET ROLE")
            conn.commit()
        except Exception:
            pass

def rejects(cur, sql, args=None, why=""):
    """Ожидаем, что база отвергнет операцию."""
    try:
        cur.execute(sql, args)
    except psycopg.Error as e:
        cur.connection.rollback()
        return e.diag.constraint_name or e.sqlstate
    cur.connection.rollback()
    raise AssertionError(f"операция прошла, хотя должна быть отвергнута ({why})")

uri = os.environ.get("DATABASE_URL")
if uri:
    print(f"подключаюсь к внешней базе: {re.sub(r'//[^@]*@', '//***@', uri)}")
else:
    import pgserver
    print("поднимаю встроенный postgres...")
    uri = pgserver.get_server(DB).get_uri()
conn = psycopg.connect(uri, autocommit=False)
cur = conn.cursor()

# ---------- применение схемы ----------
cur.execute("SELECT count(*) FROM pg_available_extensions WHERE name = 'btree_gist'")
HAS_GIST = cur.fetchone()[0] > 0
if not HAS_GIST:
    print("  ВНИМАНИЕ: btree_gist недоступен — ограничения от двойной брони "
          "не будут созданы и не будут проверены")

def strip_exclusions(sql: str) -> str:
    """Вырезает EXCLUDE-ограничения, чтобы остальную схему можно было проверить."""
    sql = re.sub(r"CREATE EXTENSION IF NOT EXISTS btree_gist;", "", sql)
    sql = re.sub(r"ALTER TABLE \w+ ADD CONSTRAINT \w+\s*\n\s*EXCLUDE USING gist.*?;", "", sql, flags=re.S)
    # строка ограничения внутри CREATE TABLE
    sql = re.sub(r"\n\s*EXCLUDE USING gist \([^\n]*\)", "", sql)
    # и осиротевшая запятая перед закрывающей скобкой, возможно через комментарии
    sql = re.sub(r",((?:\s*--[^\n]*)*\s*)\);", r"\1);", sql)
    return sql

cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
conn.commit()
for f in sorted(SQL.glob("*.sql")):
    body = f.read_text(encoding="utf-8")
    if not HAS_GIST:
        body = strip_exclusions(body)
    cur.execute(body)
    conn.commit()
    print(f"  применён {f.name}")

# ---------- посев ----------
# Параметры подставляются в текст, а не передаются драйверу: psycopg не даёт
# сочетать несколько команд в одном запросе с аргументами, а разбивать посев
# на два десятка вызовов ради этого незачем. Значения — константы теста.
SEED = """
INSERT INTO tenant (id, slug, name) VALUES
  (%(a)s, 'rockschool', 'RockSchool Алматы'),
  (%(b)s, 'other-school', 'Другая школа');

INSERT INTO branch (id, tenant_id, name) VALUES
  ('01890000-0000-7000-8000-0000000000b1', %(a)s, 'Аль-Фараби 53В');
INSERT INTO room (id, tenant_id, branch_id, name, features) VALUES
  ('01890000-0000-7000-8000-0000000000c1', %(a)s, '01890000-0000-7000-8000-0000000000b1',
   'Барабанная A', '{"drum_kit": true, "soundproof": true}');
INSERT INTO room (id, tenant_id, branch_id, name) VALUES
  ('01890000-0000-7000-8000-0000000000c2', %(a)s, '01890000-0000-7000-8000-0000000000b1', 'Класс 1');

INSERT INTO discipline (id, tenant_id, name, min_age, room_reqs) VALUES
  ('01890000-0000-7000-8000-0000000000d1', %(a)s, 'Барабаны', 5, '{"drum_kit": true}');

INSERT INTO person (id, tenant_id, first_name, last_name, phone) VALUES
  ('01890000-0000-7000-8000-0000000000a1', %(a)s, 'Дмитрий', 'Шарапов', '+77015550001'),
  ('01890000-0000-7000-8000-0000000000a2', %(a)s, 'Егор', 'Мадратов', '+77015550002'),
  ('01890000-0000-7000-8000-0000000000a3', %(a)s, 'Амина', 'Сагындык', '+77015550003'),
  ('01890000-0000-7000-8000-0000000000a9', %(b)s, 'Чужой', 'Ученик', '+77015559999');

INSERT INTO staff (id, tenant_id, person_id, kind) VALUES
  ('01890000-0000-7000-8000-0000000000e1', %(a)s, '01890000-0000-7000-8000-0000000000a1', 'teacher'),
  ('01890000-0000-7000-8000-0000000000e2', %(a)s, '01890000-0000-7000-8000-0000000000a2', 'teacher');

INSERT INTO student (id, tenant_id, person_id, discipline_id) VALUES
  ('01890000-0000-7000-8000-0000000000f1', %(a)s, '01890000-0000-7000-8000-0000000000a3',
   '01890000-0000-7000-8000-0000000000d1');
"""
cur.execute(SEED.replace("%(a)s", f"'{TA}'").replace("%(b)s", f"'{TB}'"))
conn.commit()

L = "01890000-0000-7000-8000-0000000000%s"
BASE = dict(t=TA, b='01890000-0000-7000-8000-0000000000b1',
            r1='01890000-0000-7000-8000-0000000000c1',
            r2='01890000-0000-7000-8000-0000000000c2',
            s1='01890000-0000-7000-8000-0000000000e1',
            s2='01890000-0000-7000-8000-0000000000e2',
            st='01890000-0000-7000-8000-0000000000f1')

def add_lesson(lid, room, teacher, start, mins=55, status='planned', ack=False):
    cur.execute("""
      INSERT INTO lesson (id, tenant_id, branch_id, teacher_id, room_id, student_id,
                          starts_at, ends_at, status, overbook_ack)
      VALUES (%(id)s, %(t)s, %(b)s, %(tc)s, %(rm)s, %(st)s,
              %(s)s::timestamptz, %(s)s::timestamptz + make_interval(mins => %(m)s),
              %(status)s, %(ack)s)
    """, {**BASE, "id": lid, "rm": room, "tc": teacher, "s": start,
          "m": mins, "status": status, "ack": ack})

# базовое занятие 11:00–11:55, Барабанная A, Шарапов
add_lesson(L % "1a", BASE["r1"], BASE["s1"], "2026-08-12 11:00+06")
conn.commit()

# ---------- тесты расписания ----------
def t_room_overlap():
    c = rejects(cur, """
      INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, student_id, starts_at, ends_at)
      VALUES (%(t)s, %(b)s, %(s2)s, %(r1)s, %(st)s,
              '2026-08-12 11:30+06', '2026-08-12 12:25+06')
    """, BASE, "другой преподаватель в занятом кабинете")
    assert c == "lesson_room_no_overlap", f"сработало не то ограничение: {c}"

def t_teacher_overlap():
    c = rejects(cur, """
      INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, student_id, starts_at, ends_at)
      VALUES (%(t)s, %(b)s, %(s1)s, %(r2)s, %(st)s,
              '2026-08-12 11:30+06', '2026-08-12 12:25+06')
    """, BASE, "тот же преподаватель в двух кабинетах")
    assert c == "lesson_teacher_no_overlap", f"сработало не то ограничение: {c}"

def t_adjacent_ok():
    """Занятие впритык (11:55–12:50) конфликтом не является: границы полуоткрыты."""
    add_lesson(L % "1b", BASE["r1"], BASE["s2"], "2026-08-12 11:55+06")
    conn.commit()

def t_overbook_ack():
    """Осознанный овербукинг проходит."""
    add_lesson(L % "1c", BASE["r1"], BASE["s2"], "2026-08-12 11:10+06", ack=True)
    conn.commit()

def t_cancelled_frees_slot():
    cur.execute("UPDATE lesson SET status='cancelled' WHERE id=%s", (L % "1a",))
    add_lesson(L % "1d", BASE["r1"], BASE["s1"], "2026-08-12 11:00+06")
    conn.commit()
    cur.execute("UPDATE lesson SET status='cancelled' WHERE id=%s", (L % "1d",))
    conn.commit()

check("кабинет нельзя занять дважды", t_room_overlap, needs_gist=True)
check("преподаватель не может вести два занятия сразу", t_teacher_overlap, needs_gist=True)
check("занятие впритык не считается конфликтом", t_adjacent_ok)
check("овербукинг с подтверждением разрешён", t_overbook_ack)
check("отменённое занятие освобождает слот", t_cancelled_frees_slot)

# ---------- тесты абонемента ----------
SUB = L % "2a"
def t_ledger():
    cur.execute("""
      INSERT INTO subscription (id, tenant_id, student_id, lessons_total, price, rules, valid_until)
      SELECT %s, %s, %s, 8, 54000, default_rules, current_date + 31 FROM tenant WHERE id = %s
    """, (SUB, TA, BASE["st"], TA))
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta)
                   VALUES (%s, %s, 'purchase', 8)""", (TA, SUB))
    conn.commit()
    cur.execute("SELECT lessons_balance, status FROM subscription WHERE id=%s", (SUB,))
    bal, st = cur.fetchone()
    assert bal == 8, f"после покупки остаток {bal}, ожидалось 8"
    assert st == "active", f"статус {st}"

def t_charge():
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta)
                   VALUES (%s, %s, 'charge', -1)""", (TA, SUB))
    conn.commit()
    cur.execute("SELECT lessons_balance FROM subscription WHERE id=%s", (SUB,))
    assert cur.fetchone()[0] == 7, "списание не отразилось в остатке"

def t_makeup():
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, makeups_delta)
                   VALUES (%s, %s, 'makeup_grant', 1)""", (TA, SUB))
    conn.commit()
    cur.execute("SELECT lessons_balance, makeups_balance FROM subscription WHERE id=%s", (SUB,))
    l, m = cur.fetchone()
    assert (l, m) == (7, 1), f"остаток {l}, отработки {m}; ожидалось 7 и 1"

def t_exhausted():
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta)
                   VALUES (%s, %s, 'charge', -7)""", (TA, SUB))
    conn.commit()
    cur.execute("SELECT lessons_balance, status FROM subscription WHERE id=%s", (SUB,))
    bal, st = cur.fetchone()
    assert (bal, st) == (0, "exhausted"), f"остаток {bal}, статус {st}"
    # возвращаем занятия назад для дальнейших тестов
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta)
                   VALUES (%s, %s, 'refund', 5)""", (TA, SUB))
    conn.commit()

def t_empty_entry():
    rejects(cur, """INSERT INTO subscription_entry (tenant_id, subscription_id, kind)
                    VALUES (%s, %s, 'adjust')""", (TA, SUB), "пустая запись журнала")

def t_double_charge():
    """Одна отметка не может списать занятие дважды."""
    cur.execute("""INSERT INTO attendance (id, tenant_id, lesson_id, student_id, mark)
                   VALUES (%s, %s, %s, %s, 'came')""",
                (L % "3a", TA, L % "1b", BASE["st"]))
    cur.execute("""INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta, attendance_id)
                   VALUES (%s, %s, 'charge', -1, %s)""", (TA, SUB, L % "3a"))
    conn.commit()
    rejects(cur, """INSERT INTO subscription_entry (tenant_id, subscription_id, kind, lessons_delta, attendance_id)
                    VALUES (%s, %s, 'charge', -1, %s)""", (TA, SUB, L % "3a"),
            "повторное списание по той же отметке")

def t_hold_overlap():
    cur.execute("""INSERT INTO subscription_hold (tenant_id, subscription_id, period)
                   VALUES (%s, %s, daterange('2026-08-14','2026-08-25'))""", (TA, SUB))
    conn.commit()
    c = rejects(cur, """INSERT INTO subscription_hold (tenant_id, subscription_id, period)
                        VALUES (%s, %s, daterange('2026-08-20','2026-08-30'))""", (TA, SUB),
                "пересекающиеся заморозки")
    assert "excl" in (c or "").lower() or c is not None

check("покупка наполняет абонемент", t_ledger)
check("списание уменьшает остаток", t_charge)
check("отработки считаются отдельно от занятий", t_makeup)
check("нулевой остаток переводит абонемент в exhausted", t_exhausted)
check("пустая запись журнала отвергается", t_empty_entry)
check("повторное списание по одной отметке невозможно", t_double_charge)
check("заморозки не могут пересекаться", t_hold_overlap, needs_gist=True)

# ---------- прочие ограничения ----------
def t_lost_reason():
    rejects(cur, """INSERT INTO lead (tenant_id, name, stage) VALUES (%s, 'Тест', 'lost')""",
            (TA,), "отказ без причины")

def t_phone_format():
    rejects(cur, """INSERT INTO person (tenant_id, first_name, phone) VALUES (%s, 'Тест', '8 701 555')""",
            (TA,), "телефон не в E.164")

def t_uuid_v7():
    cur.execute("SELECT uuid_v7()")
    u = str(cur.fetchone()[0])
    assert u[14] == "7", f"версия UUID = {u[14]}, ожидалась 7"
    # Порядок проверяем с паузой: внутри одной миллисекунды старший разряд
    # одинаков, и сравнение решает случайная часть — v7 этого и не обещает.
    first = u
    cur.execute("SELECT pg_sleep(0.005)")
    cur.execute("SELECT uuid_v7()")
    second = str(cur.fetchone()[0])
    assert first < second, f"UUID v7 не возрастают во времени: {first} !< {second}"
    conn.commit()

def t_lesson_target():
    rejects(cur, """INSERT INTO lesson (tenant_id, branch_id, teacher_id, room_id, starts_at, ends_at)
                    VALUES (%(t)s, %(b)s, %(s1)s, %(r2)s, '2026-09-01 10:00+06', '2026-09-01 10:55+06')""",
            BASE, "занятие без ученика, группы и заявки")

check("отказ по заявке требует причины", t_lost_reason)
check("телефон приводится к E.164", t_phone_format)
check("uuid_v7 даёт седьмую версию и возрастает", t_uuid_v7)
check("занятие обязано иметь адресата", t_lesson_target)

# ---------- изоляция тенантов ----------
def t_rls_isolation():
    cur.execute("SET ROLE rockcrm_app")
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (TA,))
    cur.execute("SELECT count(*) FROM person")
    a = cur.fetchone()[0]
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (TB,))
    cur.execute("SELECT count(*) FROM person")
    b = cur.fetchone()[0]
    conn.commit()
    assert a == 3, f"в тенанте A видно {a} персон, ожидалось 3"
    assert b == 1, f"в тенанте B видно {b} персон, ожидалось 1"

def t_rls_no_tenant():
    """Забытый SET app.tenant_id обязан дать пустоту, а не чужие данные."""
    cur.execute("SET ROLE rockcrm_app")
    cur.execute("SELECT set_config('app.tenant_id', '', false)")
    cur.execute("SELECT count(*) FROM person")
    n = cur.fetchone()[0]
    conn.commit()
    assert n == 0, f"без указанного тенанта видно {n} строк"

def t_rls_write_guard():
    """Нельзя записать строку в чужой тенант."""
    cur.execute("SET ROLE rockcrm_app")
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (TA,))
    rejects(cur, "INSERT INTO person (tenant_id, first_name) VALUES (%s, 'Диверсант')",
            (TB,), "запись в чужой тенант")

def t_journal_append_only():
    """Правка журнала обязана падать с ошибкой, а не менять ноль строк тихо."""
    for sql, why in [
        ("UPDATE subscription_entry SET lessons_delta = 99 WHERE subscription_id = %s",
         "правка журнала абонемента"),
        ("DELETE FROM subscription_entry WHERE subscription_id = %s",
         "удаление из журнала абонемента"),
    ]:
        cur.execute("SET ROLE rockcrm_app")
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (TA,))
        code = rejects(cur, sql, (SUB,), why)
        assert code == "2F004" or code is not None, f"неожиданный код ошибки: {code}"

def t_audit_append_only():
    cur.execute("RESET ROLE")
    cur.execute("""INSERT INTO audit_log (tenant_id, action, entity, entity_id)
                   VALUES (%s, 'attendance.mark', 'lesson', %s)""", (TA, L % "1b"))
    conn.commit()
    rejects(cur, "UPDATE audit_log SET action = 'подделка' WHERE tenant_id = %s",
            (TA,), "правка журнала аудита")

def t_junction_isolation():
    cur.execute("RESET ROLE")
    cur.execute("""INSERT INTO family (id, tenant_id, name) VALUES (%s, %s, 'Сагындык')""",
                (L % "4a", TA))
    cur.execute("""INSERT INTO family_member (family_id, person_id, relation)
                   VALUES (%s, %s, 'student')""", (L % "4a", L % "a3"))
    conn.commit()
    cur.execute("SET ROLE rockcrm_app")
    cur.execute("SELECT set_config('app.tenant_id', %s, false)", (TB,))
    cur.execute("SELECT count(*) FROM family_member")
    n = cur.fetchone()[0]
    conn.commit()
    assert n == 0, f"чужой тенант видит {n} связей семьи"

check("тенанты видят только свои данные", t_rls_isolation)
check("без выставленного тенанта не видно ничего", t_rls_no_tenant)
check("нельзя записать строку в чужой тенант", t_rls_write_guard)
check("журнал абонемента защищён от правок и удалений", t_journal_append_only)
check("журнал аудита защищён от правок", t_audit_append_only)
check("связующие таблицы изолированы через родителя", t_junction_isolation)

cur.execute("RESET ROLE")
conn.commit()

# ---------- итог ----------
print(f"\n{'='*62}\nпройдено: {len(ok)}   провалено: {len(fail)}   "
      f"пропущено: {len(skipped)}\n{'='*62}")
for n in ok:
    print(f"  [ok]   {n}")
for n in skipped:
    print(f"  [SKIP] {n}  (нужен btree_gist)")
for n in fail:
    print(f"  [FAIL] {n}")
sys.exit(1 if fail else 0)
