"""G37 -- every garage reference is half of a composite tenant key.

The branch L3 inserted, as the application role inside tenant A, a row naming
tenant B's garage uuid -- into ``vehicle_registrations``, and into 0001's
``agreements`` at ``main`` -- and the database accepted both: a foreign-key
check runs past row-level security, and a bare ``garage_id REFERENCES
garages(id)`` asks only whether the garage exists. Inert through the module
(tenant A reads by its own garage uuid, tenant B's policy hides the row), and a
hole all the same -- the class 0002 closed for money-history rows with a
composite key. 0003 now gives ``garages`` the pair and every table with a
``garage_id`` the composite key, and the same raw insert is refused on all
three tables.

The guarantee is read from the CATALOGUE -- every column named ``garage_id``
in the schema, and for each one a foreign key of exactly ``(tenant_id,
garage_id)`` at ``garages (tenant_id, id)`` -- so a table added tomorrow with
a bare reference is caught the day it exists. The controls plant one key away
in 0003 and require red from both the catalogue read and the raw insert.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_garage, store_payer
from store_harness import needs_postgres, new_tenant, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
AT = datetime(2026, 4, 15, 12, 0, tzinfo=TZ)

#: Every table that references a garage, and the raw insert that points a
#: tenant-A row at tenant B's garage. The list is checked against the
#: catalogue below, so a table added with a garage_id and left out of here
#: fails the derived test rather than sliding past this one.
CROSS_GARAGE_INSERTS = {
    "agreements": (
        "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, spots, "
        "monthly_price_minor, start_day) VALUES (%s, 'ag-cross', 1, %s, %s, 1, 100, '2026-01-01')",
        "agreements_garage_in_tenant",
    ),
    "invoices": (
        "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
        "period_start_day, due_at) VALUES (%s, 'x-cross', %s, %s, 'USD', '2026-01-01', now())",
        "invoices_garage_in_tenant",
    ),
    "vehicle_registrations": (
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, 'xyz1', 'ag-X', now())",
        "vehicle_registrations_garage_in_tenant",
    ),
    "agreement_garages": (
        "INSERT INTO agreement_garages (tenant_id, agreement_id, garage_id) VALUES (%s, %s, %s)",
        "agreement_garages_garage_in_tenant",
    ),
}


def _params(table: str, tenant_id, garage, payer_a, agreement_a) -> tuple:
    """The positional parameters each statement above takes, with ``garage`` in
    the garage_id slot -- tenant B's for the refusal, tenant A's for the control."""
    if table == "vehicle_registrations":
        return (tenant_id, garage)
    if table == "agreements":
        return (tenant_id, garage, payer_a)
    if table == "agreement_garages":
        return (tenant_id, agreement_a, garage)
    return (tenant_id, payer_a, garage)


def _catalogue(connection, statement: str) -> list[tuple]:
    """A catalogue read on the app connection, rolled back afterwards -- no
    tenant context is needed for pg_class and pg_constraint."""
    with connection.cursor() as cursor:
        cursor.execute(statement)
        rows = cursor.fetchall()
    connection.rollback()
    return rows


def _garage_id_columns(connection) -> list[str]:
    """Every ordinary table in `public` with a column named garage_id."""
    return [
        row[0]
        for row in _catalogue(
            connection,
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND a.attname = 'garage_id' AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relname
            """,
        )
    ]


def _composite_garage_keys(connection) -> dict[str, str]:
    """table -> constraint name, for every foreign key whose columns are exactly
    (tenant_id, garage_id) and whose target is garages (tenant_id, id)."""
    return dict(
        _catalogue(
            connection,
            """
            SELECT c.conrelid::regclass::text, c.conname
            FROM pg_constraint c
            WHERE c.contype = 'f' AND c.confrelid = 'garages'::regclass
              AND (SELECT array_agg(a.attname ORDER BY k.ord)
                     FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
                  = ARRAY['tenant_id', 'garage_id']::name[]
              AND (SELECT array_agg(a.attname ORDER BY k.ord)
                     FROM unnest(c.confkey) WITH ORDINALITY AS k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.attnum)
                  = ARRAY['tenant_id', 'id']::name[]
            ORDER BY 1
            """,
        )
    )


