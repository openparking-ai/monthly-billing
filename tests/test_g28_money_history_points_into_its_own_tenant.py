"""G28 -- every money-history row points into its own tenant, by key.

The L3 inserted, as the application role inside tenant B, a payment whose
invoice_id was tenant A's: the policy's WITH CHECK looked at payments.tenant_id
only, and a foreign-key check runs past row-level security, so the row went in.
Effectless through the module -- A's derivation cannot see B's row -- but a hole
is a hole. Each of the three tables now carries a composite foreign key
(tenant_id, <parent>) to the parent's (tenant_id, id), and the same raw INSERT
is refused by that key. The control plants the single-column keys back and
requires red.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import tenant
from store_harness import needs_postgres, new_tenant, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)

CROSS_TENANT_INSERTS = {
    "payments": (
        "INSERT INTO payments (tenant_id, invoice_id, method, amount_minor, currency, "
        "received_at, recorded_by) VALUES (%s, %s, 'cheque', 1, 'USD', now(), 'raw')",
        "SELECT id FROM invoices",
        "payments_invoice_in_tenant",
    ),
    "payment_reversals": (
        "INSERT INTO payment_reversals (tenant_id, payment_id, reason, reversed_at, recorded_by) "
        "VALUES (%s, %s, 'bounced_cheque', now(), 'raw')",
        "SELECT id FROM payments",
        "payment_reversals_payment_in_tenant",
    ),
    "charge_attempts": (
        # The 0003 shape: a reservation names its attempt and carries the money.
        "INSERT INTO charge_attempts (tenant_id, invoice_id, kind, attempt_id, amount_minor, "
        "currency, occurred_at, recorded_by) "
        "VALUES (%s, %s, 'attempt', gen_random_uuid(), 1, 'USD', now(), 'raw')",
        "SELECT id FROM invoices",
        "charge_attempts_invoice_in_tenant",
    ),
}


def _tenant_a_with_a_payment(app, tenant_id) -> None:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    record_payment(app, tenant_id, line.reference, PaymentMethod.CHEQUE, 100, NOW, recorded_by="op")


@pytest.mark.guarantee("G28")
@pytest.mark.parametrize("table", sorted(CROSS_TENANT_INSERTS))
def test_a_raw_insert_cannot_point_at_another_tenants_row(owner, app, tenant_id, table):
    _tenant_a_with_a_payment(app, tenant_id)
    statement, parent_query, key = CROSS_TENANT_INSERTS[table]
    (parent_id,) = query(app, tenant_id, parent_query)[0]
    tenant_b = new_tenant(owner)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as violation:
        with tenant(app, tenant_b) as cursor:
            cursor.execute(statement, (tenant_b, parent_id))
    app.rollback()
    assert violation.value.diag.constraint_name == key
    assert query(app, tenant_b, f"SELECT count(*) FROM {table}") == [(0,)]


@pytest.mark.guarantee("G28")
@pytest.mark.parametrize("table", sorted(CROSS_TENANT_INSERTS))
def test_the_same_insert_inside_its_own_tenant_is_accepted(app, tenant_id, table):
    """Control on the instrument: the key refuses the tenant, not the row."""
    _tenant_a_with_a_payment(app, tenant_id)
    statement, parent_query, _ = CROSS_TENANT_INSERTS[table]
    (parent_id,) = query(app, tenant_id, parent_query)[0]
    with tenant(app, tenant_id) as cursor:
        cursor.execute(statement, (tenant_id, parent_id))
    app.rollback()
