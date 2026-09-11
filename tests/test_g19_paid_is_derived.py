"""G19 -- paid is derived from unreversed payments against the current total,
after each of the three events, and a reversal reopens from the ORIGINAL due date.

There is no "mark paid" call in this module, and the tests here never set
``paid_at``: they record money and decisions and read what the derivation wrote.

Two controls. One plants the other reading of a reversal -- the invoice becomes
due from the day the bank said no -- and requires the due-date assertion to go
red. The other removes the derivation after an owner's adjustment and requires
the adjustment tests to go red, because that omission is the one that leaves a
credited payer reading as unpaid at the barrier.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.payments import (
    CardPaymentsEnterThroughTheCharge,
    PaymentMethod,
    ReversalReason,
    paid_state,
    record_payment,
    record_reversal,
)
from monthly_billing.store.postgres import tenant
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
MAY_8 = datetime(2026, 5, 8, 10, 0, tzinfo=TZ)
MAY_12 = datetime(2026, 5, 12, 10, 0, tzinfo=TZ)


def _issued(app, tenant_id) -> str:
    """One invoice for one payer, 12000 minor, due 2026-04-30. Its reference."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    report = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    (line,) = report.lines
    assert line.total_minor == 12000
    return line.reference


def _invoice(app, tenant_id, reference):
    ((due_at, paid_at),) = query(
        app, tenant_id, "SELECT due_at, paid_at FROM invoices WHERE reference = %s", (reference,)
    )
    return due_at, paid_at


@pytest.mark.guarantee("G19")
def test_a_covering_cheque_sets_paid_at_to_the_instant_it_was_received(app, tenant_id):
    reference = _issued(app, tenant_id)
    _, state = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8,
        recorded_by="operator", processor_reference="cheque 1043",
    )
    assert state.paid and state.paid_at == MAY_8
    assert _invoice(app, tenant_id, reference)[1] == MAY_8


@pytest.mark.guarantee("G19")
def test_a_partial_payment_leaves_it_unpaid_and_decides_nothing(app, tenant_id):
    reference = _issued(app, tenant_id)
    _, state = record_payment(
        app, tenant_id, reference, PaymentMethod.ACH, 5000, MAY_8, recorded_by="operator",
    )
    assert not state.paid and state.paid_minor == 5000 and state.total_minor == 12000
    assert _invoice(app, tenant_id, reference)[1] is None
    # Nothing else was written about it: no exception, no adjustment.
    assert query(app, tenant_id, "SELECT count(*) FROM owner_exceptions") == [(0,)]


@pytest.mark.guarantee("G19")
def test_a_reversal_reopens_the_invoice_from_its_original_due_date(app, tenant_id):
    reference = _issued(app, tenant_id)
    due_before, _ = _invoice(app, tenant_id, reference)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="operator",
    )
    state = record_reversal(
        app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_12,
        recorded_by="operator", note="returned by the bank",
    )
    assert not state.paid and state.paid_minor == 0
    due_after, paid_after = _invoice(app, tenant_id, reference)
    assert paid_after is None
    assert due_after == due_before, (
        f"the reversal moved due_at from {due_before} to {due_after}; a bounced "
        "cheque was never money and the invoice was unpaid from its billing day"
    )
    assert due_after != MAY_12


@pytest.mark.guarantee("G19")
def test_a_reversal_is_a_second_row_and_the_payment_row_is_untouched(app, tenant_id):
    reference = _issued(app, tenant_id)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="operator",
    )
    record_reversal(
        app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_12, recorded_by="operator",
    )
    assert query(app, tenant_id, "SELECT amount_minor, method FROM payments") == [
        (12000, "cheque")
    ]
    assert query(
        app, tenant_id, "SELECT reason, note FROM payment_reversals WHERE payment_id = %s",
        (payment_id,),
    ) == [("bounced_cheque", "")]


@pytest.mark.guarantee("G19")
def test_an_adjustment_lowering_the_total_to_what_was_paid_sets_paid_at(app, tenant_id):
    """THE THIRD EVENT. A partial payment, then the owner waives the rest: the
    total drops to the sum already received, and the invoice is paid from the
    instant of the decision that made it so."""
    reference = _issued(app, tenant_id)
    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 9000, MAY_8, recorded_by="operator",
    )
    assert _invoice(app, tenant_id, reference)[1] is None
    state = record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="exc-1", agreement_id=None, invoice_reference=reference,
            kind=ExceptionKind.WAIVE_FEE, recorded_by="the owner", recorded_at=MAY_12,
            note="long-standing customer", amount_minor=3000,
        ),
    )
    assert state.total_minor == 9000 and state.paid_minor == 9000
    assert state.paid and state.paid_at == MAY_12
    assert _invoice(app, tenant_id, reference)[1] == MAY_12
    lines = query(
        app, tenant_id,
        "SELECT kind, amount_minor, exception_id IS NOT NULL FROM invoice_lines "
        "ORDER BY created_at",
    )
    assert lines == [("full_period", 12000, False), ("exception_adjustment", -3000, True)]


