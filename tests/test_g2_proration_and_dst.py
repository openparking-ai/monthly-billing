"""G2 -- proration by actual days, and a period's boundaries are INSTANTS.

**READ THE SECOND HALF FIRST, BECAUSE IT IS WHY THIS FILE IS SHAPED LIKE THIS.**

Counting calendar days is DST-invariant. March has 31 days in Denver whether or
not the clocks moved, so a divisor computed by a naive UTC implementation and one
computed correctly are the same number. A guarantee written as "proration divides
by the actual days of the month, including a spring-forward month" therefore
passes under the very implementation it exists to catch.

So the DST assertions here are on the BOUNDARY INSTANTS -- their UTC offsets, and
the real elapsed hours between them. A spring-forward period is 743 hours and a
fall-back one 721, while both still contain a whole number of calendar days. The
fail control plants a boundary computed by adding a fixed 24 hours per day: the
day-count assertions go on passing and these go red, which is the whole point.

`hours_between` goes through UTC, and it had to be fixed to. Same-zone
subtraction in Python is wall-clock arithmetic, so it originally reported 24
hours for a 23-hour day and 744 for a 743-hour period -- a measuring instrument
carrying the defect it was built to measure.
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import (
    FALL_BACK_2026,
    FIXED_ZONE,
    SHIFTING_ZONE,
    SPRING_FORWARD_2026,
    month_end_garage,
    simple_agreement,
    tenth_of_month_garage,
)
from monthly_billing.cycle import billable_days, period_containing, prorate
from monthly_billing.localday import days_in_month, hours_between


@pytest.mark.guarantee("G2")
def test_the_divisor_is_the_actual_days_of_the_period():
    """February divides by 28 and March by 31, for a month-end garage."""
    garage = month_end_garage()
    february = period_containing(garage, date(2026, 2, 10))
    march = period_containing(garage, date(2026, 3, 10))
    assert february.days == 28
    assert march.days == 31
    assert february.days == days_in_month(2026, 2)


@pytest.mark.guarantee("G2")
def test_a_leap_february_divides_by_twenty_nine():
    """2028 is a leap year. Without this the 28 above could be a hard-coded 28."""
    garage = month_end_garage()
    february = period_containing(garage, date(2028, 2, 10))
    assert february.days == 29


@pytest.mark.guarantee("G2")
def test_a_period_that_is_not_a_calendar_month_divides_by_its_own_length():
    """The 10th-of-the-month case, which "the actual days of that month" does not
    describe: 10 January to 10 February is 31 days, and that is neither month."""
    garage = tenth_of_month_garage()
    period = period_containing(garage, date(2026, 1, 15))
    assert (period.start_day, period.end_day) == (date(2026, 1, 10), date(2026, 2, 10))
    assert period.days == 31


@pytest.mark.guarantee("G2")
def test_the_spring_forward_period_is_really_an_hour_shorter():
    """THE ASSERTION A NAIVE UTC IMPLEMENTATION FAILS.

    Same day count, different elapsed time, different UTC offsets at the two
    boundaries. Nothing about the day count can see this.
    """
    garage = month_end_garage(timezone=SHIFTING_ZONE)
    period = period_containing(garage, SPRING_FORWARD_2026)
    assert period.days == 31
    assert hours_between(period.start_instant, period.end_instant) == 743.0
    assert period.start_instant.utcoffset() != period.end_instant.utcoffset()


@pytest.mark.guarantee("G2")
def test_the_fall_back_period_is_really_an_hour_longer():
    garage = month_end_garage(timezone=SHIFTING_ZONE)
    period = period_containing(garage, FALL_BACK_2026)
    assert period.days == 30
    assert hours_between(period.start_instant, period.end_instant) == 721.0


@pytest.mark.guarantee("G2")
def test_a_zone_that_does_not_shift_is_the_negative_control():
    """Without this, arithmetic that made EVERY period an hour short would satisfy
    the two assertions above and read as a working DST measurement."""
    garage = month_end_garage(timezone=FIXED_ZONE)
    period = period_containing(garage, SPRING_FORWARD_2026)
    assert period.days == 31
    assert hours_between(period.start_instant, period.end_instant) == 744.0
    assert period.start_instant.utcoffset() == period.end_instant.utcoffset()


@pytest.mark.guarantee("G2")
def test_proration_multiplies_before_it_divides():
    """Dividing first loses the remainder before it can be scaled.

    12000 over 15 of 31 days is 5806 the right way round and 5805 the wrong one --
    a minor unit, every part month, on every agreement.
    """
    assert prorate(12000, 15, 31) == 5806
    assert (12000 // 31) * 15 == 5805  # what the other order would have produced


@pytest.mark.guarantee("G2")
def test_the_remainder_favours_the_payer():
    """Truncation toward zero, stated as a pricing decision rather than a `//`.

    Rounding up would take up to one minor unit more than the days bought, every
    partial period, from everybody.
    """
    assert prorate(100, 1, 3) == 33
    assert prorate(100, 2, 3) == 66


@pytest.mark.guarantee("G2")
def test_billable_days_removes_a_day_once_when_two_reasons_apply():
    """A day both before the start AND inside a pause is removed ONCE.

    The period runs 2026-02-28 to 2026-03-31, which is 31 days. The agreement
    starts on 2026-03-05, so five days are before it. The pause covers
    2026-02-28 to 2026-03-10, which is ten days and CONTAINS those five.

    The union is ten days, so 21 are billable. An implementation that subtracted
    the two reasons in sequence would answer 16 -- five days of somebody's money,
    from a bug that looks like careful accounting.
    """
    from monthly_billing.agreement import Pause

    garage = month_end_garage()
    period = period_containing(garage, date(2026, 3, 10))
    assert (period.start_day, period.end_day) == (date(2026, 2, 28), date(2026, 3, 31))

    agreement = simple_agreement(
        start_day=date(2026, 3, 5),
        pauses=(Pause(from_day=date(2026, 2, 28), until_day=date(2026, 3, 10)),),
    )
    assert billable_days(agreement, period) == 21


@pytest.mark.guarantee("G2")
def test_prorate_refuses_more_days_than_the_period_has():
    """Refused rather than clamped: a clamp turns an arithmetic error into a
    plausible charge, which is the failure mode that survives review."""
    with pytest.raises(ValueError, match="outside the period"):
        prorate(12000, 32, 31)
