"""G23 -- a charge is for the balance, and a paid invoice is never charged.

The L3 that found this charged a paid invoice twice: ``attempt_charge`` asked
the processor for the TOTAL and read neither ``paid_at`` nor the unreversed sum,
and a SUCCESS reset the retry count so the second call was allowed. These tests
are that probe, the other way round. The control plants the total back in place
of the balance and requires red.

The outside pass added the cheque-between-reservation-and-outcome case (R1.1b):
the card payment the processor made is recorded for the amount RESERVED and the
invoice reads OVERPAID -- never dropped. Its control plants T2 to drop the
payment when the balance has moved and requires red.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import attempt_charge
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import REFUSAL_NOTHING_OWED, Refused
from monthly_billing.payment import ChargeResult, Outcome, RetryState, charge_invoice
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


class _Succeeds:
    def __init__(self) -> None:
        self.amounts: list[int] = []

    def charge(self, request):
        self.amounts.append(request.amount_minor)
        return ChargeResult(
            outcome=Outcome.SUCCESS, detail="approved", reference=f"auth-{len(self.amounts)}"
        )


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


@pytest.mark.guarantee("G23")
def test_a_paid_invoice_is_never_charged_again(app, tenant_id):
    reference = _issued(app, tenant_id)
    processor = _Succeeds()
    first = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert first.paid is not None and first.paid.paid
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_NOTHING_OWED
    assert processor.amounts == [12000], "the processor was called for the paid invoice"
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(1,)]
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(2,)], (
        "the refusal wrote a row, but nothing was attempted (two rows are one attempt)"
    )


@pytest.mark.guarantee("G23")
def test_a_part_paid_invoice_is_charged_its_remainder(app, tenant_id):
    reference = _issued(app, tenant_id)
    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 5000, _tick(0), recorded_by="op"
    )
    processor = _Succeeds()
    outcome = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert processor.amounts == [7000], "the request carried the total, not the balance"
    assert outcome.paid is not None and outcome.paid.paid
    rows = query(app, tenant_id, "SELECT method, amount_minor FROM payments ORDER BY received_at")
    assert rows == [
        (PaymentMethod.CHEQUE.value, 5000),
        (PaymentMethod.CARD.value, 7000),
    ]


@pytest.mark.guarantee("G23")
def test_a_fully_waived_invoice_is_not_charged_at_all(app, tenant_id):
    reference = _issued(app, tenant_id)
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="waive-all", agreement_id=None, invoice_reference=reference,
            kind=ExceptionKind.WAIVE_FEE, recorded_by="owner", recorded_at=_tick(0),
            amount_minor=12000,
        ),
    )
    processor = _Succeeds()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert refused.value.code == REFUSAL_NOTHING_OWED
    assert processor.amounts == [], "the processor was asked for a charge of nothing"
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(0,)]


@pytest.mark.guarantee("G23")
def test_a_paid_invoice_is_refused_before_the_count_is_consulted(app, tenant_id):
    """After a SUCCESS the invoice is paid, and a paid invoice is refused before
    the retry count is consulted; the success itself resets nothing (G22)."""
    reference = _issued(app, tenant_id)
    attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(1))
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(2))
    assert refused.value.code == REFUSAL_NOTHING_OWED
    assert query(
        app, tenant_id, "SELECT outcome FROM charge_attempts WHERE kind = 'outcome'"
    ) == [("success",)]


@pytest.mark.guarantee("G23")
def test_a_cheque_between_the_reservation_and_the_outcome_leaves_the_invoice_overpaid(
    app, tenant_id
):
    """THE OUTSIDE PASS'S R1.1b. The processor moved the money the reservation
    asked for; the card payment is recorded for the RESERVED amount and the
    invoice reads overpaid by exactly the cheque. Never silently, never dropped."""
    from store_harness import DSN, app_connection

    reference = _issued(app, tenant_id)

    class _ChequeLandsMeanwhile(_Succeeds):
        def charge(self, request):
            other = app_connection(DSN)
            try:
                record_payment(
                    other, tenant_id, reference, PaymentMethod.CHEQUE, request.amount_minor,
                    _tick(1), recorded_by="front-desk",
                )
            finally:
                other.close()
            return super().charge(request)

    outcome = attempt_charge(
        app, tenant_id, _ChequeLandsMeanwhile(), reference, recorded_by="cron", now=_tick(1)
    )
    assert outcome.result.outcome is Outcome.SUCCESS and outcome.payment_id is not None
    assert outcome.paid is not None and outcome.paid.paid
    assert outcome.paid.paid_minor == 24000 and outcome.paid.total_minor == 12000
    assert outcome.paid.overpaid_minor == 12000
    rows = query(app, tenant_id, "SELECT method, amount_minor FROM payments ORDER BY method")
    assert rows == [(PaymentMethod.CARD.value, 12000), (PaymentMethod.CHEQUE.value, 12000)]


@pytest.mark.guarantee("G23")
def test_the_pure_call_charges_the_amount_it_is_given_and_refuses_nothing():
    """No store: the amount is the caller's, the total by default, and zero or
    less is the refusal by name before the processor is called."""
    from monthly_billing.invoice import first_charge

    agreement = simple_agreement(start_day=date(2026, 1, 5))
    invoice = first_charge(GARAGE, agreement)
    processor = _Succeeds()
    result, _ = charge_invoice(processor, agreement, invoice, RetryState("inv-1"))
    assert result.outcome is Outcome.SUCCESS and processor.amounts == [invoice.total_minor]
    charge_invoice(processor, agreement, invoice, RetryState("inv-1"), amount_minor=250)
    assert processor.amounts[-1] == 250
    for nothing in (0, -1, True):
        with pytest.raises(Refused) as refused:
            charge_invoice(
                processor, agreement, invoice, RetryState("inv-1"), amount_minor=nothing
            )
        assert refused.value.code == REFUSAL_NOTHING_OWED
    assert len(processor.amounts) == 2, "a charge of nothing reached the processor"