@pytest.mark.guarantee("G37")
def test_every_garage_id_column_is_half_of_a_composite_tenant_key(app):
    """Read from the catalogue: the set of tables with a garage_id equals the
    set of tables with the composite key, and neither is empty."""
    columns = _garage_id_columns(app)
    keys = _composite_garage_keys(app)
    assert columns, "no table has a garage_id column; the migrations did not run"
    assert sorted(keys) == columns, (
        f"tables with a bare garage reference: {sorted(set(columns) - set(keys))}"
    )
    assert set(columns) == set(CROSS_GARAGE_INSERTS), (
        "a table references a garage and this test has no raw insert for it"
    )


@pytest.mark.guarantee("G37")
def test_garages_carries_the_pair_every_garage_reference_points_at(app):
    rows = _catalogue(
        app,
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'garages'::regclass AND contype = 'u' ORDER BY 1",
    )
    assert ("garages_tenant_id_id_key", "UNIQUE (tenant_id, id)") in rows


def _two_tenants_two_garages(owner, app, tenant_a):
    tenant_b = new_tenant(owner)
    with tenant(app, tenant_a) as cursor:
        garage_a = store_garage(cursor, tenant_a, GARAGE)
        payer_a = store_payer(cursor, tenant_a, "payer-a", "Payer A")
        # One agreement VERSION row in tenant A for the 0004 table's insert to
        # name -- written raw, with no covered-set rows of its own, so the
        # control insert at tenant A's garage is not a duplicate of the home row.
        cursor.execute(
            "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, spots, "
            "monthly_price_minor, start_day) "
            "VALUES (%s, 'ag-raw', 1, %s, %s, 1, 100, '2026-01-01') RETURNING id",
            (tenant_a, garage_a, payer_a),
        )
        (agreement_a,) = cursor.fetchone()
    with tenant(app, tenant_b) as cursor:
        garage_b = store_garage(cursor, tenant_b, GARAGE)
    app.commit()
    return tenant_b, garage_a, garage_b, payer_a, agreement_a


@pytest.mark.guarantee("G37")
@pytest.mark.parametrize("table", sorted(CROSS_GARAGE_INSERTS))
def test_a_raw_insert_cannot_point_at_another_tenants_garage(owner, app, tenant_id, table):
    """THE BRANCH L3'S A1.2 / ChatGPT 14, the other way: the tenant-A row naming
    tenant B's garage is refused by the database, by the composite key's name,
    on every table that references a garage."""
    tenant_b, garage_a, garage_b, payer_a, agreement_a = _two_tenants_two_garages(
        owner, app, tenant_id
    )
    statement, key = CROSS_GARAGE_INSERTS[table]
    params = _params(table, tenant_id, garage_b, payer_a, agreement_a)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as violation:
        with tenant(app, tenant_id) as cursor:
            cursor.execute(statement, params)
    app.rollback()
    assert violation.value.diag.constraint_name == key
    assert query(
        app, tenant_id, f"SELECT count(*) FROM {table} WHERE garage_id = %s", (garage_b,)
    ) == [(0,)]


@pytest.mark.guarantee("G37")
@pytest.mark.parametrize("table", sorted(CROSS_GARAGE_INSERTS))
def test_the_same_insert_at_the_tenants_own_garage_is_accepted(owner, app, tenant_id, table):
    """Control on the instrument: the key refuses the tenant, not the row."""
    _, garage_a, _, payer_a, agreement_a = _two_tenants_two_garages(owner, app, tenant_id)
    statement, _ = CROSS_GARAGE_INSERTS[table]
    params = _params(table, tenant_id, garage_a, payer_a, agreement_a)
    with tenant(app, tenant_id) as cursor:
        cursor.execute(statement, params)
    app.rollback()


@pytest.mark.guarantee("G37")
def test_the_module_still_answers_for_its_own_garage(app, tenant_id):
    """The key changes nothing the module does: a seeded agreement is stored
    and the lane is answered, through the same store_agreement and
    covered_from_store as before."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    answer = covered_from_store(app, tenant_id, GARAGE.id, "CAR000", AT)
    assert answer.covered
