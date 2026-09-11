"""Billing periods, proration, and the first charge.

**A PERIOD IS TWO LOCAL DAYS AND TWO INSTANTS**, and both matter for different
reasons. The DAYS decide the arithmetic -- how many there are, how many of them
an agreement was live for. The INSTANTS decide when a period actually begins and
ends in the real world, which is the half a naive implementation gets wrong and
the half a day-counting test cannot see. ``BillingPeriod`` carries both, and the
DST guarantee is written against the instants. See localday.py for why that
distinction is the whole of it.

**PRORATION IS BY ACTUAL DAYS, AND THE DIVISOR IS THE PERIOD'S OWN LENGTH.**

The specification says "actual days", and for a garage billing at month-end the
period IS a calendar month, so the divisor is the actual number of days in that
month -- February by 28, March by 31, exactly as stated.

**For any other billing day the period is not a calendar month, and "that month"
names nothing.** A garage billing on the 10th has a period running 10 January to
10 February: 31 days, which is not the length of either month it touches. So the
divisor is the actual number of days IN THE PERIOD BEING PRORATED. That reduces
to "the actual days of that month" for month-end billing, which is the case the
specification described, and it is the only reading that is defined for the
others. Recorded because it is a decision about somebody's money that the
specification did not settle.

**THE REMAINDER GOES TO THE PAYER, NOT TO THE GARAGE.** Integer division leaves
one; rounding it up would take up to one minor unit more than the days bought,
every partial period, from everybody. Down is the direction that cannot overcharge.
That is a pricing decision, so it is stated here rather than left to be
discovered in the `//`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .agreement import Agreement
from .findings import REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD, Refused
from .garage import Garage
from .localday import add_months, day_start, days_between, zone


@dataclass(frozen=True)
class BillingPeriod:
    """One period: the days it covers, and the instants it runs between.

    Half-open on both: ``start_day`` is in the period, ``end_day`` is the first
    day of the next one. ``start_instant`` and ``end_instant`` are the local
    midnights of those two days in the garage's zone -- which is why a
    spring-forward period is 23 hours shorter than a fall-back one of the same
    day count, and why a fixed 24-hour step gets both wrong.
    """

    start_day: date
    end_day: date
    start_instant: datetime
    end_instant: datetime

    @property
    def days(self) -> int:
        """The actual number of calendar days. The proration divisor."""
        return days_between(self.start_day, self.end_day)

    def contains(self, day: date) -> bool:
        return self.start_day <= day < self.end_day


def period_containing(garage: Garage, day: date) -> BillingPeriod:
    """The billing period a local day falls in.

    A period runs from one billing day up to the next. ``clamp_to_month_end`` is
    passed through from the garage's option, which is what keeps February from
    being a special case: a month-end garage's period ends on the last day of the
    following month whatever that is, rather than on a fixed date number that may
    not exist.
    """
    tz = zone(garage.timezone)
    clamp = garage.billing_day.value == "last_day_of_month"

    start = garage.next_billing_day_on_or_after(day)
    if start > day:
        # `day` is before this month's billing day, so it belongs to the period
        # that opened on the previous one.
        start = _previous_billing_day(garage, start)
    end = _next_billing_day_after(garage, start, clamp)

    return BillingPeriod(
        start_day=start,
        end_day=end,
        start_instant=day_start(start, tz),
        end_instant=day_start(end, tz),
    )


def _previous_billing_day(garage: Garage, billing_day: date) -> date:
    previous_month = add_months(date(billing_day.year, billing_day.month, 1), -1,
                                clamp_to_month_end=False)
    return garage.billing_day_in(previous_month.year, previous_month.month)


def _next_billing_day_after(garage: Garage, billing_day: date, clamp: bool) -> date:
    if clamp:
        return add_months(billing_day, 1, clamp_to_month_end=True)
    following = add_months(date(billing_day.year, billing_day.month, 1), 1,
                           clamp_to_month_end=False)
    return garage.billing_day_in(following.year, following.month)


def next_period_after(garage: Garage, period: BillingPeriod) -> BillingPeriod:
    return period_containing(garage, period.end_day)


# --------------------------------------------------------------------------
# Proration
# --------------------------------------------------------------------------


def billable_days(agreement: Agreement, period: BillingPeriod) -> int:
    """How many days of ``period`` this agreement is live and unpaused for.

    Three things remove a day, and they are counted TOGETHER rather than in
    sequence so that a day both before the start and inside a pause is removed
    once. Counting them separately is how a period ends up with negative days.
    """
    count = 0
    day = period.start_day
    while day < period.end_day:
        if day >= agreement.start_day and not agreement.is_paused_on(day):
            if (
                agreement.cancelled_effective_day is None
                or day < agreement.cancelled_effective_day
            ):
                count += 1
        day = _tomorrow(day)
    return count


def _tomorrow(day: date) -> date:
    from datetime import timedelta

    return day + timedelta(days=1)


def prorate(full_amount_minor: int, days_billed: int, days_in_period: int) -> int:
    """``full_amount`` scaled to ``days_billed`` of ``days_in_period``.

    Integer arithmetic throughout, and the multiplication happens before the
    division so the intermediate is exact. Doing it the other way -- dividing
    first -- loses the remainder before it can be scaled, which is a different
    and wrong answer for every amount that is not a multiple of the day count.

    Truncation is toward zero, which favours the payer. See the module docstring.
    """
    if days_in_period <= 0:
        raise ValueError(
            f"a billing period of {days_in_period} days cannot prorate anything. A "
            "period with no days in it is a period the calendar arithmetic got wrong, "
            "and dividing by it would be a crash or a wrong number depending on the "
            "interpreter."
        )
    if days_billed < 0 or days_billed > days_in_period:
        raise ValueError(
            f"{days_billed} billable days in a {days_in_period}-day period is outside "
            "the period. Refused rather than clamped: a clamp would turn an "
            "arithmetic error into a plausible charge."
        )
    return (full_amount_minor * days_billed) // days_in_period


def refuse_pause_over_a_paid_period(
    agreement: Agreement,
    pause_from: date,
    pause_until: date,
    paid_periods: tuple[BillingPeriod, ...],
) -> None:
    """Refuse a pause that reaches into a period somebody has already paid for.

    **THE MODULE DOES NOT DECIDE REFUNDS.** A paid period is paid; a pause that
    covers days inside one would either have to credit them, which is a refund
    decision, or silently not cover them, which is an operator believing they
    paused an account that is still being billed. Both are worse than a refusal
    naming the period, so the owner records an exception with an amount -- who,
    when, what changed, and how much -- and that is a decision with a name on it.

    Refusing is also the only answer this module can give honestly: nothing in an
    agreement says whether a mid-period pause is refundable, and the specification
    is explicit that the garage owner decides these.
    """
    for period in paid_periods:
        overlaps = pause_from < period.end_day and pause_until > period.start_day
        if overlaps:
            raise Refused(
                REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD,
                f"the pause {pause_from}..{pause_until} on agreement "
                f"{agreement.id!r} reaches into the paid period "
                f"{period.start_day}..{period.end_day}.",
            )
