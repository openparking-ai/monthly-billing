"""G36 -- the store-backed coverage call answers the agreement axes at the
instant asked and the payment state as of now, and the command line says so
for a past instant.

The outside pass asked ``covered_from_store(at=day 6)`` after a cheque recorded
on day 8 and got COVERED -- true of now, not of day 6. That is by design (a
bounced cheque was never money, G19), and it is now said: a description of
mechanism in the contract, and one line on the command line when ``--at`` is in
the past. The line is the falsifiable half: present for a past instant, absent
otherwise. The control plants the line away and requires red.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.cli import PAYMENT_STATE_IS_NOW, main
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import APP_PASSWORD, DSN, needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
PLATE = "CAR001"


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


@pytest.mark.guarantee("G36")
def test_the_payment_state_is_read_as_of_now_and_the_agreement_axes_at_the_instant(
    app, tenant_id
):
    reference = _issued(app, tenant_id)
    day_6 = datetime(2026, 5, 6, 9, 0, tzinfo=TZ)
    assert covered_from_store(app, tenant_id, GARAGE.id, PLATE, day_6).reason_code == (
        "UNPAID_PAST_GRACE"
    )
    record_payment(app, tenant_id, reference, PaymentMethod.CHEQUE, 12000,
                   datetime(2026, 5, 8, 10, 0, tzinfo=TZ), recorded_by="op")
    # The same instant, asked after the cheque: today's payment state.
    assert covered_from_store(app, tenant_id, GARAGE.id, PLATE, day_6).covered
    # The agreement axes ARE answered at the instant.
    before_start = datetime(2025, 12, 1, tzinfo=TZ)
    assert covered_from_store(app, tenant_id, GARAGE.id, PLATE, before_start).reason_code == (
        "NOT_STARTED"
    )


@pytest.mark.guarantee("G36")
def test_the_command_line_says_so_for_a_past_instant_and_not_otherwise(app, tenant_id, capsys):
    _issued(app, tenant_id)
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"

    def ask(at: datetime) -> str:
        argv = ["covered-in-store", "--tenant", str(tenant_id), "--garage", GARAGE.id,
                "--vehicle", PLATE, "--at", at.isoformat(), "--dsn", dsn]
        main(argv)
        return capsys.readouterr().out

    past = ask(datetime(2026, 5, 3, 9, 0, tzinfo=TZ))
    assert PAYMENT_STATE_IS_NOW in past
    later = ask(datetime.now(TZ) + timedelta(hours=1))
    assert PAYMENT_STATE_IS_NOW not in later
    assert later.splitlines()[0] in ("COVERED", "NOT COVERED")
