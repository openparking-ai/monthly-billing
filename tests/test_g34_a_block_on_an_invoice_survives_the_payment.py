"""G34 -- a block recorded against an INVOICE is lifted by an unblock, never
by a payment.

The outside pass recorded a block against an invoice, paid the invoice, and
watched the lane answer COVERED with the block still the only exception on the
books: invoice-attached exceptions were read only while the invoice was
unpaid. Blocks and unblocks are now read whatever the paid state; a grace
extension on an invoice is still read only while that invoice is unpaid. The
control plants the old predicate back and requires red.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import NOT_COVERED_BLOCKED_BY_OWNER
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
PLATE = "CAR001"


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _on_invoice(reference: str, kind: ExceptionKind, n: int, **extra) -> OwnerException:
    return OwnerException(
        id=f"ex-{n}", agreement_id=None, invoice_reference=reference, kind=kind,
        recorded_by="owner", recorded_at=NOW + timedelta(days=n), **extra,
    )


def _ask(app, tenant_id, day: int):
    return covered_from_store(app, tenant_id, GARAGE.id, PLATE, NOW + timedelta(days=day))


@pytest.mark.guarantee("G34")
def test_a_block_on_the_invoice_holds_after_the_invoice_is_paid(app, tenant_id):
    reference = _issued(app, tenant_id)
    record_invoice_exception(app, tenant_id, _on_invoice(reference, ExceptionKind.BLOCK, 1))
    assert _ask(app, tenant_id, 2).reason_code == NOT_COVERED_BLOCKED_BY_OWNER
    record_payment(app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, NOW + timedelta(days=3),
                   recorded_by="op")
    after = _ask(app, tenant_id, 4)
    assert after.reason_code == NOT_COVERED_BLOCKED_BY_OWNER, "a cheque lifted the owner's block"
    record_invoice_exception(app, tenant_id, _on_invoice(reference, ExceptionKind.UNBLOCK, 5))
    assert _ask(app, tenant_id, 6).covered


@pytest.mark.guarantee("G34")
def test_a_grace_extension_on_a_paid_invoice_is_not_read(app, tenant_id):
    """The other half of the predicate, and the control that the block test is
    not green because every invoice exception is now read forever: an
    extension on an invoice that then bounces back to unpaid IS read again,
    and one on an invoice that stays paid decides nothing."""
    from monthly_billing.payments import ReversalReason, record_reversal

    reference = _issued(app, tenant_id)
    payment_id, _ = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, NOW + timedelta(days=1),
        recorded_by="op",
    )
    record_invoice_exception(
        app, tenant_id, _on_invoice(reference, ExceptionKind.EXTEND_GRACE, 2, extra_grace_days=10)
    )
    # Paid: covered on day 20 whatever the extension says.
    assert _ask(app, tenant_id, 20).covered
    # The cheque bounces: unpaid from the ORIGINAL due date (day 0), grace 5 + 10.
    record_reversal(app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE,
                    NOW + timedelta(days=3), recorded_by="op")
    assert _ask(app, tenant_id, 14).covered, "the extension on the reopened invoice was not read"
    assert _ask(app, tenant_id, 16).reason_code == "UNPAID_PAST_GRACE"
