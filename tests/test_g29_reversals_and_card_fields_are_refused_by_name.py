"""G29 -- one reversal per payment, a reason that fits the method, card fields
only on a card: each refused BY NAME before the database has to.

The L3 reversed the same cheque twice through the command line and got a
psycopg traceback; a cheque carrying a card brand got a CheckViolation. The
database was right both times and the operator was handed a driver error for a
state the module can name. The control plants the reason table wide open and
requires red.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import attempt_charge
from monthly_billing.cli import main
from monthly_billing.findings import (
    REFUSAL_ALREADY_REVERSED,
    REFUSAL_CARD_FIELDS_WITHOUT_A_CARD,
    REFUSAL_REVERSAL_REASON_MISMATCH,
    Refused,
)
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.payments import (
    OPERATOR_RECORDED,
    REASONS_FOR_METHOD,
    PaymentMethod,
    ReversalReason,
    record_payment,
    record_reversal,
)
from store_harness import APP_PASSWORD, DSN, app_connection, needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
MAY_8 = datetime(2026, 5, 8, 10, 0, tzinfo=TZ)


class _Succeeds:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-1")


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _one_payment_of_each_method(app, tenant_id, reference: str) -> dict[PaymentMethod, object]:
    ids = {}
    ids[PaymentMethod.CHEQUE], _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 100, MAY_8, recorded_by="op"
    )
    ids[PaymentMethod.ACH], _ = record_payment(
        app, tenant_id, reference, PaymentMethod.ACH, 100, MAY_8, recorded_by="op"
    )
    ids[PaymentMethod.CARD] = attempt_charge(
        app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=MAY_8
    ).payment_id
    return ids


@pytest.mark.guarantee("G29")
def test_a_second_reversal_of_the_same_payment_is_refused_by_name_before_the_database(
    app, tenant_id
):
    """BEFORE the database has to: the second reversal never reaches the
    insert. Since the constraint is also caught by name as a backstop, a
    refusal alone would not show which layer refused -- so the inserts are
    counted, and the control that plants the module's check away turns this
    red by the second insert it then attempts."""
    import monthly_billing.payments as pm

    reference = _issued(app, tenant_id)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 100, MAY_8, recorded_by="op"
    )
    inserts: list[str] = []
    real = pm.guarded_insert

    def counting(cursor, table, record):
        inserts.append(table)
        return real(cursor, table, record)

    pm.guarded_insert = counting
    try:
        record_reversal(
            app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_8, recorded_by="op"
        )
        with pytest.raises(Refused) as refused:
            record_reversal(
                app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_8, recorded_by="op"
            )
    finally:
        pm.guarded_insert = real
    assert refused.value.code == REFUSAL_ALREADY_REVERSED
    app.rollback()
    assert inserts.count("payment_reversals") == 1, "the second reversal reached the database"
    assert query(app, tenant_id, "SELECT count(*) FROM payment_reversals") == [(1,)]


def _race_two_reversals(app, tenant_id, *, meet_before: str):
    """Two connections reverse one payment at once. ``meet_before`` is where the
    two rendezvous: at the lock (the ordinary path) or at the insert with the
    lock planted away in-process (which is how the BACKSTOP is reached)."""
    import threading

    import monthly_billing.payments as pm

    reference = _issued(app, tenant_id)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="op"
    )
    arrived = {"op-1": threading.Event(), "op-2": threading.Event()}

    def wait_for_the_other():
        me = threading.current_thread().name
        arrived[me].set()
        arrived["op-2" if me == "op-1" else "op-1"].wait(1.5)

    real_lock, real_insert = pm.lock_invoice, pm.guarded_insert
    if meet_before == "lock":
        def lock(cursor, invoice_uuid):
            wait_for_the_other()
            return real_lock(cursor, invoice_uuid)
        pm.lock_invoice = lock
    else:
        pm.lock_invoice = lambda cursor, invoice_uuid: None  # the lock planted away
        def insert(cursor, table, record):
            if table == "payment_reversals":
                wait_for_the_other()
            return real_insert(cursor, table, record)
        pm.guarded_insert = insert

    results: dict[str, object] = {}

    def reverse():
        c = app_connection(DSN)
        try:
            record_reversal(c, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_8,
                            recorded_by=threading.current_thread().name)
            results[threading.current_thread().name] = "recorded"
        except Exception as exc:  # noqa: BLE001 -- the shape of the failure is the finding
            results[threading.current_thread().name] = exc
        finally:
            c.close()

    try:
        threads = [threading.Thread(target=reverse, name=n) for n in ("op-1", "op-2")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
    finally:
        pm.lock_invoice, pm.guarded_insert = real_lock, real_insert
    return results


@pytest.mark.guarantee("G29")
def test_two_reversals_at_once_are_one_recorded_and_one_refused_by_name(app, tenant_id):
    """THE OUTSIDE PASS'S R4: the second reversal used to come back as a raw
    UniqueViolation. Under the invoice lock the second waits and then sees the
    first's row."""
    results = _race_two_reversals(app, tenant_id, meet_before="lock")
    outcomes = sorted(type(r).__name__ if isinstance(r, Exception) else r for r in results.values())
    assert outcomes == ["Refused", "recorded"], results
    refused = next(r for r in results.values() if isinstance(r, Refused))
    assert refused.code == REFUSAL_ALREADY_REVERSED
    assert query(app, tenant_id, "SELECT count(*) FROM payment_reversals") == [(1,)]


