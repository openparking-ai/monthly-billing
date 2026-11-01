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


#: The bare single-column keys 0001 created, each with the composite key that
#: now stands beside it (OP-7-12). Derived checks below read the catalogue; this
#: is the list the migration was written from, kept for the error message.
_CATALOGUE_BARE_KEYS = """
    SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text
    FROM pg_constraint c
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
      AND array_length(c.conkey, 1) = 1
      AND c.confrelid <> 'tenants'::regclass
    ORDER BY 1, 2
"""

_COMPOSITE_PARTNER = """
    SELECT 1 FROM pg_constraint c
    WHERE c.contype = 'f' AND c.conrelid = %s::regclass AND c.confrelid = %s::regclass
      AND array_length(c.conkey, 1) = 2
      AND (SELECT array_agg(attname::text ORDER BY attname::text) FROM pg_attribute
           WHERE attrelid = c.conrelid AND attnum = ANY(c.conkey))
          = (SELECT array_agg(n ORDER BY n) FROM unnest(ARRAY['tenant_id', %s]) n)
"""


@pytest.mark.guarantee("G28")
def test_every_bare_key_has_a_composite_tenant_key_beside_it(app, tenant_id):
    """OP-7-12. 0003 put a composite key beside every garage reference; the
    agreement, payer and invoice references 0001 made were left single-column,
    so a raw INSERT in tenant B could hang a vehicle, a pause, a mandate, an
    owner's exception or an invoice line on tenant A's agreement or invoice --
    a foreign-key check runs past row-level security. Read from the catalogue:
    every single-column key to a table other than ``tenants`` has the composite
    ``(tenant_id, <column>)`` to the same table beside it."""
    bare = query(app, tenant_id, _CATALOGUE_BARE_KEYS)
    assert bare, "the catalogue read found no key at all, so it measures nothing"
    unpaired = [
        f"{table}.{column} -> {target}"
        for table, column, target in bare
        if not query(app, tenant_id, _COMPOSITE_PARTNER, (table, target, column))
    ]
    assert unpaired == []


@pytest.mark.guarantee("G28")
def test_a_raw_exception_cannot_hang_on_another_tenants_agreement(owner, app, tenant_id):
    """The composite key itself, on the table the finding named."""
    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (agreement_uuid,) = seeded.agreement_uuids.values()
    tenant_b = new_tenant(owner)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as violation:
        with tenant(app, tenant_b) as cursor:
            cursor.execute(
                "INSERT INTO owner_exceptions (tenant_id, agreement_id, kind, recorded_by, "
                "recorded_at) VALUES (%s, %s, 'block', 'raw', now())",
                (tenant_b, agreement_uuid),
            )
    app.rollback()
    assert violation.value.diag.constraint_name == "owner_exceptions_agreement_in_tenant"


@pytest.mark.guarantee("G28")
def test_record_agreement_exception_does_not_see_another_tenants_agreement(
    owner, app, tenant_id
):
    """OP-7-12. ``record_agreement_exception`` took an agreement uuid and
    inserted, so tenant B's operator could block -- or extend grace on --
    tenant A's agreement by its uuid. It now reads the agreement under B's
    context first and raises LookupError when B cannot see it; nothing is
    written in either tenant."""
    from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
    from monthly_billing.exceptions_store import record_agreement_exception

    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    ((agreement_id, agreement_uuid),) = seeded.agreement_uuids.items()
    tenant_b = new_tenant(owner)
    block = OwnerException(
        id="x-1", agreement_id=agreement_id, invoice_reference=None,
        kind=ExceptionKind.BLOCK, recorded_by="owner-b", recorded_at=NOW,
    )
    with pytest.raises(LookupError):
        record_agreement_exception(app, tenant_b, agreement_uuid, block)
    app.rollback()
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM owner_exceptions WHERE agreement_id = %s",
                       (agreement_uuid,))
        assert cursor.fetchone() == (0,)
    # The control: in its own tenant the same call writes the row.
    record_agreement_exception(app, tenant_id, agreement_uuid, block)
    with owner.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM owner_exceptions WHERE agreement_id = %s",
                       (agreement_uuid,))
        assert cursor.fetchone() == (1,)
