"""G41 -- migration 0004 backfills every existing agreement's covered set to
its home garage, asserted against pre-migration counts, and a row it cannot
place fails the migration BY NAME.

**"MOOT ON AN EMPTY CLUSTER" IS NOT AN ARGUMENT.** The module had never been
deployed when 0004 was written, and that is exactly the premise a migration
should not rest on: the day it stops holding, a migration that skipped the
backfill leaves every existing agreement covering nothing, and the lane calls
every monthly parker NO_AGREEMENT. So the schema is built to 0003 here, rows
are seeded THROUGH 0003's shape, 0004 is applied to them, and the covered-set
rows are compared to the agreement rows that were there before -- by count and
by (version row, garage) pair, never against a literal.

**THE ORPHAN IS PLANTED AS THE OWNER.** 0003's composite key makes an agreement
whose home is not a garage of its tenant impossible through the module and
through the application role, so the test disables the constraint triggers as
the owner, writes the row, re-enables them, and applies 0004. The migration
must fail naming the agreement's external id and version, and the failed
migration must have created nothing: the whole file is one transaction.

Controls: the orphan refusal removed (the migration then fails on the foreign
key -- by constraint name, not by agreement -- which the test does not accept);
the backfill narrowed to version 1 rows (the migration's own count assertion
fires, and the seeded test goes red on it).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from fixtures import first_of_month_garage, month_end_garage
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.store.postgres import set_tenant
from monthly_billing.store.records import store_garage, store_payer
from store_harness import (
    DSN,
    app_connection,
    apply_migration,
    migrate,
    needs_postgres,
    new_tenant,
)

pytestmark = needs_postgres

HOME = month_end_garage()
OTHER = first_of_month_garage()


@pytest.fixture()
def schema_0003():
    """A fresh schema built through 0003, per test -- 0004 can be applied once."""
    owner = migrate(DSN, through="0003")
    with owner.cursor() as cursor:
        cursor.execute("SELECT to_regclass('agreement_garages')")
        assert cursor.fetchone() == (None,), "0004 was applied; the fixture stops at 0003"
    yield owner
    owner.close()


def _seed_through_0003(owner, tenant_id, *, versions: tuple[tuple[str, int, str], ...]):
    """Garages, one payer, and agreement VERSION rows written in 0003's shape --
    raw, because ``store_agreement`` now writes the 0004 table and there is no
    0004 table yet. Returns {(external_id, version): garage uuid}."""
    app = app_connection(DSN)
    with app.cursor() as cursor:
        set_tenant(cursor, tenant_id)
        garages = {g.id: store_garage(cursor, tenant_id, g) for g in (HOME, OTHER)}
        rules = {g.id: g for g in (HOME, OTHER)}
        payer = store_payer(cursor, tenant_id, "payer-acme", "Acme")
        homes = {}
        for external_id, version, garage_id in versions:
            # 0003's registration is normalised under the garage's OWN rule, as
            # the module wrote it then: folded at the month-end garage, exact at
            # the first-of-month one.
            normalised = rules[garage_id].normalise_identity("CAR001")
            cursor.execute(
                "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, "
                "spots, monthly_price_minor, start_day) VALUES (%s, %s, %s, %s, %s, 2, 1000, "
                "'2026-01-05') RETURNING id",
                (tenant_id, external_id, version, garages[garage_id], payer),
            )
            (row_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO agreement_vehicles (tenant_id, agreement_id, identity, "
                "identity_normalised) VALUES (%s, %s, 'CAR001', %s)",
                (tenant_id, row_id, normalised),
            )
            cursor.execute(
                "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
                "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT DO NOTHING",
                (tenant_id, garages[garage_id], normalised, external_id),
            )
            homes[(external_id, version)] = garages[garage_id]
    app.commit()
    app.close()
    return homes


def _owner_rows(owner, statement: str, params: tuple = ()) -> list[tuple]:
    with owner.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


@pytest.mark.guarantee("G41")
def test_every_existing_version_gets_exactly_its_home_asserted_against_the_rows_before(
    schema_0003,
):
    owner = schema_0003
    tenant_a, tenant_b = new_tenant(owner), new_tenant(owner)
    _seed_through_0003(
        owner, tenant_a,
        versions=(("ag-A", 1, HOME.id), ("ag-A", 2, HOME.id), ("ag-B", 1, OTHER.id)),
    )
    _seed_through_0003(owner, tenant_b, versions=(("ag-C", 1, OTHER.id),))
    before = _owner_rows(
        owner,
        "SELECT id, tenant_id, garage_id FROM agreements ORDER BY tenant_id, external_id, version",
    )
    assert len(before) == 4, "the premise: four version rows across two tenants"

    apply_migration(owner, "0004")

    after = _owner_rows(
        owner,
        "SELECT a.id, ag.tenant_id, ag.garage_id FROM agreement_garages ag "
        "JOIN agreements a ON a.id = ag.agreement_id "
        "ORDER BY ag.tenant_id, a.external_id, a.version",
    )
    assert after == before, "every version row got exactly one covered-set row: its home"
    assert _owner_rows(owner, "SELECT count(*) FROM agreement_garages") == [(len(before),)]
    # And the module reads them back: the lane at ag-A's home is answered by
    # ag-A version 2 through the store, covering its home and nothing else.
    # The module that reads is today's, whose loaders read the schema as it
    # ships -- 0005's registrar column included -- so the later migration is
    # applied before the read; 0004's backfill was judged above, on its own.
    apply_migration(owner, "0005")
    app = app_connection(DSN)
    try:
        answer = covered_from_store(
            app, tenant_a, HOME.id, "CAR001",
            datetime(2026, 5, 3, 9, 0, tzinfo=ZoneInfo(HOME.timezone)),
        )
        assert answer.covered and answer.agreement_id == "ag-A" and answer.agreement_version == 2
        assert "across" not in answer.reason
        elsewhere = covered_from_store(
            app, tenant_a, OTHER.id, "CAR001",
            datetime(2026, 5, 3, 9, 0, tzinfo=ZoneInfo(OTHER.timezone)),
        )
        assert elsewhere.covered and elsewhere.agreement_id == "ag-B", (
            "ag-B is homed at OTHER and holds CAR001 there; the backfill placed it"
        )
    finally:
        app.close()


@pytest.mark.guarantee("G41")
def test_the_empty_case_applies_cleanly_and_places_nothing(schema_0003):
    owner = schema_0003
    assert _owner_rows(owner, "SELECT count(*) FROM agreements") == [(0,)]
    apply_migration(owner, "0004")
    assert _owner_rows(owner, "SELECT count(*) FROM agreement_garages") == [(0,)]
    assert _owner_rows(owner, "SELECT to_regclass('agreement_garages') IS NOT NULL") == [(True,)]


@pytest.mark.guarantee("G41")
def test_a_version_whose_home_cannot_be_placed_fails_the_migration_by_name_and_writes_nothing(
    schema_0003,
):
    import psycopg

    owner = schema_0003
    tenant_id = new_tenant(owner)
    _seed_through_0003(owner, tenant_id, versions=(("ag-fine", 1, HOME.id),))
    nowhere = uuid4()
    with owner.cursor() as cursor:
        # The owner can disable the constraint triggers; the application role
        # and the module cannot write this row at all. That is the point.
        cursor.execute("ALTER TABLE agreements DISABLE TRIGGER ALL")
        cursor.execute(
            "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, spots, "
            "monthly_price_minor, start_day) SELECT %s, 'ag-orphan', 3, %s, p.id, 1, 1000, "
            "'2026-01-05' FROM payers p WHERE p.tenant_id = %s",
            (tenant_id, nowhere, tenant_id),
        )
        cursor.execute("ALTER TABLE agreements ENABLE TRIGGER ALL")
    assert _owner_rows(
        owner, "SELECT count(*) FROM agreements WHERE garage_id = %s", (nowhere,)
    ) == [(1,)], "the premise: an orphan row exists"

    with pytest.raises(psycopg.errors.RaiseException) as failed:
        apply_migration(owner, "0004")
    message = str(failed.value)
    assert "ag-orphan" in message and "version 3" in message, message
    assert str(nowhere) in message
    assert "Nothing was written" in message
    # The whole file is one transaction: no table, no rows, the schema as it was.
    assert _owner_rows(owner, "SELECT to_regclass('agreement_garages')") == [(None,)]
    assert _owner_rows(owner, "SELECT count(*) FROM agreements") == [(2,)]
    # Fix the row and the same migration applies: the refusal was about the row.
    with owner.cursor() as cursor:
        cursor.execute(
            "UPDATE agreements SET garage_id = (SELECT id FROM garages WHERE tenant_id = %s "
            "AND external_id = %s) WHERE garage_id = %s",
            (tenant_id, HOME.id, nowhere),
        )
    apply_migration(owner, "0004")
    assert _owner_rows(owner, "SELECT count(*) FROM agreement_garages") == [(2,)]


@pytest.mark.guarantee("G41")
def test_the_migration_file_carries_the_composite_tenant_key_and_the_policy(schema_0003):
    """0004's table is caught by G12 and G37 from the catalogue once applied;
    this pins that the file itself is what puts them there, so a rewrite that
    dropped either cannot pass by being applied on a schema that had them."""
    owner = schema_0003
    apply_migration(owner, "0004")
    rows = _owner_rows(
        owner,
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'agreement_garages'::regclass AND contype IN ('f', 'u') ORDER BY 1",
    )
    assert (
        "agreement_garages_garage_in_tenant",
        "FOREIGN KEY (tenant_id, garage_id) REFERENCES garages(tenant_id, id) ON DELETE RESTRICT",
    ) in rows
    assert (
        "agreement_garages_one_row_per_garage_per_version",
        "UNIQUE (tenant_id, agreement_id, garage_id)",
    ) in rows
    assert _owner_rows(
        owner,
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'agreement_garages'::regclass",
    ) == [(True, True)]
    grants = _owner_rows(
        owner,
        "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = 'monthly_billing_app' AND table_name = 'agreement_garages'",
    )
    assert grants == [("INSERT,SELECT",)], "part of a version, and a version is never edited"
