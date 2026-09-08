"""G8 -- no mandate, no charge. And the invoice is issued anyway.

The specification says an agreement with no mandate record "cannot be billed, and
refuses by name". Read as blocking the INVOICE, that stops a garage invoicing
anybody who pays by cheque -- and the same specification asks for owner exceptions
covering bounced cheques, so cheque payers plainly exist. So the mandate gates the
CHARGE, and both halves are asserted here: the refusal, AND the invoice that is
still produced.

**THE REFUSAL HAPPENS BEFORE THE PROCESSOR IS CALLED.** Asserted against the
stub's own record of what it was asked to do, not against the return value. A
processor that has already been called cannot be un-called, and "we tried it
anyway and then complained" is how somebody gets charged on an agreement nobody
agreed to.
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.findings import REFUSAL_NO_MANDATE, REFUSAL_RETRIES_EXHAUSTED, Refused
from monthly_billing.invoice import first_charge
from monthly_billing.payment import (
    MAX_ATTEMPTS,
    Outcome,
    RetryState,
    StubProcessor,
    charge_invoice,
)


def invoice_for(agreement):
    return first_charge(month_end_garage(), agreement)


@pytest.mark.guarantee("G8")
def test_a_charge_without_a_mandate_refuses_by_name():
    agreement = simple_agreement(start_day=date(2026, 3, 10), with_mandate=False)
    processor = StubProcessor()
    with pytest.raises(Refused) as caught:
        charge_invoice(processor, agreement, invoice_for(agreement), RetryState("inv-1"))
    assert caught.value.code == REFUSAL_NO_MANDATE
    assert agreement.id in str(caught.value)


@pytest.mark.guarantee("G8")
def test_the_processor_is_never_called_when_the_mandate_is_missing():
    agreement = simple_agreement(start_day=date(2026, 3, 10), with_mandate=False)
    processor = StubProcessor()
    with pytest.raises(Refused):
        charge_invoice(processor, agreement, invoice_for(agreement), RetryState("inv-1"))
    assert processor.requests == [], (
        "the processor was called before the refusal. A charge that has been "
        "attempted cannot be un-attempted."
    )


@pytest.mark.guarantee("G8")
def test_the_invoice_is_still_issued_without_a_mandate():
    """The half that makes a cheque payer possible.

    Without this assertion, an implementation that refused to invoice as well
    would satisfy every other test in this file.
    """
    agreement = simple_agreement(start_day=date(2026, 3, 10), with_mandate=False)
    invoice = invoice_for(agreement)
    assert invoice.total_minor > 0
    assert len(invoice.lines) >= 2


@pytest.mark.guarantee("G8")
def test_with_a_mandate_the_charge_reaches_the_processor():
    """The negative control: without it, an implementation that refused every
    charge would pass this whole file."""
    agreement = simple_agreement(start_day=date(2026, 3, 10), with_mandate=True)
    processor = StubProcessor()
    result, retry = charge_invoice(
        processor, agreement, invoice_for(agreement), RetryState("inv-1")
    )
    assert len(processor.requests) == 1
    assert processor.requests[0].amount_minor == invoice_for(agreement).total_minor
    assert result.outcome is Outcome.ERROR  # the stub moves no money and says so
    assert retry.attempts == 1


@pytest.mark.guarantee("G8")
def test_the_stub_does_not_pretend_to_succeed():
    """A stub returning SUCCESS would let everything above it pass while nothing
    was ever collected, and the day a real processor arrives is the day that
    becomes visible."""
    processor = StubProcessor()
    agreement = simple_agreement(start_day=date(2026, 3, 10))
    result, _ = charge_invoice(
        processor, agreement, invoice_for(agreement), RetryState("inv-1")
    )
    assert result.outcome is not Outcome.SUCCESS
    assert "no payment processor is connected" in result.detail


@pytest.mark.guarantee("G8")
def test_three_attempts_and_then_a_refusal_by_name():
    agreement = simple_agreement(start_day=date(2026, 3, 10))
    processor = StubProcessor()
    retry = RetryState("inv-1")
    for _ in range(MAX_ATTEMPTS):
        _, retry = charge_invoice(processor, agreement, invoice_for(agreement), retry)
    assert retry.attempts == MAX_ATTEMPTS
    with pytest.raises(Refused) as caught:
        charge_invoice(processor, agreement, invoice_for(agreement), retry)
    assert caught.value.code == REFUSAL_RETRIES_EXHAUSTED


@pytest.mark.guarantee("G8")
def test_the_count_resets_only_when_the_caller_reports_a_new_payment_method():
    """This module holds no payment method, so it cannot see one being added.

    A count that reset itself on a fact it could not observe would be a count
    that never reset -- which is the shape the specification's own PCI rule
    forces, and the reason this is an input.
    """
    retry = RetryState("inv-1", attempts=MAX_ATTEMPTS)
    assert retry.exhausted
    assert retry.record_payment_method_changed().attempts == 0
    assert not retry.record_payment_method_changed().exhausted
