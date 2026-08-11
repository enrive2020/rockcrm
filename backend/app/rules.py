"""Правила отметки посещаемости — единственное место, где они живут.

Эту функцию вызывает и предпросмотр (GET /lessons/{id} -> mark_effects),
и применение (POST /lessons/{id}/attendance). Никакой второй реализации
«для сохранения» быть не должно: расхождение между обещанным и сделанным —
худший баг этой системы, потому что администратор показывает родителю одно,
а в журнале оказывается другое, и доверие к CRM кончается на этом месте.

Правила берутся из subscription.rules конкретного абонемента, а не из
tenant.default_rules: проданный абонемент — договор на условиях момента
покупки (spec.md §4.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

# Все шесть значений отметки из CHECK-ограничения attendance.mark.
MARKS: tuple[str, ...] = (
    "came",
    "late",
    "no_show",
    "cancelled_early",
    "cancelled_late",
    "cancelled_teacher",
)

MARK_LABELS: dict[str, str] = {
    "came": "Пришёл",
    "late": "Опоздал",
    "no_show": "Прогул без предупреждения",
    "cancelled_early": "Отменил заранее",
    "cancelled_late": "Отменил поздно",
    "cancelled_teacher": "Отменил преподаватель",
}

# Значения по умолчанию повторяют tenant.default_rules из 001_core.sql.
# Нужны только как страховка от абонемента с неполным JSON: отсутствие ключа
# не должно ронять отметку — должно давать поведение «как у всех».
DEFAULT_RULES: dict[str, Any] = {
    "no_show_burns": True,
    "cancel_notice_hours": 24,
    "cancel_early_effect": "makeup",
    "teacher_cancel_effect": "no_charge",
    "makeup_ttl_days": 30,
    "freeze_days_per_year": 14,
    "pay_teacher_on_no_show": True,
}


@dataclass(frozen=True)
class SubscriptionState:
    """Снимок абонемента на момент расчёта."""

    id: str
    lessons_total: int
    lessons_balance: int
    makeups_balance: int
    valid_until: date
    status: str
    rules: dict[str, Any]
    price: Decimal = Decimal(0)

    def rule(self, name: str) -> Any:
        value = self.rules.get(name)
        return DEFAULT_RULES[name] if value is None else value


@dataclass(frozen=True)
class MarkEffect:
    """Последствия одной отметки — ровно то, что уйдёт и в предпросмотр, и в базу."""

    mark: str
    lessons_delta: int
    makeups_delta: int
    teacher_amount: int
    lessons_after: int
    makeups_after: int
    summary: str
    # Почему применить нельзя. None = можно. Предпросмотр показывает причину
    # заранее, применение отвечает 422 с этим же текстом — один источник.
    blocked_reason: str | None = None
    # Как считалась зарплата. Уходит в payroll_entry.calc: через полгода
    # объяснить преподавателю сумму без снимка расчёта невозможно.
    payroll_calc: dict[str, Any] = field(default_factory=dict)

    def api_dict(self) -> dict[str, Any]:
        """Форма из контракта плюс два дополнительных поля."""
        return {
            "lessons_delta": self.lessons_delta,
            "makeups_delta": self.makeups_delta,
            "teacher_amount": self.teacher_amount,
            "lessons_after": self.lessons_after,
            "makeups_after": self.makeups_after,
            "summary": self.summary,
            "blocked_reason": self.blocked_reason,
        }


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 занятие / 2 занятия / 5 занятий."""
    n = abs(n)
    if 10 < n % 100 < 20:
        return many
    tail = n % 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"


def lessons_word(n: int) -> str:
    return plural(n, "занятие", "занятия", "занятий")


# ---------------------------------------------------------------------------
# Ядро: сколько занятий, отработок и денег даёт каждая отметка.
#
# Таблица spec.md §4.3 задаёт базовое поведение, настройки из rules его
# двигают. Разбито на две части — «что с абонементом» и «что с деньгами», —
# потому что школы настраивают их независимо: часть платит преподавателю
# за прогул ученика, часть нет, и к списанию занятия это отношения не имеет.
# ---------------------------------------------------------------------------


