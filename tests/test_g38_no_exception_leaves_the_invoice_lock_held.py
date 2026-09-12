"""G38 -- no exception leaves the invoice lock held.

The branch L3 reversed a cheque twice on one connection: the second was refused
by name, as designed -- and the connection was left INSIDE its transaction
with the invoice's row lock still held, so a cheque on a second connection
waited for as long as the first caller idled. A refusal that hands the lock
back to the caller still held is a lock held across whatever the caller does
next; a long-lived library connection -- a collection worker -- is exactly that.
And the refusal was not the only raise inside a locked block: the instrument
guard raises there too, in every money event.

So the lock and the rollback are one context manager (``locked_invoice``) and
not two habits, and every money event takes the lock through it -- read from
the source: ``lock_invoice`` is called in exactly one place. Each arm below
raises inside a locked block on connection 1 and reads three things: the
connection's transaction status is IDLE, ``pg_locks`` shows no lock on
``invoices`` for its backend, and a cheque on connection 2 is recorded at once.

The control removes the rollback from the manager and requires red.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import attempt_charge, pending_attempts, resolve_attempt
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import (
    REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL,
    REFUSAL_ALREADY_REVERSED,
    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
    Refused,
)
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.payments import (
    PaymentMethod,
    ReversalReason,
    record_payment,
    record_reversal,
)
from monthly_billing.sensitive import InstrumentLike, luhn_ok
from monthly_billing.store.postgres import direct_lock_call_sites, lock_taking_names
from store_harness import DSN, app_connection, needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
BOUND = 3.0


def _card_shaped() -> str:
    body = "4" + "1" * 14
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(minutes=n)


class _Succeeds:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-9")


def _locks_held_on_invoices(owner, backend_pid: int) -> list[tuple[str, bool]]:
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT l.mode, l.granted FROM pg_locks l JOIN pg_class r ON r.oid = l.relation "
            "WHERE r.relname = 'invoices' AND l.pid = %s",
            (backend_pid,),
        )
        return cursor.fetchall()


def _a_cheque_on_another_connection_is_recorded_within(tenant_id, reference, bound) -> float:
    """How long a cheque on a fresh connection takes; inf if it did not finish
    inside the bound (blocked on a lock the other connection still holds)."""
    done = threading.Event()
    started = time.monotonic()

    def other():
        c = app_connection(DSN)
        try:
            record_payment(c, tenant_id, reference, PaymentMethod.CHEQUE, 100, _tick(9),
                           recorded_by="op-2")
            done.set()
        finally:
            c.close()

    t = threading.Thread(target=other)
    t.start()
    finished = done.wait(bound)
    elapsed = time.monotonic() - started
    return elapsed if finished else float("inf")


# ---------------------------------------------------------------------------
# The arms: every money event, raising inside its locked block.
# ---------------------------------------------------------------------------


def _reversal_refused_twice(app, tenant_id, reference):
    pid, _ = record_payment(app, tenant_id, reference, PaymentMethod.CHEQUE, 100, _tick(1),
                            recorded_by="op")
    record_reversal(app, tenant_id, pid, ReversalReason.BOUNCED_CHEQUE, _tick(2), recorded_by="op")
    with pytest.raises(Refused) as refused:
        record_reversal(app, tenant_id, pid, ReversalReason.BOUNCED_CHEQUE, _tick(3),
                        recorded_by="op")
    assert refused.value.code == REFUSAL_ALREADY_REVERSED


def _card_shaped_reference_on_a_cheque(app, tenant_id, reference):
    with pytest.raises(InstrumentLike):
        record_payment(app, tenant_id, reference, PaymentMethod.CHEQUE, 100, _tick(1),
                       recorded_by="op", processor_reference=_card_shaped())


def _adjustment_past_zero(app, tenant_id, reference):
    with pytest.raises(Refused) as refused:
        record_invoice_exception(
            app, tenant_id,
            OwnerException(id="cr-1", agreement_id=None, invoice_reference=reference,
                           kind=ExceptionKind.CREDIT, recorded_by="owner",
                           recorded_at=_tick(1), amount_minor=99999),
        )
    assert refused.value.code == REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL


def _attempt_resolved_twice(app, tenant_id, reference):
    import monthly_billing.charging as ch

    real = ch._settle
    ch._settle = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker died before T2"))
    try:
        with pytest.raises(RuntimeError):
            attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(1))
    finally:
        ch._settle = real
    (pending,) = pending_attempts(app, tenant_id, reference)
    resolve_attempt(app, tenant_id, pending, ChargeResult(outcome=Outcome.DECLINE, detail="no"),
                    recorded_by="op", now=_tick(2))
    with pytest.raises(Refused) as refused:
        resolve_attempt(app, tenant_id, pending, ChargeResult(outcome=Outcome.DECLINE, detail="no"),
                        recorded_by="op", now=_tick(3))
    assert refused.value.code == REFUSAL_ATTEMPT_ALREADY_RESOLVED


def _card_shaped_detail_from_the_processor_at_settlement(app, tenant_id, reference):
    """The guard's raise inside T2 itself: the outcome row's detail is scanned
    at the store, and a detail that passed the ChargeResult guard cannot trip
    it -- so the raise is planted at the insert, where the guard sits."""
    import monthly_billing.charging as ch

    real = ch.guarded_insert

    def refusing(cursor, table, record):
        if table == "charge_attempts" and record.get("kind") == "outcome":
            return real(cursor, table, {**record, "detail": _card_shaped()})
        return real(cursor, table, record)

    ch.guarded_insert = refusing
    try:
        with pytest.raises(InstrumentLike):
            attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(1))
    finally:
        ch.guarded_insert = real


ARMS = {
    "record_reversal refused (already reversed)": _reversal_refused_twice,
    "record_payment, the instrument guard (card-shaped reference)": (
        _card_shaped_reference_on_a_cheque
    ),
    "record_invoice_exception refused (adjustment exceeds total)": _adjustment_past_zero,
    "resolve_attempt refused (already resolved)": _attempt_resolved_twice,
    "attempt_charge T2, the instrument guard at the store": (
        _card_shaped_detail_from_the_processor_at_settlement
    ),
}


@pytest.mark.guarantee("G38")
@pytest.mark.parametrize("arm", sorted(ARMS))
def test_a_raise_inside_a_locked_block_hands_the_lock_back(owner, app, tenant_id, arm):
    """THE BRANCH L3'S l3_lock_leak, the other way, on every money event: after
    the raise, connection 1 is IDLE, holds no lock on invoices, and a cheque on
    connection 2 is recorded at once."""
    reference = _issued(app, tenant_id)
    ARMS[arm](app, tenant_id, reference)
    assert app.info.transaction_status.name == "IDLE", (
        f"connection 1 is {app.info.transaction_status.name} after the raise: the lock is held"
    )
    assert _locks_held_on_invoices(owner, app.info.backend_pid) == []
    took = _a_cheque_on_another_connection_is_recorded_within(tenant_id, reference, BOUND)
    assert took < BOUND, "the cheque on connection 2 waited on a lock the refusal left held"
    assert took < 1.0, f"the cheque took {took:.2f} s; a lock was held for part of it"


@pytest.mark.guarantee("G38")
def test_the_instrument_can_see_a_held_lock(owner, app, tenant_id):
    """The control on the three reads above: a transaction that DOES hold the
    lock is INTRANS, shows a lock on invoices, and makes the cheque wait out
    the bound -- so a green test above is a measurement, not a wrapper that
    never looked."""
    from monthly_billing.payments import invoice_uuid_for
    from monthly_billing.store.postgres import lock_invoice, tenant

    reference = _issued(app, tenant_id)
    with tenant(app, tenant_id) as cursor:
        lock_invoice(cursor, invoice_uuid_for(cursor, reference))
        assert app.info.transaction_status.name == "INTRANS"
        assert ("RowShareLock", True) in _locks_held_on_invoices(owner, app.info.backend_pid)
        assert _a_cheque_on_another_connection_is_recorded_within(tenant_id, reference, 0.5) == (
            float("inf")
        )
    app.rollback()
    assert app.info.transaction_status.name == "IDLE"
    time.sleep(0.5)  # the blocked cheque above lands once the lock is released


@pytest.mark.guarantee("G38")
def test_the_lock_is_taken_in_exactly_one_place_read_from_the_source():
    """Every money event enters ``locked_invoice``; nothing in the package calls
    ``lock_invoice`` directly except the manager itself. That is what makes
    'no exception leaves the lock held' a property of one function and not a
    habit at every raise site."""
    assert direct_lock_call_sites() == (("monthly_billing.store.postgres", "locked_invoice"),)
    assert lock_taking_names() == frozenset({"lock_invoice", "locked_invoice"})
