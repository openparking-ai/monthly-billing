"""G61 -- access hours are judged on dated times.

Access hours are entry no earlier than, exit no later than. The exit check
compared two times of day with no date, so a day agreement (07:00 to 18:00)
covered a stay from Monday 08:00 to Wednesday 10:00, and one from Friday 19:30
to Saturday 09:00 -- wholly outside its hours -- while a night agreement
(18:00 to 08:00) covered nothing at all: 19:00 to 21:00 was "after 08:00". The
exit limit is now ``exit_by`` on the ENTRY's local day, or on the day after it
when the hours run past midnight -- the rate engine's bounded day span, on the
garage's wall clock. At entry, with no exit known, nothing changes.

Controls, each with a plant in ``scripts/fail_controls.py``: the time-of-day
compare planted back; the next-day span removed.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.agreement import AccessHours
from monthly_billing.entitlement import CONDITION_ACCESS_HOURS_AT_EXIT, is_covered
from monthly_billing.findings import NOT_COVERED_OUTSIDE_ACCESS_HOURS
from monthly_billing.localday import zone

GARAGE = month_end_garage()  # Denver: observes daylight saving
TZ = zone(GARAGE.timezone)
DAY = AccessHours(entry_from=time(7, 0), exit_by=time(18, 0))
NIGHT = AccessHours(entry_from=time(18, 0), exit_by=time(8, 0))


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def _answer(hours: AccessHours, entered: datetime | None, at: datetime):
    return is_covered(
        garage=GARAGE,
        agreements=(simple_agreement(start_day=date(2026, 1, 5), access_hours=hours),),
        vehicle_identity="CAR001",
        at=at,
        stay_entered_at=entered,
    )


MON, TUE, WED = date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 13)
FRI, SAT = date(2026, 5, 15), date(2026, 5, 16)


@pytest.mark.guarantee("G61")
@pytest.mark.parametrize(
    ("entered", "left", "covered"),
    [
        ((MON, 8), (MON, 17), True),
        ((MON, 7), (MON, 18), True),  # both limits are inclusive
        ((MON, 8), (WED, 10), False),  # a multi-day stay: out by 18:00 Monday
        ((FRI, 19, 30), (SAT, 9), False),  # overnight, wholly outside the hours
        ((MON, 8), (TUE, 7), False),  # overnight, back before the doors open
        ((MON, 6), (MON, 9), False),  # in before the hours open
    ],
)
def test_a_day_agreement_covers_a_stay_inside_one_day_of_its_hours(entered, left, covered):
    answer = _answer(DAY, _local(*entered), _local(*left))
    assert answer.covered is covered, (entered, left, answer.reason)
    if not covered:
        assert answer.reason_code == NOT_COVERED_OUTSIDE_ACCESS_HOURS
    assert answer.unchecked == ()


@pytest.mark.guarantee("G61")
@pytest.mark.parametrize(
    ("entered", "left", "covered"),
    [
        ((MON, 19), (MON, 21), True),  # the same evening
        ((MON, 18, 30), (TUE, 7, 30), True),  # overnight, out by morning
        ((MON, 18), (TUE, 8), True),  # both limits are inclusive
        ((MON, 18, 30), (TUE, 8, 30), False),  # out after the morning limit
        ((MON, 19), (WED, 7), False),  # two nights: the limit is Tuesday 08:00
        ((MON, 10), (MON, 21), False),  # in during the day
    ],
)
def test_a_night_agreement_covers_an_evening_in_and_a_morning_out(entered, left, covered):
    answer = _answer(NIGHT, _local(*entered), _local(*left))
    assert answer.covered is covered, (entered, left, answer.reason)
    if not covered:
        assert answer.reason_code == NOT_COVERED_OUTSIDE_ACCESS_HOURS


@pytest.mark.guarantee("G61")
def test_the_limit_is_read_on_the_wall_clock_across_a_clock_change():
    """Denver springs forward on 2026-03-08 at 02:00: the night is an hour
    shorter in real time, and 'out by 08:00' still means 08:00 on the clock."""
    assert _answer(NIGHT, _local(date(2026, 3, 7), 19), _local(date(2026, 3, 8), 7, 59)).covered
    assert not _answer(
        NIGHT, _local(date(2026, 3, 7), 19), _local(date(2026, 3, 8), 8, 1)
    ).covered


@pytest.mark.guarantee("G61")
def test_at_entry_only_the_entry_half_is_judged_as_before():
    """No exit is known at entry: the entry half decides and the exit half is
    named in ``unchecked``, exactly as before this guarantee."""
    evening = _answer(NIGHT, None, _local(MON, 19))
    assert evening.covered and evening.unchecked == (CONDITION_ACCESS_HOURS_AT_EXIT,)
    early = _answer(DAY, None, _local(MON, 6))
    assert not early.covered and early.reason_code == NOT_COVERED_OUTSIDE_ACCESS_HOURS
    morning = _answer(DAY, None, _local(MON, 8))
    assert morning.covered and morning.unchecked == (CONDITION_ACCESS_HOURS_AT_EXIT,)