def _subscription_deltas(mark: str, sub: SubscriptionState) -> tuple[int, int]:
    """Возвращает (lessons_delta, makeups_delta) по правилам абонемента."""
    if mark in ("came", "late"):
        return -1, 0

    if mark in ("no_show", "cancelled_late"):
        # cancelled_late привязан к тому же правилу, что и прогул: контракт
        # даёт отдельную отметку, но spec.md отдельного правила для неё
        # не содержит, а по смыслу поздняя отмена — тот же несостоявшийся урок.
        # Школа, отключившая сгорание прогула, вряд ли имела в виду обратное.
        return (-1, 0) if sub.rule("no_show_burns") else (0, 0)

    if mark == "cancelled_early":
        effect = sub.rule("cancel_early_effect")
        if effect == "makeup":
            return 0, 1
        if effect == "burn":
            return -1, 0
        return 0, 0  # no_charge

    if mark == "cancelled_teacher":
        # По умолчанию no_charge: занятие не списывается, отработка не выдаётся.
        return (0, 1) if sub.rule("teacher_cancel_effect") == "makeup" else (0, 0)

    raise ValueError(f"неизвестная отметка: {mark}")


def _teacher_pay_share(mark: str, sub: SubscriptionState | None) -> float:
    """Доля ставки, которую получает преподаватель."""
    if mark in ("came", "late"):
        return 1.0
    if mark in ("no_show", "cancelled_late"):
        rules = sub.rules if sub else DEFAULT_RULES
        pay = rules.get("pay_teacher_on_no_show", DEFAULT_RULES["pay_teacher_on_no_show"])
        return 1.0 if pay else 0.0
    # Отмена заранее и отмена преподавателем не оплачиваются: урока не было
    # и предупреждение пришло вовремя.
    return 0.0


def _teacher_amount(
    mark: str,
    sub: SubscriptionState | None,
    rate_amount: Decimal | None,
    rate_percent: Decimal | None,
) -> tuple[int, dict[str, Any]]:
    """Сумма преподавателю в целых тенге плюс снимок расчёта."""
    share = _teacher_pay_share(mark, sub)

    if rate_amount is not None:
        base = Decimal(rate_amount)
        calc: dict[str, Any] = {"kind": "fixed", "rate": int(base)}
    elif rate_percent is not None:
        # Процент считается от стоимости одного занятия абонемента: другой
        # цены урока в схеме нет. Без абонемента (разовое, пробное) базы для
        # процента не существует — платим 0 и честно пишем это в расчёт.
        if sub is not None and sub.lessons_total > 0:
            lesson_price = Decimal(sub.price) / Decimal(sub.lessons_total)
        else:
            lesson_price = Decimal(0)
        base = lesson_price * Decimal(rate_percent) / Decimal(100)
        calc = {
            "kind": "percent",
            "percent": float(rate_percent),
            "lesson_price": int(lesson_price),
        }
    else:
        base = Decimal(0)
        calc = {"kind": "none"}

    amount = int((base * Decimal(str(share))).to_integral_value())
    calc.update({"mark": mark, "share": share, "amount": amount})
    return amount, calc


