"""G33 -- an invoice total never goes below zero.

The outside pass credited 15000 against a 12000 invoice: the total went to
-3000 and ``paid_at`` was set with zero payments. Now the adjustment is refused
by name, under the invoice lock, from the committed lines; exactly zero is a
fully waived invoice and is allowed. There is no database backstop -- a floor
across rows is not a CHECK -- and the contract says so. The control plants the
floor away and requires red.

On a taxed invoice the floor is the PRE-TAX total, not the taxed one: a credit
strictly between the two is refused by name. Its own control plants the
non-tax filter away -- nothing else -- and requires red: without the filter the
ceiling is the taxed total, the credit passes the guard, and the engine refuses
the negative base with an arithmetic error instead of this module's sentence.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL, Refused
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import needs_postgres, query, seed
from tax_harness import TAXED, engine_tax, issue, lines_of, pre_tax

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _credit(reference: str, amount: int, n: int = 1, kind=ExceptionKind.CREDIT) -> OwnerException:
    return OwnerException(
        id=f"adj-{n}", agreement_id=None, invoice_reference=reference, kind=kind,
        recorded_by="owner", recorded_at=NOW + timedelta(days=n), amount_minor=amount,
    )


def _total(app, tenant_id):
    return query(app, tenant_id, "SELECT sum(amount_minor), count(*) FROM invoice_lines")[0]


@pytest.mark.guarantee("G33")
@pytest.mark.parametrize(
    "kind", [ExceptionKind.CREDIT, ExceptionKind.WAIVE_FEE], ids=lambda k: k.value
)
def test_an_adjustment_past_zero_is_refused_by_name_and_lands_nothing(app, tenant_id, kind):
    reference = _issued(app, tenant_id)
    with pytest.raises(Refused) as refused:
        record_invoice_exception(app, tenant_id, _credit(reference, 15000, kind=kind))
    app.rollback()
    assert refused.value.code == REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL
    assert _total(app, tenant_id) == (12000, 1)
    assert query(app, tenant_id, "SELECT count(*) FROM owner_exceptions") == [(0,)], (
        "the refused decision was recorded anyway"
    )
    assert query(app, tenant_id, "SELECT paid_at FROM invoices") == [(None,)]


@pytest.mark.guarantee("G33")
def test_exactly_zero_is_a_fully_waived_invoice_and_is_paid_with_no_payment(app, tenant_id):
    reference = _issued(app, tenant_id)
    state = record_invoice_exception(app, tenant_id, _credit(reference, 12000))
    assert _total(app, tenant_id) == (0, 2)
    assert state.paid and state.paid_minor == 0 and state.total_minor == 0
    with pytest.raises(Refused) as refused:
        record_invoice_exception(app, tenant_id, _credit(reference, 1, n=2))
    app.rollback()
    assert refused.value.code == REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL


@pytest.mark.guarantee("G33")
def test_the_floor_is_the_committed_lines_not_the_payments(app, tenant_id):
    """A part-paid invoice can still be credited down to zero -- the floor is
    about the TOTAL. It then reads overpaid by what was paid, which is G35's."""
    reference = _issued(app, tenant_id)
    record_payment(app, tenant_id, reference, PaymentMethod.CHEQUE, 5000, NOW, recorded_by="op")
    state = record_invoice_exception(app, tenant_id, _credit(reference, 7000))
    assert state.paid and state.total_minor == 5000 and state.overpaid_minor == 0
    state = record_invoice_exception(app, tenant_id, _credit(reference, 5000, n=2))
    assert state.paid and state.total_minor == 0 and state.overpaid_minor == 5000
    with pytest.raises(Refused):
        record_invoice_exception(app, tenant_id, _credit(reference, 1, n=3))
    app.rollback()


@pytest.mark.guarantee("G33")
def test_a_credit_within_the_total_still_lands(app, tenant_id):
    """The control on the refusal: a credit that does not cross zero lands."""
    reference = _issued(app, tenant_id)
    state = record_invoice_exception(app, tenant_id, _credit(reference, 2000))
    assert _total(app, tenant_id) == (10000, 2) and not state.paid


@pytest.mark.guarantee("G33")
def test_on_a_taxed_invoice_the_floor_is_the_pre_tax_total(app, tenant_id):
    """A credit strictly between the pre-tax total and the taxed one is refused
    BY CODE, and the invoice is as it was."""
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    reference = line.reference
    tax = sum(engine_tax(12000).values())
    assert pre_tax(app, tenant_id, reference) == 12000
    credit = 12500
    assert 12000 < credit < 12000 + tax, "the fixture must sit strictly between the two"
    before = lines_of(app, tenant_id, reference)
    with pytest.raises(Refused) as refused:
        record_invoice_exception(app, tenant_id, _credit(reference, credit))
    app.rollback()
    assert refused.value.code == REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL
    assert lines_of(app, tenant_id, reference) == before
    assert query(app, tenant_id, "SELECT count(*) FROM owner_exceptions") == [(0,)]
    assert query(app, tenant_id, "SELECT paid_at FROM invoices") == [(None,)]