@pytest.mark.guarantee("G29")
def test_the_constraint_is_the_backstop_and_is_caught_by_name(app, tenant_id):
    """With the lock planted away in-process, both pass the check and one hits
    the UNIQUE -- which is caught BY ITS NAME and refused, never a driver
    error. The control plants the catch away and this goes red."""
    results = _race_two_reversals(app, tenant_id, meet_before="insert")
    outcomes = sorted(type(r).__name__ if isinstance(r, Exception) else r for r in results.values())
    assert outcomes == ["Refused", "recorded"], results
    assert query(app, tenant_id, "SELECT count(*) FROM payment_reversals") == [(1,)]


@pytest.mark.guarantee("G29")
def test_every_reason_fits_exactly_the_methods_the_table_says(app, tenant_id):
    """The whole matrix: each (method, reason) pair either records or is refused
    by name, and the table decides which. Derived from the enums, so a reason
    added tomorrow is exercised the day it exists."""
    reference = _issued(app, tenant_id)
    ids = _one_payment_of_each_method(app, tenant_id, reference)
    seen_fit = seen_refused = 0
    for method in PaymentMethod:
        for reason in ReversalReason:
            if reason in REASONS_FOR_METHOD[method]:
                seen_fit += 1
                continue  # the fitting reasons are recorded in the test below
            with pytest.raises(Refused) as refused:
                record_reversal(app, tenant_id, ids[method], reason, MAY_8, recorded_by="op")
            assert refused.value.code == REFUSAL_REVERSAL_REASON_MISMATCH
            app.rollback()
            seen_refused += 1
    assert seen_fit == len(ReversalReason), "a reason fits no method or two"
    assert seen_refused == len(PaymentMethod) * len(ReversalReason) - len(ReversalReason)
    assert query(app, tenant_id, "SELECT count(*) FROM payment_reversals") == [(0,)]


@pytest.mark.guarantee("G29")
@pytest.mark.parametrize("method", list(PaymentMethod), ids=lambda m: m.value)
def test_a_fitting_reason_is_recorded(app, tenant_id, method):
    reference = _issued(app, tenant_id)
    ids = _one_payment_of_each_method(app, tenant_id, reference)
    (reason, *_) = sorted(REASONS_FOR_METHOD[method], key=lambda r: r.value)
    state = record_reversal(app, tenant_id, ids[method], reason, MAY_8, recorded_by="op")
    assert not state.paid
    assert query(app, tenant_id, "SELECT reason FROM payment_reversals") == [(reason.value,)]


@pytest.mark.guarantee("G29")
@pytest.mark.parametrize("method", sorted(OPERATOR_RECORDED, key=lambda m: m.value))
def test_card_fields_on_a_payment_that_is_not_a_card_are_refused_by_name(app, tenant_id, method):
    reference = _issued(app, tenant_id)
    with pytest.raises(Refused) as refused:
        record_payment(
            app, tenant_id, reference, method, 100, MAY_8, recorded_by="op", card_last4="1234"
        )
    assert refused.value.code == REFUSAL_CARD_FIELDS_WITHOUT_A_CARD
    app.rollback()
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G29")
def test_the_command_line_prints_one_line_and_exits_two_never_a_traceback(app, tenant_id, capsys):
    reference = _issued(app, tenant_id)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 100, MAY_8, recorded_by="op"
    )
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    argv = ["record-reversal", "--tenant", str(tenant_id), "--payment", str(payment_id),
            "--reason", "bounced_cheque", "--reversed-at", "2026-05-09T10:00:00-06:00",
            "--recorded-by", "op", "--dsn", dsn]
    assert main(argv) == 0
    capsys.readouterr()
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("REFUSED") and REFUSAL_ALREADY_REVERSED in captured.err
    assert len(captured.err.strip().splitlines()) == 1
    assert "Traceback" not in captured.err + captured.out