@pytest.mark.guarantee("G19")
def test_a_total_raised_above_what_was_paid_clears_paid_at(app, tenant_id):
    """The mirror. No owner exception RAISES a total -- a waiver, a credit and a
    refund all reduce it -- so the raise is planted as a line directly, and the
    derivation must clear paid_at and leave due_at where it was."""
    from monthly_billing.payments import rederive_paid_at
    from monthly_billing.store.writes import guarded_insert

    reference = _issued(app, tenant_id)
    due_before, _ = _invoice(app, tenant_id, reference)
    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="operator",
    )
    assert _invoice(app, tenant_id, reference)[1] == MAY_8

    with tenant(app, tenant_id) as cursor:
        cursor.execute("SELECT id FROM invoices WHERE reference = %s", (reference,))
        (invoice_uuid,) = cursor.fetchone()
        cursor.execute(
            "SELECT agreement_id, agreement_version, period_start_day, period_end_day "
            "FROM invoice_lines WHERE invoice_id = %s",
            (invoice_uuid,),
        )
        agreement_uuid, version, start, end = cursor.fetchone()
        guarded_insert(
            cursor, "owner_exceptions",
            {"tenant_id": tenant_id, "invoice_id": invoice_uuid, "kind": "credit",
             "recorded_by": "test", "recorded_at": MAY_12, "note": "", "amount_minor": 1},
        )
        (exception_uuid,) = cursor.fetchone()
        guarded_insert(
            cursor, "invoice_lines",
            {"tenant_id": tenant_id, "invoice_id": invoice_uuid, "kind": "exception_adjustment",
             "label": "planted raise", "amount_minor": 500, "agreement_id": agreement_uuid,
             "agreement_version": version, "period_start_day": start, "period_end_day": end,
             "exception_id": exception_uuid},
        )
        cursor.fetchone()
        state = rederive_paid_at(cursor, str(invoice_uuid), MAY_12)
    app.commit()

    assert state.total_minor == 12500 and state.paid_minor == 12000 and not state.paid
    due_after, paid_after = _invoice(app, tenant_id, reference)
    assert paid_after is None and due_after == due_before


@pytest.mark.guarantee("G19")
def test_an_invoice_that_stays_paid_keeps_the_instant_it_became_paid(app, tenant_id):
    reference = _issued(app, tenant_id)
    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="operator",
    )
    _, state = record_payment(
        app, tenant_id, reference, PaymentMethod.ACH, 100, MAY_12, recorded_by="operator",
    )
    assert state.paid_at == MAY_8, "an overpayment is a later event, not the one that paid it"


@pytest.mark.guarantee("G19")
def test_a_card_payment_cannot_be_recorded_by_hand(app, tenant_id):
    reference = _issued(app, tenant_id)
    with pytest.raises(CardPaymentsEnterThroughTheCharge):
        record_payment(
            app, tenant_id, reference, PaymentMethod.CARD, 12000, MAY_8, recorded_by="operator",
        )
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G19")
def test_paid_state_is_read_from_the_rows_not_remembered(app, tenant_id):
    """The derivation reads the store every time: a state computed from the rows
    agrees with what the writers returned."""
    reference = _issued(app, tenant_id)
    _, written = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, MAY_8, recorded_by="operator",
    )
    with tenant(app, tenant_id) as cursor:
        cursor.execute("SELECT id FROM invoices WHERE reference = %s", (reference,))
        (invoice_uuid,) = cursor.fetchone()
        read = paid_state(cursor, str(invoice_uuid))
    app.rollback()
    assert read == written


@pytest.mark.guarantee("G19")
def test_the_command_line_records_a_cheque_and_its_bounce(app, tenant_id, capsys):
    from monthly_billing.cli import main
    from store_harness import APP_PASSWORD, DSN

    reference = _issued(app, tenant_id)
    store = ["--tenant", str(tenant_id), "--dsn",
             f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"]
    assert main([
        "record-payment", *store, "--invoice", reference, "--method", "cheque",
        "--amount-minor", "12000", "--received-at", MAY_8.isoformat(),
        "--reference", "cheque 1043", "--recorded-by", "operator",
    ]) == 0
    out = capsys.readouterr().out
    assert "invoice PAID at" in out
    payment_id = out.split("payment ")[1].split(" ")[0]
    assert _invoice(app, tenant_id, reference)[1] == MAY_8

    assert main([
        "record-reversal", *store, "--payment", payment_id, "--reason", "bounced_cheque",
        "--reversed-at", MAY_12.isoformat(), "--recorded-by", "operator",
    ]) == 0
    assert "UNPAID: 0 of 12000" in capsys.readouterr().out
    assert _invoice(app, tenant_id, reference)[1] is None

    # A hand-typed card payment is refused at the parser: the choice does not exist.
    with pytest.raises(SystemExit):
        main(["record-payment", *store, "--invoice", reference, "--method", "card",
              "--amount-minor", "1", "--received-at", MAY_8.isoformat(), "--recorded-by", "x"])
