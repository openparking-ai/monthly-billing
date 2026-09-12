"""G27 -- ALREADY_ISSUED means the period row exists, and nothing else.

The L3 planted an invoice carrying the reference the run would derive but a
different period, and the run said "this period was already invoiced" while the
period was not: the catch was by exception class, and the reference lock had
fired. Now the run reads the period's own fact back and reports any other
constraint as its own outcome, by name, exiting non-zero.

The control plants the outcome decision to "always already issued" and requires
red.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import RunOutcome, invoice_reference, run_billing
from monthly_billing.cli import main
from monthly_billing.cycle import period_containing
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _plant_reference_collision(owner, app, tenant_id):
    """As the owner: an invoice with the reference the run will derive for the
    2026-05-31 period, sitting on a period of its own."""
    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    period = period_containing(GARAGE, date(2026, 6, 1))
    reference = invoice_reference(GARAGE, period, "payer-acme")
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
            "period_start_day, issued_at, due_at) VALUES (%s, %s, %s, %s, 'USD', %s, now(), now())",
            (tenant_id, reference, seeded.payer_uuids["payer-acme"], seeded.garage_uuid,
             date(2000, 1, 1)),
        )
    return period, reference


@pytest.mark.guarantee("G27")
def test_a_reference_collision_is_not_already_issued(owner, app, tenant_id):
    period, reference = _plant_reference_collision(owner, app, tenant_id)
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 6, 1), now=NOW).lines
    assert line.outcome is RunOutcome.CONSTRAINT_VIOLATED
    assert "invoices_tenant_id_reference_key" in line.detail
    assert "NOT known to be invoiced" in line.detail
    assert query(
        app, tenant_id, "SELECT count(*) FROM invoices WHERE period_start_day = %s",
        (period.start_day,),
    ) == [(0,)], "the period was issued after all"


@pytest.mark.guarantee("G27")
def test_the_run_exits_non_zero_on_a_constraint_that_is_not_the_period_lock(
    owner, app, tenant_id, capsys
):
    from store_harness import APP_PASSWORD, DSN

    _plant_reference_collision(owner, app, tenant_id)
    argv = ["run", "--tenant", str(tenant_id), "--garage", GARAGE.id,
            "--period-containing", "2026-06-01",
            "--dsn", f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"]
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert "CONSTRAINT_VIOLATED" in out and "ALREADY_ISSUED" not in out


@pytest.mark.guarantee("G27")
def test_a_genuine_duplicate_is_already_issued_whichever_lock_the_database_named(app, tenant_id):
    """Both locks are violated by a real duplicate and the database names the
    one it checked first (the 0001 reference lock, as measured). The run reads
    the period row back rather than trusting the name."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    assert line.outcome is RunOutcome.ALREADY_ISSUED
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(1,)]
