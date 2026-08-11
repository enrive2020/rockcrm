"""Правила отметки без базы: чистая проверка таблицы из spec.md §4.3.

База здесь не нужна — нужна уверенность, что настройки абонемента двигают
расчёт именно так, как обещано школе при продаже.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.rules import DEFAULT_RULES, MARKS, SubscriptionState, compute_all_effects, compute_effect


def sub(balance: int = 5, makeups: int = 1, **rule_overrides) -> SubscriptionState:
    return SubscriptionState(
        id="00000000-0000-7000-8000-000000000001",
        lessons_total=8,
        lessons_balance=balance,
        makeups_balance=makeups,
        valid_until=dt.date(2026, 8, 31),
        status="active",
        rules={**DEFAULT_RULES, **rule_overrides},
        price=Decimal(54000),
    )


RATE = Decimal(4500)


@pytest.mark.parametrize(
    "mark, lessons_delta, makeups_delta, teacher_amount",
    [
        # Таблица spec.md §4.3 при настройках школы по умолчанию.
        ("came", -1, 0, 4500),
        ("late", -1, 0, 4500),
        ("no_show", -1, 0, 4500),
        ("cancelled_early", 0, 1, 0),
        ("cancelled_late", -1, 0, 4500),
        ("cancelled_teacher", 0, 0, 0),
    ],
)
def test_default_rules_match_spec(mark, lessons_delta, makeups_delta, teacher_amount):
    effect = compute_effect(mark, sub(), RATE)
    assert (effect.lessons_delta, effect.makeups_delta, effect.teacher_amount) == (
        lessons_delta,
        makeups_delta,
        teacher_amount,
    )


def test_all_six_marks_present():
    effects = compute_all_effects(sub(), RATE)
    assert set(effects) == set(MARKS)


def test_no_show_does_not_burn_when_school_says_so():
    effect = compute_effect("no_show", sub(no_show_burns=False), RATE)
    assert effect.lessons_delta == 0
    assert effect.lessons_after == 5


def test_teacher_not_paid_for_no_show_when_school_says_so():
    effect = compute_effect("no_show", sub(pay_teacher_on_no_show=False), RATE)
    assert effect.teacher_amount == 0
    assert effect.lessons_delta == -1  # деньги преподавателя и абонемент независимы


@pytest.mark.parametrize(
    "cancel_early_effect, lessons_delta, makeups_delta",
    [("makeup", 0, 1), ("no_charge", 0, 0), ("burn", -1, 0)],
)
def test_cancel_early_effect_variants(cancel_early_effect, lessons_delta, makeups_delta):
    effect = compute_effect("cancelled_early", sub(cancel_early_effect=cancel_early_effect), RATE)
    assert (effect.lessons_delta, effect.makeups_delta) == (lessons_delta, makeups_delta)


def test_teacher_cancel_can_grant_makeup():
    assert compute_effect("cancelled_teacher", sub(teacher_cancel_effect="makeup"), RATE).makeups_delta == 1
    assert compute_effect("cancelled_teacher", sub(teacher_cancel_effect="no_charge"), RATE).makeups_delta == 0


def test_zero_balance_blocks_charge_but_not_cancellation():
    empty = sub(balance=0)
    assert compute_effect("came", empty, RATE).blocked_reason is not None
    # Отмену на пустом абонементе отметить можно: она ничего не списывает.
    assert compute_effect("cancelled_early", empty, RATE).blocked_reason is None


def test_no_subscription_means_one_off_payment():
    effect = compute_effect("came", None, RATE)
    assert effect.lessons_delta == 0
    assert effect.teacher_amount == 4500
    assert "разовой оплатой" in effect.summary


def test_rules_gaps_fall_back_to_school_defaults():
    """Абонемент с урезанным JSON правил не должен ронять отметку."""
    partial = SubscriptionState(
        id="00000000-0000-7000-8000-000000000002",
        lessons_total=8,
        lessons_balance=3,
        makeups_balance=0,
        valid_until=dt.date(2026, 8, 31),
        status="active",
        rules={},
        price=Decimal(54000),
    )
    assert compute_effect("no_show", partial, RATE).lessons_delta == -1


def test_percent_rate_counts_from_lesson_price():
    """Ставка процентом считается от стоимости одного занятия абонемента."""
    effect = compute_effect("came", sub(), rate_amount=None, rate_percent=Decimal(50))
    assert effect.teacher_amount == 54000 // 8 // 2


def test_summary_is_human_readable():
    effect = compute_effect("came", sub(), RATE)
    assert "Спишется 1 занятие, останется 4" in effect.summary
    assert "4 500 ₸" in effect.summary
