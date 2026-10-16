"""G13 -- a pause reaching into a paid period is refused by name.

Not credited, not ignored, and not silently applied to the unpaid part. This
module does not decide refunds; the garage owner records an exception with an
amount, which is a decision with a name and a date on it.

Refusing is also the only honest answer available: nothing in an agreement says
whether a mid-period pause is refundable, and the specification is explicit that
the owner decides these.

**THE STORE CALLS IT.** The refusal existed and only these tests called it: a
version pausing a paid month was stored without a word, the car read PAUSED on
paid days, and nothing recorded a decision. ``store_agreement`` now reads the
agreement's paid periods before any write and refuses a version that NEWLY
pauses a day inside one -- a pause the newest stored version already held took
its days out of that period's price, so keeping it is not a pause over paid
days. The store tests below carry the database mark; the arithmetic above
needs none. Control: the store's call removed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.agreement import Pause
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.cycle import period_containing, refuse_pause_over_a_paid_period
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD, Refused
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_agreement
from store_harness import needs_postgres, query, seed


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


# ---------------------------------------------------------------------------
# The store's half: the version that adds the pause is refused before any write.
# ---------------------------------------------------------------------------

STORE_GARAGE = month_end_garage()  # bills on the last day of the month, Denver
STORE_TZ = ZoneInfo(STORE_GARAGE.timezone)


def _store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=STORE_TZ)


def _version(number: int, *pauses: tuple[date, date], **overrides):
    return simple_agreement(
        version=number, start_day=date(2026, 1, 5),
        pauses=tuple(Pause(from_day=f, until_day=u) for f, u in pauses), **overrides,
    )


def _store(app, tenant_id, seeded, agreement):
    try:
        with tenant(app, tenant_id) as cursor:
            store_agreement(cursor, tenant_id, STORE_GARAGE, seeded.garage_uuid,
                            seeded.payer_uuids[agreement.payer_id], agreement,
                            now=_at(date(2026, 5, 9)))
    except BaseException:
        app.rollback()
        raise
    app.commit()


def _issue(app, tenant_id, month_day: date, *, paid: bool):
    """The run's invoice for the period containing ``month_day``, paid in full
    by cheque the day it falls due when ``paid``."""
    (line,) = run_billing(app, tenant_id, STORE_GARAGE.id, month_day,
                          now=_at(month_day.replace(day=1), 0)).lines
    assert line.outcome is RunOutcome.ISSUED, line
    if paid:
        record_payment(app, tenant_id, line.reference, PaymentMethod.CHEQUE, line.total_minor,
                       _at(month_day.replace(day=1)), recorded_by="operator")
    return line


def _counts(app, tenant_id):
    (row,) = query(
        app, tenant_id,
        "SELECT (SELECT count(*) FROM agreements), (SELECT count(*) FROM agreement_pauses), "
        "(SELECT count(*) FROM agreement_vehicles), (SELECT count(*) FROM vehicle_registrations)",
    )
    return row


@pytest.mark.guarantee("G13")
@_store_backed
def test_the_store_refuses_a_version_pausing_a_paid_month_and_writes_nothing(app, tenant_id):
    """FO-M18's sequence: May's $120 is paid; a v2 pausing May 10 to June 10
    is offered. Refused by name, naming May, and nothing is written -- the car
    is still covered on May 15, and nothing of May has been lost."""
    seeded = seed(app, tenant_id, STORE_GARAGE, (_version(1),))
    _issue(app, tenant_id, date(2026, 5, 1), paid=True)
    counts = _counts(app, tenant_id)
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, seeded, _version(2, (date(2026, 5, 10), date(2026, 6, 10))))
    assert refused.value.code == REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD
    assert "2026-04-30..2026-05-31" in str(refused.value)
    assert _counts(app, tenant_id) == counts, "the refused version wrote something"
    assert covered_from_store(app, tenant_id, STORE_GARAGE.id, "CAR001",
                              _at(date(2026, 5, 15))).covered


@pytest.mark.guarantee("G13")
@_store_backed
def test_the_store_accepts_a_pause_after_the_paid_month_and_over_an_unpaid_one(app, tenant_id):
    """The over-reach controls, must stay green: a pause that starts where the
    paid month ends stores; so does one over a month that is issued and NOT
    paid -- the guarantee is about paid periods, and nothing else changed."""
    seeded = seed(app, tenant_id, STORE_GARAGE, (_version(1),))
    _issue(app, tenant_id, date(2026, 5, 1), paid=True)
    _store(app, tenant_id, seeded, _version(2, (date(2026, 5, 31), date(2026, 6, 10))))
    _issue(app, tenant_id, date(2026, 6, 1), paid=False)
    _store(app, tenant_id, seeded, _version(3, (date(2026, 5, 31), date(2026, 6, 20))))
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (1,), (2,), (3,)
    ]


@pytest.mark.guarantee("G13")
@_store_backed
def test_a_pause_already_held_is_kept_and_only_the_days_an_extension_adds_are_judged(
    app, tenant_id
):
    """June 5 to June 20 is paused BEFORE June is invoiced, so June is priced
    without those days and then paid. A later version that keeps the pause
    (a price change) stores -- nothing newly paused. One that extends it to
    July 5 newly pauses June 20 to July 5, inside paid June: refused, naming
    June."""
    seeded = seed(app, tenant_id, STORE_GARAGE, (_version(1),))
    held = (date(2026, 6, 5), date(2026, 6, 20))
    _store(app, tenant_id, seeded, _version(2, held))
    _issue(app, tenant_id, date(2026, 6, 1), paid=True)
    _store(app, tenant_id, seeded, _version(3, held, monthly_price_minor=15000))
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, seeded, _version(4, (date(2026, 6, 5), date(2026, 7, 5))))
    assert refused.value.code == REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD
    assert "2026-06-20..2026-07-05" in str(refused.value)
    assert "2026-05-31..2026-06-30" in str(refused.value)
    assert query(app, tenant_id, "SELECT max(version) FROM agreements") == [(3,)]
