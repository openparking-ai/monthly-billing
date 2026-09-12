"""G35 -- overpayment is a RECORDED FACT and never acted on.

The outside pass recorded a 30000 cheque against a 12000 invoice: accepted,
PAID, and the excess was a fact nowhere. It is now on the paid state, printed
by the command line, and still not acted on -- a cheque is what it is, a refund
is the owner's exception. The control plants ``overpaid_minor`` to zero and
requires red.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.cli import main
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import APP_PASSWORD, DSN, needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


@pytest.mark.guarantee("G35")
def test_a_cheque_past_the_total_is_accepted_and_the_excess_is_on_the_state(app, tenant_id):
    reference = _issued(app, tenant_id)
    _, state = record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 30000, NOW + timedelta(days=1),
        recorded_by="op",
    )
    assert state.paid and state.paid_minor == 30000 and state.total_minor == 12000
    assert state.overpaid_minor == 18000
    assert query(app, tenant_id, "SELECT count(*) FROM invoice_lines") == [(1,)], (
        "the module acted on the overpayment"
    )


@pytest.mark.guarantee("G35")
def test_an_exact_payment_is_not_overpaid_and_the_command_line_says_neither_more_nor_less(
    app, tenant_id, capsys
):
    reference = _issued(app, tenant_id)
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"

    def record(amount: int) -> str:
        argv = ["record-payment", "--tenant", str(tenant_id), "--invoice", reference,
                "--method", "cheque", "--amount-minor", str(amount),
                "--received-at", "2026-05-01T10:00:00-06:00", "--recorded-by", "op", "--dsn", dsn]
        assert main(argv) == 0
        return capsys.readouterr().out

    exact = record(12000)
    assert "invoice PAID at" in exact and "overpaid" not in exact
    more = record(1)
    assert "invoice PAID at" in more and "overpaid by 1 minor" in more
