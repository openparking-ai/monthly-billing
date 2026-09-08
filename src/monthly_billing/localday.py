"""A calendar day is the garage's local day, and a billing boundary is an INSTANT.

Everything in this module that decides money divides or compares days, and every
one of those days belongs to the garage's own timezone. A garage in Phoenix and
a garage in Denver bill on the same calendar date and at different instants, and
on two days a year they are eight hours apart instead of seven.

**WHY THIS FILE EXISTS SEPARATELY, AND IT IS THE WHOLE OF THE DST GUARANTEE.**

Counting calendar days is DST-INVARIANT. March has 31 days in Denver whether or
not the clocks moved, so a proration divisor computed by a naive UTC
implementation and one computed correctly are THE SAME NUMBER. A guarantee
written as "proration divides by the actual days of the month, including a
spring-forward month" therefore passes under the implementation it exists to
catch. It is not a weak test; it is a test that cannot produce a negative result,
which this project has a name for.

**The DST-sensitive quantity is the boundary INSTANT.** Local midnight on
2026-03-08 in Denver is 07:00Z; local midnight on 2026-03-09 is 06:00Z. The
period between them is 23 hours, not 24. So the guarantee is written against
``start_instant`` and ``end_instant`` -- their UTC offsets, and the fact that a
spring-forward day is 23 hours long and a fall-back day 25 -- and the fail
control plants a boundary computed by adding a fixed 24 hours. That plant makes
the day-count assertions go on passing and the instant assertions go red, which
is the point.

**A local midnight can be SKIPPED or DOUBLED, and neither is theoretical.** Some
zones have moved their transition to midnight, so 00:00 on the transition date
does not exist there, and some have a doubled hour that includes it. `zoneinfo`
resolves both without raising -- a skipped local time resolves forward, an
ambiguous one to the FIRST occurrence by default -- which means it never tells
the caller that anything happened. ``day_start`` therefore states its resolution
explicitly with ``fold`` and the module documents which side it took, rather than
depending on a default that reads as an accident.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class UnknownTimezone(ValueError):
    """A garage named a timezone the running system does not carry."""


def zone(name: str) -> ZoneInfo:
    """The garage's zone, or a refusal that names it.

    Refused rather than defaulted to UTC. A garage whose zone is unavailable
    would otherwise bill on UTC days silently, which is wrong by up to a day at
    every boundary and invisible in every test written near the equator of the
    zone list.
    """
    if not isinstance(name, str) or not name:
        raise UnknownTimezone(
            f"a timezone must be an IANA name such as 'America/Denver', not {name!r}."
        )
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezone(
            f"{name!r} is not a timezone this system carries. It is refused rather "
            "than defaulted to UTC: a garage billed on UTC days crosses its own "
            "midnight by hours, and nothing in the output would say so."
        ) from exc


def day_start(day: date, tz: ZoneInfo) -> datetime:
    """The instant a local calendar day begins.

    ``fold=0`` is stated rather than left to the default. Where a zone's
    transition falls at midnight, the local time 00:00 is either absent or
    doubled; ``fold=0`` takes the FIRST occurrence of a doubled midnight and
    `zoneinfo` resolves an absent one forward. Both are decisions, and a decision
    that is only a default is a decision nobody made.
    """
    return datetime.combine(day, time(0, 0), tzinfo=tz).replace(fold=0)


def day_of(moment: datetime, tz: ZoneInfo) -> date:
    """Which local calendar day an instant falls on.

    Refuses a naive datetime. A naive value here would be interpreted as local
    by `astimezone`, which is right about half the time and silent about the
    rest -- and "about half the time" is how a monthly charge lands a day early.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            "an instant must carry a timezone. A naive datetime would be read as "
            "the running machine's local time, which is a property of the server "
            "and not of the garage."
        )
    return moment.astimezone(tz).date()


def days_in_month(year: int, month: int) -> int:
    """The actual number of days in that month. February divides by 28, or 29."""
    return monthrange(year, month)[1]


def last_day_of_month(year: int, month: int) -> date:
    return date(year, month, days_in_month(year, month))


def add_months(day: date, months: int, *, clamp_to_month_end: bool) -> date:
    """Move a date by whole months.

    ``clamp_to_month_end`` is what makes month-end billing free of February as a
    special case. A garage billing on the last day of the month has a billing day
    that IS "the last day", not "the 31st" -- so the 31st of January advances to
    the 28th of February and back to the 31st of March, rather than to a date
    that does not exist or sticking at 28 for the rest of the year.

    Without the clamp, the ordinary rule applies: a billing day later than the
    target month has is pulled back to that month's last day, and it does NOT
    become permanent -- the anchor day is carried separately by the caller.
    """
    total = (day.year * 12 + (day.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    if clamp_to_month_end:
        return last_day_of_month(year, month)
    return date(year, month, min(day.day, days_in_month(year, month)))


def days_between(start: date, end: date) -> int:
    """Whole calendar days from ``start`` up to but not including ``end``.

    Calendar days, deliberately, not ``(end_instant - start_instant).days``. The
    second is 22.958... days across a spring-forward month and truncates to 22,
    which is how an hour of clock change becomes a day of somebody's money.
    """
    return (end - start).days


def hours_between(start_instant: datetime, end_instant: datetime) -> float:
    """Real elapsed hours. Used only by the DST guarantee, never by pricing.

    Pricing counts days; this counts what actually elapsed, so a test can show
    that a 23-hour day and a 25-hour day both contain exactly one calendar day.
    That difference is the whole observable consequence of doing this correctly,
    and a guarantee that cannot observe it is not measuring the thing.

    **BOTH SIDES GO THROUGH UTC FIRST, AND THIS FUNCTION WAS WRONG WITHOUT IT.**
    Subtracting two aware datetimes IN THE SAME ZONE gives Python's wall-clock
    difference, not the elapsed one -- PEP 495, and it is silent. Measured here
    before it was fixed: local midnight on 2026-03-08 in Denver to local midnight
    on 2026-03-09 came back as 24.0 hours, and the period spanning the transition
    came back as 744.0 instead of 743.0, while `.astimezone(UTC)` on the same two
    values showed 07:00Z and 06:00Z.

    That is worth more than a bug fix. It is the exact failure mode the DST
    guarantee exists to catch, sitting inside the helper the guarantee was going
    to measure WITH: a naive implementation and a correct one would have agreed,
    through this function, on every number the test looked at. A measuring
    instrument that shares the defect it measures reports success.
    """
    utc = ZoneInfo("UTC")
    return (end_instant.astimezone(utc) - start_instant.astimezone(utc)).total_seconds() / 3600.0


def month_of(day: date) -> tuple[int, int]:
    return (day.year, day.month)


def next_day(day: date) -> date:
    return day + timedelta(days=1)
