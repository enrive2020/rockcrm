"""Ночное сгорание отработок по сроку.

Главное свойство задания — идемпотентность, и проверяется она буквально:
второй прогон подряд не должен списать отработку второй раз. Ключ здесь —
сама отработка (`expired_at`), а не отметка «задание сегодня отработало»:
после сбоя на середине задание обязано доработать остаток, а не начать
с чистого листа и не пропустить всё.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from conftest import HEADERS, TENANT, student, subscription
from scripts import expire_makeups

STUDENT = "sagyndyk"          # у Амины ровно одна непотраченная отработка
SUB = "sagyndyk"

# «Сегодня» берётся в поясе школы — ровно так же, как его берёт задание.
# По часам сервера тест разъехался бы с ним на сутки на стенде в другом
# поясе, и падал бы не на ошибке, а на разнице во времени.
SCHOOL_TZ = ZoneInfo("Asia/Almaty")


def school_today() -> dt.date:
    return dt.datetime.now(SCHOOL_TZ).date()


def credit(sql) -> dict:
    return sql.execute(
        """SELECT id, expires_on, expired_at, used_at FROM makeup_credit
            WHERE student_id = %s""",
        (student(STUDENT),),
    ).fetchone()


def makeups(sql) -> int:
    return int(
        sql.execute(
            "SELECT makeups_balance FROM subscription WHERE id = %s", (subscription(SUB),)
        ).fetchone()["makeups_balance"]
    )


def entries(sql) -> list[dict]:
    return sql.execute(
        """SELECT makeups_delta, reason FROM subscription_entry
            WHERE subscription_id = %s AND kind = 'makeup_expire' ORDER BY id""",
        (subscription(SUB),),
    ).fetchall()


def set_expiry(sql, days_from_today: int) -> dt.date:
    """Двигает срок отработки относительно сегодняшнего дня.

    Двигаем данные, а не часы: перевести время у процесса теста нечем,
    а проверка — про дату, а не про способ её узнать. Тот же приём, что
    у тестов ночной сверки статусов.
    """
    day = school_today() + dt.timedelta(days=days_from_today)
    sql.execute(
        "UPDATE makeup_credit SET expires_on = %s WHERE student_id = %s",
        (day, student(STUDENT)),
    )
    sql.commit()
    return day


def notifications(sql) -> list[dict]:
    return sql.execute(
        """SELECT dedup_key, template, to_address, payload FROM notification
            WHERE tenant_id = %s AND template = 'makeup_expiring'""",
        (TENANT,),
    ).fetchall()


# ---------------------------------------------------------------------------


def test_overdue_makeup_burns_and_leaves_a_trace(client, sql):
    """Просроченная отработка гасится и объясняется в журнале.

    Баланс отработок ведёт триггер от журнала, поэтому погасить кредит,
    не записав движение, значило бы оставить в балансе отработку, которой
    больше нет. Родителю на «а где моя отработка» отвечают именно журналом.
    """
    expired_on = set_expiry(sql, -1)
    assert makeups(sql) == 1

    assert expire_makeups.run(TENANT)["expired"] == 1

    assert makeups(sql) == 0
    assert credit(sql)["expired_at"] is not None

    rows = entries(sql)
    assert len(rows) == 1
    assert rows[0]["makeups_delta"] == -1
    # В тексте стоит срок из договора, а не день, когда до отработки дошло
    # задание: опоздавшее на сутки задание не должно двигать дату сгорания.
    assert rows[0]["reason"] == f"Отработка сгорела: срок истёк {expired_on:%d.%m.%Y}"


def test_second_run_does_not_burn_it_again(client, sql):
    """Два прогона подряд — одно списание. Иначе баланс уходит в минус."""
    set_expiry(sql, -1)

    assert expire_makeups.run(TENANT)["expired"] == 1
    assert expire_makeups.run(TENANT)["expired"] == 0

    assert makeups(sql) == 0
    assert len(entries(sql)) == 1, "второй прогон дописал в журнал движение, которого не было"


def test_last_day_of_the_makeup_is_still_its_day(client, sql):
    """Отработка со сроком «сегодня» ещё живёт.

    «Действует до 1 сентября» родитель понимает как «первого ещё можно»,
    и карточка ученика показывает такую отработку как days_left = 0,
    а не как вчерашнюю. Сгорание строго после срока.
    """
    set_expiry(sql, 0)

    assert expire_makeups.run(TENANT)["expired"] == 0
    assert makeups(sql) == 1
    assert credit(sql)["expired_at"] is None


def test_used_makeup_is_left_alone(client, sql):
    """Потраченную отработку сжигать нечего — она уже ушла из баланса."""
    set_expiry(sql, -1)
    sql.execute(
        "UPDATE makeup_credit SET used_at = now() WHERE student_id = %s", (student(STUDENT),)
    )
    sql.commit()

    assert expire_makeups.run(TENANT)["expired"] == 0
    assert credit(sql)["expired_at"] is None


def test_burnt_makeup_disappears_from_the_student_card(client, sql):
    """Сгоревшая отработка уходит с карточки: она уже история, а не остаток."""
    set_expiry(sql, -1)
    expire_makeups.run(TENANT)

    card = client.get(f"/api/v1/students/{student(STUDENT)}", headers=HEADERS).json()
    assert card["makeups"] == []
    assert card["subscription"]["makeups_balance"] == 0
    # А в журнале она осталась — человеческой формулировкой, как и всё остальное.
    assert any(row["title"] == "Отработка сгорела по сроку" for row in card["ledger"])


def test_dry_run_shows_but_does_not_burn(client, sql):
    """Посмотреть, что задание собирается списать, надо уметь до списания."""
    set_expiry(sql, -1)

    assert expire_makeups.run(TENANT, dry_run=True)["expired"] == 1

    assert makeups(sql) == 1
    assert credit(sql)["expired_at"] is None
    assert entries(sql) == []


# ---------------------------------------------------------------------------
# Предупреждение родителю (spec.md §8: за 5 дней, WhatsApp)
# ---------------------------------------------------------------------------


def test_warning_goes_to_the_payer_five_days_before(client, sql):
    """Сообщение получает плательщик семьи, а не ребёнок.

    У ребёнка своего телефона обычно нет, а решение «прийти на отработку»
    принимает и везёт родитель. Оно ставится в очередь, а не отправляется:
    относит сообщения отдельный воркер.
    """
    expires_on = set_expiry(sql, 3)

    assert expire_makeups.run(TENANT)["warned"] == 1

    queued = notifications(sql)
    assert len(queued) == 1
    assert queued[0]["dedup_key"] == f"makeup_expiring:{credit(sql)['id']}"
    assert queued[0]["to_address"] == "+77015552418"      # Гульнара, мама Амины
    assert queued[0]["payload"]["days_left"] == 3
    assert queued[0]["payload"]["expires_on"] == expires_on.isoformat()


def test_warning_is_sent_once_however_often_the_job_runs(client, sql):
    """Задание идёт каждую ночь, а предупреждение уходит одно.

    От повтора бережёт dedup_key в очереди, а не узость окна: задание,
    пропустившее сутки из-за сбоя, обязано дослать вчерашних, но не должно
    задваивать сегодняшних.
    """
    set_expiry(sql, 3)

    assert expire_makeups.run(TENANT)["warned"] == 1
    assert expire_makeups.run(TENANT)["warned"] == 0
    assert expire_makeups.run(TENANT)["warned"] == 0

    assert len(notifications(sql)) == 1


def test_no_warning_while_the_deadline_is_far(client, sql):
    set_expiry(sql, 20)

    assert expire_makeups.run(TENANT)["warned"] == 0
    assert notifications(sql) == []


def test_burnt_makeup_is_not_warned_about(client, sql):
    """Сгоревшую предупреждать поздно — оба шага смотрят на один и тот же факт."""
    set_expiry(sql, -1)

    totals = expire_makeups.run(TENANT)
    assert totals == {"expired": 1, "warned": 0}
    assert notifications(sql) == []
