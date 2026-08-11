"""Дата операции — параметр, а не свойство среды (ADR-001, issue #15).

До этого модуля вся работа с абонементами опиралась на системные часы:
заморозку нельзя было начать вчера, истёкший абонемент не находился, а
проданный «в марте» становился `expired` в момент вставки. Из-за этого
нельзя было ни перевезти школу с Excel, ни внести неделю занятий за
заболевшего администратора — то есть невозможен был не редкий, а ежемесячный
сценарий.

Здесь живёт всё, что отвечает на вопрос «какой датой считать эту операцию»:

* значение по умолчанию — сегодня в поясе филиала, поэтому существующие
  вызовы не меняются и ни один старый тест не знает об этом модуле;
* окно правки ограничено правилом школы `backdating_days` (по умолчанию 30),
  ноль означает «только сегодня»;
* закрытый зарплатный период — жёсткая граница, которую не открывает
  никакое окно;
* всё, что внесено задним числом, помечается флагом `backdated`.

Почему окно вообще есть: разрешить любую дату значило бы обменять один класс
ошибок на другой, более тихий. Заморозка «с прошлого понедельника» задним
числом меняет уже проведённые занятия, а истёкший абонемент, который снова
можно списывать, — прямая дорога к отрицательным остаткам. Разбор вариантов
целиком — в docs/adr-001-operation-date.md.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import psycopg

from .errors import ApiError

# Сколько дней назад можно внести операцию, если школа не сказала иначе.
# Месяц — это «администратор болел две недели» плюс запас: сценарий, ради
# которого всё и делалось, обязан укладываться в значение по умолчанию,
# иначе первым действием каждой школы будет правка настроек.
DEFAULT_BACKDATING_DAYS = 30

RULE = "backdating_days"


@dataclass(frozen=True)
class OperationDate:
    """Дата, которой считается операция, и сегодняшний день филиала.

    `backdated` вычисляется здесь, а не у вызывающего: иначе каждая из четырёх
    операций сравнивала бы даты сама, и однажды одна из них сравнила бы иначе.
    """

    on: dt.date
    today: dt.date

    @property
    def backdated(self) -> bool:
        return self.on < self.today


def today_in(tz_name: str | None) -> dt.date:
    """Сегодня в поясе филиала.

    Пояс филиала, а не сервера: у школы в Алматы день начинается на пять
    часов раньше, чем в UTC, и операция вечера 11 августа для сервера
    пришлась бы ещё на десятое — то есть на день раньше, чем её видит
    администратор.
    """
    return dt.datetime.now(ZoneInfo(tz_name or "Asia/Almaty")).date()


def resolve(
    cur: psycopg.Cursor,
    tenant_id: str,
    requested: dt.date | None,
    tz_name: str | None,
) -> OperationDate:
    """Проверяет запрошенную дату операции и возвращает её вместе с «сегодня».

    Без параметра — сегодняшний день филиала: ровно то поведение, которое
    было до ADR-001, поэтому вызовы без `effective_date` не меняются.
    """
    today = today_in(tz_name)
    if requested is None:
        return OperationDate(on=today, today=today)

    if requested > today:
        # Дата операции в будущем — это не «заранее», а ошибка ввода:
        # отметить занятие, которого ещё не было, нельзя, а абонемент,
        # проданный завтрашним числом, выпадет из сегодняшней кассы.
        raise ApiError(
            422,
            "effective_date_future",
            f"Дата операции {requested:%d.%m.%Y} ещё не наступила: "
            f"сегодня {today:%d.%m.%Y}.",
            {"today": today.isoformat(), "effective_date": requested.isoformat()},
        )

    window = backdating_days(cur, tenant_id)
    earliest = today - dt.timedelta(days=window)
    if requested < earliest:
        raise ApiError(
            422,
            "effective_date_too_old",
            (
                f"Задним числом можно вносить операции не глубже чем на {window} "
                f"{_days_word(window)}: не раньше {earliest:%d.%m.%Y}, "
                f"а запрошено {requested:%d.%m.%Y}. "
                "Окно задаётся правилом школы backdating_days."
                if window
                else (
                    f"Школа запретила ввод задним числом (backdating_days = 0): "
                    f"операцию можно внести только сегодняшним числом "
                    f"{today:%d.%m.%Y}, а запрошено {requested:%d.%m.%Y}."
                )
            ),
            {
                "today": today.isoformat(),
                "earliest": earliest.isoformat(),
                "effective_date": requested.isoformat(),
                RULE: window,
            },
        )

    return OperationDate(on=requested, today=today)


def backdating_days(cur: psycopg.Cursor, tenant_id: str) -> int:
    """Окно правки из настроек школы. Отсутствие правила — значение по умолчанию.

    Правило живёт в `tenant.default_rules`, а не копируется в абонемент, как
    остальные: это регламент работы администратора, а не условие договора
    с родителем. Школа, ужесточившая окно сегодня, ужесточила его для всех
    вчерашних абонементов тоже — и это ровно то, чего она хотела.
    """
    cur.execute("SELECT default_rules FROM tenant WHERE id = %s", (tenant_id,))
    row = cur.fetchone()
    value = (row["default_rules"] or {}).get(RULE) if row else None
    if value is None:
        return DEFAULT_BACKDATING_DAYS
    return max(int(value), 0)


def refuse_closed_payroll(cur: psycopg.Cursor, when: OperationDate) -> None:
    """Отказывает, если дата операции попадает в закрытый зарплатный период.

    Жёсткая граница: её не открывает никакое окно `backdating_days`.
    Начисления закрытого месяца уже выплачены людям на руки, и операция,
    датированная внутрь него, означала бы расхождение ведомости с фактически
    выданными деньгами — то, что не сверить уже никогда.

    Выход из положения есть и он тот же, что для любой правки закрытого
    месяца (spec.md §6.2): внести операцию сегодняшним числом. Начисление
    попадёт в текущую ведомость корректировкой, и обе суммы останутся
    объяснимыми.

    Проверка стоит на операциях, которые пишут в `payroll_entry`, — отметке
    и её отмене. Продажа и заморозка зарплату не двигают, и запрещать их
    в закрытом месяце значило бы мешать вносить платежи без всякой пользы.
    """
    cur.execute(
        """
        SELECT id, lower(period) AS from_day, upper(period) AS to_day, closed_at
        FROM payroll_period
        WHERE closed_at IS NOT NULL AND period @> %s::date
        LIMIT 1
        """,
        (when.on,),
    )
    period = cur.fetchone()
    if period is None:
        return
    last_day = period["to_day"] - dt.timedelta(days=1)
    raise ApiError(
        422,
        "payroll_period_closed",
        f"Период {period['from_day']:%d.%m.%Y}–{last_day:%d.%m.%Y} закрыт, "
        f"зарплата за него уже посчитана и выдана. Операцию нельзя датировать "
        f"внутрь него ни при каком окне правки — внесите её сегодняшним "
        f"числом {when.today:%d.%m.%Y}, она уйдёт корректировкой "
        "в текущую ведомость.",
        {
            "period_id": str(period["id"]),
            "from": period["from_day"].isoformat(),
            "to": last_day.isoformat(),
            "effective_date": when.on.isoformat(),
            "today": when.today.isoformat(),
        },
    )


def _days_word(n: int) -> str:
    # Локальная копия склонения: тянуть сюда rules.py ради одного слова
    # значило бы связать проверку даты со всей таблицей правил отметки.
    n = abs(n)
    if 10 < n % 100 < 20:
        return "дней"
    tail = n % 10
    if tail == 1:
        return "день"
    if 2 <= tail <= 4:
        return "дня"
    return "дней"


# ---------------------------------------------------------------------------
# Пометка «внесено задним числом»
#
# Колонки `subscription_entry.backdated` и `attendance.backdated` приезжают
# миграцией 009. Пока она не накатана, приложение обязано работать по-старому:
# школа не должна падать оттого, что администратор базы ещё не дошёл
# до сервера. Поэтому наличие колонки спрашивается у самой базы один раз
# за процесс, а не выводится из наличия файла в db/.
#
# Когда 009 накатана везде, эта функция и её вызовы убираются: писать колонку
# станет можно безусловно.
# ---------------------------------------------------------------------------

_HAS_COLUMN: dict[str, bool] = {}


def marks_backdating(cur: psycopg.Cursor, table: str) -> bool:
    """Есть ли в таблице колонка `backdated` (миграция 009)."""
    known = _HAS_COLUMN.get(table)
    if known is None:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s AND column_name = 'backdated'
            """,
            (table,),
        )
        known = _HAS_COLUMN[table] = cur.fetchone() is not None
    return known


def forget_schema_probe() -> None:
    """Сбрасывает кэш наличия колонок. Нужен только тестам после миграции."""
    _HAS_COLUMN.clear()