def _summary(
    mark: str,
    sub: SubscriptionState | None,
    lessons_delta: int,
    makeups_delta: int,
    lessons_after: int,
    teacher_amount: int,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return blocked_reason

    if sub is None:
        return (
            "Действующего абонемента нет — занятие пойдёт разовой оплатой. "
            f"Преподавателю {money(teacher_amount)}."
        )

    if lessons_delta < 0:
        head = (
            f"Спишется {-lessons_delta} {lessons_word(lessons_delta)}, "
            f"останется {lessons_after}."
        )
    elif mark in ("no_show", "cancelled_late"):
        head = f"Занятие не списывается по правилам абонемента, останется {lessons_after}."
    else:
        head = f"Занятие не спишется, останется {lessons_after}."

    if makeups_delta > 0:
        ttl = sub.rule("makeup_ttl_days")
        head += f" Начислится {makeups_delta} отработка сроком {ttl} дней."

    return f"{head} Преподавателю {money(teacher_amount)}."


def compute_effect(
    mark: str,
    subscription: SubscriptionState | None,
    rate_amount: Decimal | None = None,
    rate_percent: Decimal | None = None,
) -> MarkEffect:
    """Единственная точка расчёта последствий отметки.

    Намеренно не зависит от текущего времени: предпросмотр и применение
    разделены секундами или минутами, и любая зависимость от now() дала бы
    редкий, невоспроизводимый разъезд между обещанием и фактом.
    """
    if mark not in MARKS:
        raise ValueError(f"неизвестная отметка: {mark}")

    if subscription is None:
        lessons_delta = makeups_delta = 0
        lessons_after = makeups_after = 0
        blocked_reason = None
    else:
        lessons_delta, makeups_delta = _subscription_deltas(mark, subscription)
        lessons_after = subscription.lessons_balance + lessons_delta
        makeups_after = subscription.makeups_balance + makeups_delta
        blocked_reason = None
        if lessons_delta < 0 and lessons_after < 0:
            # Уход в минус недопустим: абонемент — оплаченный пакет, а не кредит.
            blocked_reason = (
                "На абонементе не осталось занятий — списывать нечего. "
                "Продайте продление или отметьте отмену."
            )

    teacher_amount, calc = _teacher_amount(mark, subscription, rate_amount, rate_percent)

    return MarkEffect(
        mark=mark,
        lessons_delta=lessons_delta,
        makeups_delta=makeups_delta,
        teacher_amount=teacher_amount,
        lessons_after=lessons_after,
        makeups_after=makeups_after,
        summary=_summary(
            mark,
            subscription,
            lessons_delta,
            makeups_delta,
            lessons_after,
            teacher_amount,
            blocked_reason,
        ),
        blocked_reason=blocked_reason,
        payroll_calc=calc,
    )


def compute_all_effects(
    subscription: SubscriptionState | None,
    rate_amount: Decimal | None = None,
    rate_percent: Decimal | None = None,
) -> dict[str, MarkEffect]:
    """Все шесть отметок разом — ровно то, что уходит в mark_effects."""
    return {
        mark: compute_effect(mark, subscription, rate_amount, rate_percent) for mark in MARKS
    }


# Порог остатка, при котором администратору пора продавать продление
# (spec.md §4.3 и §8: уведомление «абонемент заканчивается» при остатке 2).
LOW_BALANCE_THRESHOLD = 2


def low_balance_alert(lessons_after: int) -> dict[str, str] | None:
    if lessons_after > LOW_BALANCE_THRESHOLD:
        return None
    if lessons_after <= 0:
        message = "Занятия на абонементе закончились — нужно продление"
    else:
        message = (
            f"Осталось {lessons_after} {lessons_word(lessons_after)} — "
            "пора предложить продление"
        )
    return {"kind": "subscription_low", "message": message}


# ---------------------------------------------------------------------------
# Журнал абонемента: формулировки для экрана «Движение по абонементу».
#
# Это тот экран, которым администратор отвечает родителю на «куда делось
# занятие». Код вида записи («charge») ответом не является: строка обязана
# читаться вслух по телефону без перевода.
# ---------------------------------------------------------------------------

LEDGER_TITLES: dict[str, str] = {
    "purchase": "Оплата абонемента",
    "charge": "Занятие проведено",
    "refund": "Занятие возвращено — отметка отменена",
    "makeup_grant": "Начислена отработка",
    "makeup_use": "Отработка использована",
    "makeup_expire": "Отработка сгорела по сроку",
    "freeze": "Заморозка абонемента",
    "adjust": "Корректировка администратора",
    "transfer_in": "Перенос остатка с прошлого абонемента",
    "transfer_out": "Перенос остатка на новый абонемент",
    "expire": "Остаток сгорел по окончании срока",
}

# Списание объясняется отметкой, а не видом записи: «−1» без причины —
# ровно тот случай, из-за которого родитель и звонит.
_CHARGE_TITLES: dict[str, str] = {
    "came": "Занятие проведено",
    "late": "Занятие проведено, ученик опоздал",
    "no_show": "Прогул без предупреждения",
    "cancelled_late": "Поздняя отмена — занятие списано",
    "cancelled_early": "Отмена заранее — занятие списано",
    "cancelled_teacher": "Отменил преподаватель — занятие списано",
}

_GRANT_TITLES: dict[str, str] = {
    "cancelled_early": "Отмена заранее → отработка",
    "cancelled_teacher": "Отменил преподаватель → отработка",
}


def ledger_title(kind: str, mark: str | None = None, reason: str | None = None) -> str:
    """Человеческая формулировка строки журнала."""
    if kind == "charge" and mark:
        return _CHARGE_TITLES.get(mark, LEDGER_TITLES["charge"])
    if kind == "makeup_grant" and mark:
        return _GRANT_TITLES.get(mark, LEDGER_TITLES["makeup_grant"])
    if kind == "freeze" and reason:
        # У заморозки заголовок и есть причина: период в колонках
        # subscription_entry не хранится, и восстановить «14–25 августа»
        # из строки журнала больше неоткуда. Обе фразы собирают freeze_reason()
        # и unfreeze_reason(), поэтому в базе и на экране написано дословно
        # одно и то же — журнал читается и без API.
        return reason
    # Неизвестный вид лучше показать как есть, чем спрятать за «прочее»:
    # пустая строка в журнале страшнее непереведённого кода.
    return LEDGER_TITLES.get(kind, kind)


# Месяцы в родительном падеже: «14–25 августа», а не «14–25 август».
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def days_word(n: int) -> str:
    return plural(n, "день", "дня", "дней")


def date_range_ru(start: date, end: date) -> str:
    """«14–25 августа» или «28 августа – 3 сентября»."""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{end.day} {_MONTHS_GENITIVE[start.month - 1]}"
    return (
        f"{start.day} {_MONTHS_GENITIVE[start.month - 1]} – "
        f"{end.day} {_MONTHS_GENITIVE[end.month - 1]}"
    )


def freeze_reason(start: date, end: date, days: int, cause: str | None) -> str:
    """Строка журнала о заморозке.

    Две недели каникул обязаны быть видны в журнале, а не только в аудите:
    журналом администратор отвечает на «куда делось занятие», и «в эти дни
    абонемент стоял» — такой же ответ, как «занятие проведено».
    """
    text = f"Заморозка {date_range_ru(start, end)}, {days} {days_word(days)}"
    return f"{text} · {cause}" if cause else text


def unfreeze_reason(start: date, end: date, valid_until: date) -> str:
    """Строка журнала о снятии заморозки — компенсирующая, а не удаление.

    Записи журнала не правятся и не удаляются, поэтому снятая заморозка
    остаётся в истории вместе с фактом снятия. Иначе спор «мне говорили,
    что срок продлили» разрешать было бы нечем.
    """
    return (
        f"Заморозка {date_range_ru(start, end)} снята, "
        f"срок вернулся на {valid_until:%d.%m.%Y}"
    )


# ---------------------------------------------------------------------------
# Риск оттока
#
# Эвристика, а не модель. Главное требование контракта — каждая причина
# в reasons должна быть проверяемым фактом («1 прогул за 30 дней»),
# а не оценкой («ученик демотивирован»): администратор звонит родителю
# и обязан опираться на то, что можно назвать вслух.
#
# Смягчающие факты (стаж, число купленных абонементов) в reasons не попадают:
# в списке причин риска «занимается 6 месяцев» читается как довод ЗА отток,
# хотя это довод против. Они снижают счёт и уходят отдельным полем
# mitigations — тем же проверяемым фактом, но на своей стороне весов.
# ---------------------------------------------------------------------------

# Вес прогула самый большой осознанно: пропуск без предупреждения — первый
# наблюдаемый признак ухода, он появляется раньше, чем кончается абонемент.
_NO_SHOW_WEIGHT = 30
_NO_SHOW_CAP = 60
_LOW_BALANCE_WEIGHT = 25
_NO_SUBSCRIPTION_WEIGHT = 45
_EXPIRING_SOON_WEIGHT = 12
_SILENT_2W_WEIGHT = 15
_SILENT_4W_WEIGHT = 30
_LOYALTY_DISCOUNT = 8

RISK_MEDIUM = 20
RISK_HIGH = 55

RISK_LEVELS = {"low": "Низкий", "medium": "Средний", "high": "Высокий"}


def churn_risk(
    *,
    no_shows_30d: int,
    has_subscription: bool,
    lessons_balance: int,
    days_until_expiry: int | None,
    days_since_last_lesson: int | None,
    months_with_school: int,
    subscriptions_bought: int,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    if no_shows_30d > 0:
        score += min(no_shows_30d * _NO_SHOW_WEIGHT, _NO_SHOW_CAP)
        word = plural(no_shows_30d, "прогул", "прогула", "прогулов")
        reasons.append(f"{no_shows_30d} {word} за 30 дней")

    if not has_subscription:
        score += _NO_SUBSCRIPTION_WEIGHT
        reasons.append("действующего абонемента нет")
    else:
        if lessons_balance <= LOW_BALANCE_THRESHOLD:
            score += _LOW_BALANCE_WEIGHT
            if lessons_balance <= 0:
                reasons.append("занятия на абонементе закончились")
            else:
                reasons.append(
                    f"абонемент кончается через {lessons_balance} "
                    f"{lessons_word(lessons_balance)}"
                )
        if days_until_expiry is not None and days_until_expiry <= 7:
            score += _EXPIRING_SOON_WEIGHT
            if days_until_expiry < 0:
                reasons.append("срок абонемента истёк")
            else:
                word = plural(days_until_expiry, "день", "дня", "дней")
                reasons.append(f"срок абонемента истекает через {days_until_expiry} {word}")

    if days_since_last_lesson is not None and days_since_last_lesson >= 14:
        score += _SILENT_4W_WEIGHT if days_since_last_lesson >= 28 else _SILENT_2W_WEIGHT
        word = plural(days_since_last_lesson, "день", "дня", "дней")
        reasons.append(f"не было занятий {days_since_last_lesson} {word}")

    # Стаж и число купленных абонементов снижают риск. В reasons они не идут:
    # там перечислено то, что тревожит, а лояльность — довод в другую сторону.
    mitigations: list[str] = []
    if months_with_school >= 6:
        score -= _LOYALTY_DISCOUNT
        word = plural(months_with_school, "месяц", "месяца", "месяцев")
        mitigations.append(f"занимается {months_with_school} {word} подряд")
    if subscriptions_bought >= 3:
        score -= _LOYALTY_DISCOUNT
        word = plural(subscriptions_bought, "абонемент", "абонемента", "абонементов")
        mitigations.append(f"куплено {subscriptions_bought} {word}")

    score = max(0, min(100, score))
    level = "high" if score >= RISK_HIGH else "medium" if score >= RISK_MEDIUM else "low"
    return {"level": level, "score": score, "reasons": reasons, "mitigations": mitigations}


# ---------------------------------------------------------------------------
# Воронка заявок
#
# Значения стадий и причин отказа взяты дословно из CHECK-ограничений
# db/004_sales.sql. Порядок стадий здесь — это и порядок колонок на доске,
# и порядок расчёта конверсии в отчёте: «прошёл дальше» означает «попал
# в стадию, стоящую правее».
# ---------------------------------------------------------------------------

STAGE_ORDER: tuple[str, ...] = ("new", "contacting", "trial_booked", "trial_held", "won")
# lost стоит отдельной колонкой: это не следующий шаг воронки, а выход из неё.
LOST = "lost"
STAGES: tuple[str, ...] = STAGE_ORDER + (LOST,)

STAGE_TITLES: dict[str, str] = {
    "new": "Новая",
    "contacting": "Дозвон",
    "trial_booked": "Пробный назначен",
    "trial_held": "Пробный проведён",
    "won": "Абонемент куплен",
    "lost": "Отказ",
}

LOST_REASONS: dict[str, str] = {
    "price": "Дорого",
    "location": "Неудобно ехать",
    "schedule": "Не подошло расписание",
    "competitor": "Ушли к конкуренту",
    "no_answer": "Не дозвонились",
    "not_ready": "Пока не готовы",
    "other": "Другое",
}

SOURCE_TITLES: dict[str, str] = {
    "telegram_bot": "TG-бот",
    "site_form": "Сайт",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "referral": "Сарафан",
    "walk_in": "Пришли сами",
    "phone": "Звонок",
    "other": "Другое",
}

# Две неудачные попытки дозвона — тот момент, когда заявкой надо заняться
# иначе: писать в WhatsApp, а не звонить в третий раз (prototype: «×2»).
NO_ANSWER_ATTEMPTS = 2


def stage_index(stage: str) -> int:
    """Позиция стадии в воронке; -1 у lost — он никуда не ведёт."""
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else -1


def humanize_duration(seconds: float) -> str:
    """«5 минут», «2 часа», «3 дня» — сколько заявка ждёт ответа.

    Округляем вниз до крупной единицы: администратору важно отличить «час»
    от «трёх дней», а не 2 часа 47 минут от 2 часов 51 минуты. Точность
    здесь создавала бы шум в колонке, которую читают взглядом.
    """
    seconds = max(int(seconds), 0)
    minutes = seconds // 60
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    days = hours // 24
    return f"{days} {days_word(days)}"
