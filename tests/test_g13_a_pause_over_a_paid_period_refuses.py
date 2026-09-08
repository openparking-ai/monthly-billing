"""G13 -- a pause reaching into a paid period is refused by name.

Not credited, not ignored, and not silently applied to the unpaid part. This
module does not decide refunds; the garage owner records an exception with an
amount, which is a decision with a name and a date on it.

Refusing is also the only honest answer available: nothing in an agreement says
whether a mid-period pause is refundable, and the specification is explicit that
the owner decides these.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.cycle import period_containing, refuse_pause_over_a_paid_period
from monthly_billing.findings import REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD, Refused


def paid(garage, day):
    return (period_containing(garage, day),)


@pytest.mark.guarantee("G13")
def test_a_pause_inside_a_paid_period_is_refused_and_names_it():
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 1))
    period = period_containing(garage, date(2026, 4, 15))

    with pytest.raises(Refused) as caught:
        refuse_pause_over_a_paid_period(
            agreement, date(2026, 4, 10), date(2026, 4, 20), (period,)
        )
    assert caught.value.code == REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD
    assert str(period.start_day) in str(caught.value)


@pytest.mark.guarantee("G13")
def test_a_pause_wholly_after_the_paid_period_is_allowed():
    """The negative control: without it, an implementation refusing EVERY pause
    would pass the assertion above."""
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 1))
    period = period_containing(garage, date(2026, 4, 15))

    refuse_pause_over_a_paid_period(
        agreement, period.end_day, date(2026, 6, 1), (period,)
    )


@pytest.mark.guarantee("G13")
def test_the_boundaries_are_half_open_on_both_sides():
    """A pause ending exactly ON the paid period's start does not overlap it, and
    one starting exactly on its end day does not either. Off-by-one here costs a
    day of somebody's money in one direction or a false refusal in the other."""
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 1))
    period = period_containing(garage, date(2026, 4, 15))

    # Ends where the paid period begins: allowed.
    refuse_pause_over_a_paid_period(
        agreement, date(2026, 3, 1), period.start_day, (period,)
    )
    # Begins where the paid period ends: allowed.
    refuse_pause_over_a_paid_period(
        agreement, period.end_day, date(2026, 7, 1), (period,)
    )
    # One day INTO it: refused. This is the control on the two above -- without
    # it, an implementation that never overlapped anything would pass both.
    one_day_in = period.start_day + timedelta(days=1)
    with pytest.raises(Refused):
        refuse_pause_over_a_paid_period(
            agreement, date(2026, 3, 1), one_day_in, (period,)
        )
