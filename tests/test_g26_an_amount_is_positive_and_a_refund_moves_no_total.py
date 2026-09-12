"""G26 -- an exception amount is positive, and a refund moves no total.

The L3 recorded a "credit" of -4000 on a paid invoice: the module accepted the
sign, the adjustment line landed as +4000 labelled "Credit", the invoice read
unpaid and the lane called the parker transient. The amount is now refused by
name below one, and the migration's CHECK is the backstop. And a refund on a
paid invoice, which the L3 showed leaving the invoice OVERPAID with no row for
the money going back, now records the decision and lands no line at all -- the
total and paid_at stay where they were.

Controls: the module's check planted away (the CHECK still refuses, so a test
asserting the NAMED refusal goes red); refund planted back into LANDS_A_LINE.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import (
    LANDS_A_LINE,
    NEEDS_AN_AMOUNT,
    ExceptionKind,
    OwnerException,
)
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE, Refused
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import tenant
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _paid_in_full(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    record_payment(
        app, tenant_id, line.reference, PaymentMethod.CHEQUE, line.total_minor,
        _at(date(2026, 5, 2)), recorded_by="op",
    )
    return line.reference


def _exception(reference: str, kind: ExceptionKind, amount: int) -> OwnerException:
    return OwnerException(
        id=f"{kind.value}-{amount}", agreement_id=None, invoice_reference=reference,
        kind=kind, recorded_by="owner", recorded_at=_at(date(2026, 5, 9)), amount_minor=amount,
    )


@pytest.mark.guarantee("G26")
@pytest.mark.parametrize("kind", sorted(NEEDS_AN_AMOUNT, key=lambda k: k.value))
@pytest.mark.parametrize("amount", [-4000, 0])
def test_a_non_positive_amount_is_refused_by_name(kind, amount):
    with pytest.raises(Refused) as refused:
        _exception("any", kind, amount)
    assert refused.value.code == REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE


@pytest.mark.guarantee("G26")
def test_the_database_refuses_the_same_row_forced_past_the_module(app, tenant_id):
    reference = _paid_in_full(app, tenant_id)
    (invoice_uuid,) = query(app, tenant_id, "SELECT id FROM invoices WHERE reference = %s",
                            (reference,))[0]
    with pytest.raises(psycopg.errors.CheckViolation) as violation:
        with tenant(app, tenant_id) as cursor:
            cursor.execute(
                "INSERT INTO owner_exceptions (tenant_id, invoice_id, kind, recorded_by, "
                "recorded_at, amount_minor) VALUES (%s, %s, 'credit', 'raw', now(), -4000)",
                (tenant_id, invoice_uuid),
            )
    app.rollback()
    assert violation.value.diag.constraint_name == "owner_exceptions_amount_is_positive"


@pytest.mark.guarantee("G26")
def test_a_positive_credit_still_lowers_the_total(app, tenant_id):
    """Control on the direction: the kind decides it, and it works."""
    reference = _paid_in_full(app, tenant_id)
    state = record_invoice_exception(
        app, tenant_id, _exception(reference, ExceptionKind.CREDIT, 4000)
    )
    assert state.total_minor == 8000 and state.paid


@pytest.mark.guarantee("G26")
def test_a_refund_records_the_decision_and_moves_no_total(app, tenant_id):
    reference = _paid_in_full(app, tenant_id)
    before = query(app, tenant_id, "SELECT paid_at FROM invoices")
    state = record_invoice_exception(
        app, tenant_id, _exception(reference, ExceptionKind.REFUND, 4000)
    )
    assert state.total_minor == 12000 and state.paid_minor == 12000 and state.paid
    assert query(app, tenant_id, "SELECT paid_at FROM invoices") == before
    assert query(app, tenant_id, "SELECT kind FROM invoice_lines") == [("full_period",)], (
        "a refund landed an adjustment line"
    )
    assert query(app, tenant_id, "SELECT kind, amount_minor FROM owner_exceptions") == [
        ("refund", 4000)
    ]
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 20))).covered


@pytest.mark.guarantee("G26")
def test_the_two_sets_say_which_kinds_land_a_line():
    assert LANDS_A_LINE < NEEDS_AN_AMOUNT
    assert NEEDS_AN_AMOUNT - LANDS_A_LINE == {ExceptionKind.REFUND}
