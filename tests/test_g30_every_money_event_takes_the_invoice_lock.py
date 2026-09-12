"""G30 -- every money event takes the invoice's row lock first.

The outside pass recorded a cheque for 10000 and a credit of 2000 against a
12000 invoice AT ONCE, on two connections: each side derived ``paid_at`` from a
snapshot that could not see the other's row, both committed, and the invoice
sat with paid 10000 of total 10000 and ``paid_at`` NULL -- the lane read
UNPAID_PAST_GRACE for a paid invoice. Under the lock the second writer waits
and derives from the first's committed rows.

The race here is made deterministic without a barrier that would deadlock
against the lock: each derivation announces it has arrived and waits, for a
bounded time, for the other to arrive too. With the lock the first waits out
the bound alone (the second is queued on the lock) and the result is right;
with the lock planted away both arrive, both read stale, and the test is red.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import monthly_billing.payments as pm
from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import MONEY_EVENTS, events_taking_the_lock
from store_harness import DSN, app_connection, needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
DAY_10 = datetime(2026, 5, 10, 9, 0, tzinfo=TZ)
PLATE = "CAR001"


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


class _Rendezvous:
    """Each side announces and then waits, bounded, for the other."""

    def __init__(self, bound: float = 1.5) -> None:
        self.arrived = {"payment": threading.Event(), "credit": threading.Event()}
        self.bound = bound

    def wrap(self, real):
        def paid_state(cursor, invoice_uuid):
            me = threading.current_thread().name
            self.arrived[me].set()
            other = "credit" if me == "payment" else "payment"
            self.arrived[other].wait(self.bound)
            return real(cursor, invoice_uuid)

        return paid_state


@pytest.mark.guarantee("G30")
def test_a_payment_and_a_credit_at_once_leave_the_invoice_paid(app, tenant_id):
    reference = _issued(app, tenant_id)
    meet = _Rendezvous()
    real = pm.paid_state
    pm.paid_state = meet.wrap(real)
    results: dict[str, object] = {}

    def pay():
        c = app_connection(DSN)
        try:
            _, st = record_payment(
                c, tenant_id, reference, PaymentMethod.CHEQUE, 10000, NOW + timedelta(days=1),
                recorded_by="op",
            )
            results["payment"] = st
        finally:
            c.close()

    def credit():
        c = app_connection(DSN)
        try:
            results["credit"] = record_invoice_exception(
                c, tenant_id,
                OwnerException(
                    id="cr-race", agreement_id=None, invoice_reference=reference,
                    kind=ExceptionKind.CREDIT, recorded_by="owner",
                    recorded_at=NOW + timedelta(days=1), amount_minor=2000,
                ),
            )
        finally:
            c.close()

    try:
        threads = [
            threading.Thread(target=pay, name="payment"),
            threading.Thread(target=credit, name="credit"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    finally:
        pm.paid_state = real

    total, paid, paid_at = query(
        app, tenant_id,
        "SELECT (SELECT sum(amount_minor) FROM invoice_lines), "
        "(SELECT sum(amount_minor) FROM payments), (SELECT paid_at FROM invoices)",
    )[0]
    assert (total, paid) == (10000, 10000)
    assert paid_at is not None, "the rows say paid and the derived column said not"
    assert any(getattr(r, "paid", False) for r in results.values()), (
        "neither side's own derivation saw the invoice paid"
    )
    answer = covered_from_store(app, tenant_id, GARAGE.id, PLATE, DAY_10)
    assert answer.covered, answer.reason


@pytest.mark.guarantee("G30")
def test_the_rendezvous_can_fire_because_both_sides_reach_the_derivation():
    """The fixture control: without the lock both sides would arrive. Proven
    on the wrapper itself, so a green test above is not a wrapper that never
    waited."""
    meet = _Rendezvous(bound=0.05)
    calls: list[str] = []
    wrapped = meet.wrap(lambda c, i: calls.append(threading.current_thread().name))

    def side(name):
        wrapped(None, None)

    threads = [threading.Thread(target=side, name=n, args=(n,)) for n in ("payment", "credit")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(calls) == ["credit", "payment"]
    assert all(e.is_set() for e in meet.arrived.values())


@pytest.mark.guarantee("G30")
def test_every_money_event_takes_the_lock_read_from_the_source():
    """The contract's sentence is derived from this; a function added to
    MONEY_EVENTS without the lock changes the published sentence."""
    taken = events_taking_the_lock()
    assert set(taken) == {name for _, name in MONEY_EVENTS}
    assert all(taken.values()), {n for n, ok in taken.items() if not ok}
    assert len(taken) >= 5


@pytest.mark.guarantee("G30")
def test_the_source_reader_can_say_no():
    """The control on the reader: a function that plainly does not take the lock
    is reported as not taking it, so 'all True' above is a measurement."""
    import monthly_billing.store.postgres as pg

    real = pg.MONEY_EVENTS
    pg.MONEY_EVENTS = (*real, ("monthly_billing.payments", "paid_state"))
    try:
        taken = events_taking_the_lock()
    finally:
        pg.MONEY_EVENTS = real
    assert taken["paid_state"] is False
    assert taken["record_payment"] is True
