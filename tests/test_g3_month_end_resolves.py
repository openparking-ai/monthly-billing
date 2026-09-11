"""G3 -- month-end means the last day the month actually has.

February is then not a special case anybody has to remember, which is the entire
reason the billing day is a PICKER OF OPTIONS rather than an integer. An integer
field would accept 31, and a garage that chose 31 would have no February billing
day at all -- so `31` is refused and `LAST_DAY_OF_MONTH` is offered instead.
"""

from __future__ import annotations

from datetime import date

import pytest

from monthly_billing.findings import REFUSAL_NO_BILLING_DAY, Refused
from monthly_billing.garage import BillingDay, Garage, IdentityRule


def garage(**overrides):
    fields = {
        "id": "g",
        "timezone": "America/Denver",
        "currency": "USD",
        "billing_day": BillingDay.LAST_DAY_OF_MONTH,
        "payment_grace_days": 5,
        "identity_rule": IdentityRule.EXACT,
    }
    fields.update(overrides)
    return Garage(**fields)


@pytest.mark.guarantee("G3")
@pytest.mark.parametrize(
    "year, month, expected",
    [
        (2026, 1, date(2026, 1, 31)),
        (2026, 2, date(2026, 2, 28)),
        (2028, 2, date(2028, 2, 29)),  # leap
        (2026, 4, date(2026, 4, 30)),
        (2026, 12, date(2026, 12, 31)),
    ],
)
def test_month_end_asks_the_calendar(year, month, expected):
    assert garage().billing_day_in(year, month) == expected


@pytest.mark.guarantee("G3")
def test_the_billing_day_is_not_a_free_integer():
    """29, 30 and 31 are refused, and the refusal says what to use instead.

    This is the control that keeps the option set an option set: without it, the
    "picker, not an integer" claim is a sentence in a docstring.
    """
    with pytest.raises(Refused) as caught:
        garage(billing_day=BillingDay.NTH_DAY_OF_MONTH, billing_day_of_month=31)
    assert caught.value.code == REFUSAL_NO_BILLING_DAY
    assert "LAST_DAY_OF_MONTH" in str(caught.value)


@pytest.mark.guarantee("G3")
def test_the_two_billing_day_fields_have_to_agree():
    """A garage stating a day number while billing month-end has said two things.

    Refused rather than resolved: guessing which one was meant puts somebody's
    charge on a date nobody chose.
    """
    with pytest.raises(Refused) as caught:
        garage(billing_day=BillingDay.LAST_DAY_OF_MONTH, billing_day_of_month=15)
    assert caught.value.code == REFUSAL_NO_BILLING_DAY

    with pytest.raises(Refused):
        garage(billing_day=BillingDay.NTH_DAY_OF_MONTH, billing_day_of_month=None)


@pytest.mark.guarantee("G3")
def test_the_first_of_the_month_exists_in_every_month():
    assert garage(billing_day=BillingDay.FIRST_DAY_OF_MONTH).billing_day_in(2026, 2) == date(
        2026, 2, 1
    )


@pytest.mark.guarantee("G3")
def test_a_month_end_period_steps_month_to_month_without_sticking_at_28():
    """The clamp is what stops 31 January becoming 28 for the rest of the year.

    A naive "same day number next month" walks 31 Jan -> 28 Feb -> 28 Mar and
    never comes back, quietly moving every subsequent billing date.
    """
    from monthly_billing.cycle import next_period_after, period_containing

    g = garage()
    period = period_containing(g, date(2026, 1, 15))
    seen = [period.start_day]
    for _ in range(4):
        period = next_period_after(g, period)
        seen.append(period.start_day)
    assert seen == [
        date(2025, 12, 31),
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
