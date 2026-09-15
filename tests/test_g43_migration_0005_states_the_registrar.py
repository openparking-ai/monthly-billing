"""G43 -- migration 0005 states the registrar on every existing agreement
version -- this module -- asserted against the pre-migration count, and a
default that left any version stating otherwise fails the whole file.

**"MOOT ON AN EMPTY CLUSTER" IS NOT AN ARGUMENT**, as 0004 said. Every
registration row that exists when 0005 runs was written by this module, so
every version row must say so, and a stored agreement whose registrations
nothing writes -- the row says OUTSIDE, no outside registrar exists -- would
be the shape in which cars silently stop being registered. So the schema is
built to 0004 here, rows are seeded THROUGH 0004's shape, 0005 is applied to
them, and every version is compared to the count that was there before.

**THE WRONG DEFAULT IS PLANTED AS THE OWNER.** A test cannot change the file
the module ships, so it applies a COPY of 0005 with the default flipped, and
requires the migration's own count assertion to fail it by count and roll the
column back -- then applies the real one. That is the control that the
assertion is live, and the fail control that removes the assertion reddens it.

Controls: the default flipped to OUTSIDE (the count assertion fires and the
seeded test goes red on it); the count assertion removed (the planted copy
applies cleanly, which the test does not accept).
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import first_of_month_garage, month_end_garage, outside_registrar_agreement
from monthly_billing.store.postgres import set_tenant, tenant
from monthly_billing.store.records import store_agreement, store_garage, store_payer
from store_harness import (
    BLIND_OWNER,
    DSN,
    app_connection,
    apply_migration,
    as_blind_owner,
    cluster_lock,
    migrate,
    migration_path,
    needs_postgres,
    new_tenant,
)

pytestmark = needs_postgres

HOME = month_end_garage()
OTHER = first_of_month_garage()


@pytest.fixture()
def schema_0004():
    """A fresh schema built through 0004, per test -- 0005 can be applied once."""
    owner = migrate(DSN, through="0004")
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'agreements' AND column_name = 'registrar'"
        )
        assert cursor.fetchone() == (0,), "0005 was applied; the fixture stops at 0004"
    yield owner
    owner.close()


def _seed_through_0004(owner, tenant_id, *, versions: tuple[tuple[str, int, str], ...]):
    """Garages, one payer, and agreement VERSION rows written in 0004's shape --
    raw, because ``store_agreement`` now writes the 0005 column and there is no
    0005 column yet."""
    app = app_connection(DSN)
    with app.cursor() as cursor:
        set_tenant(cursor, tenant_id)
        garages = {g.id: store_garage(cursor, tenant_id, g) for g in (HOME, OTHER)}
        rules = {g.id: g for g in (HOME, OTHER)}
        payer = store_payer(cursor, tenant_id, "payer-acme", "Acme")
        for external_id, version, garage_id in versions:
            normalised = rules[garage_id].normalise_identity("CAR001")
            cursor.execute(
                "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, "
                "spots, monthly_price_minor, start_day) VALUES (%s, %s, %s, %s, %s, 2, 1000, "
                "'2026-01-05') RETURNING id",
                (tenant_id, external_id, version, garages[garage_id], payer),
            )
            (row_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO agreement_garages (tenant_id, agreement_id, garage_id) "
                "VALUES (%s, %s, %s)",
                (tenant_id, row_id, garages[garage_id]),
            )
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
    app.commit()
    app.close()


def _owner_rows(owner, statement: str, params: tuple = ()) -> list[tuple]:
    with owner.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


def _apply_text(owner, text: str) -> None:
    """Apply migration TEXT as the owner, under the cluster lock, ending an
    aborted transaction so the failure is the migration's own message."""
    with cluster_lock(DSN), owner.cursor() as cursor:
        try:
            cursor.execute(text)
        except Exception:
            cursor.execute("ROLLBACK")
            raise


@pytest.mark.guarantee("G43")
def test_every_existing_version_states_this_module_asserted_against_the_rows_before(
    schema_0004,
):
    owner = schema_0004
    tenant_a, tenant_b = new_tenant(owner), new_tenant(owner)
    _seed_through_0004(
        owner, tenant_a,
        versions=(("ag-A", 1, HOME.id), ("ag-A", 2, HOME.id), ("ag-B", 1, OTHER.id)),
    )
    _seed_through_0004(owner, tenant_b, versions=(("ag-C", 1, OTHER.id),))
    before = _owner_rows(owner, "SELECT id FROM agreements ORDER BY id")
    assert len(before) == 4, "the premise: four version rows across two tenants"

    apply_migration(owner, "0005")

    after = _owner_rows(
        owner, "SELECT id FROM agreements WHERE registrar = 'this_module' ORDER BY id"
    )
    assert after == before, "every version row states this module -- no more, no fewer"
    assert _owner_rows(owner, "SELECT count(*) FROM agreements WHERE registrar IS NULL") == [(0,)]
    # The column is the ROW'S, not a constant: an outside registrar's agreement
    # stored by the module after the migration reads back OUTSIDE, and a raw
    # insert that says nothing reads this module.
    app = app_connection(DSN)
    try:
        with tenant(app, tenant_a) as cursor:
            cursor.execute(
                "SELECT id, external_id FROM garages WHERE external_id IN (%s, %s)",
                (HOME.id, OTHER.id),
            )
            garages = {ext: uuid for uuid, ext in cursor.fetchall()}
            cursor.execute("SELECT id FROM payers LIMIT 1")
            (payer,) = cursor.fetchone()
            store_agreement(
                cursor, tenant_a, HOME, garages[HOME.id], payer,
                outside_registrar_agreement(start_day=date(2026, 1, 5)),
            )
            cursor.execute(
                "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, "
                "spots, monthly_price_minor, start_day) VALUES (%s, 'ag-raw', 1, %s, %s, 1, 1000, "
                "'2026-01-05')",
                (tenant_a, garages[HOME.id], payer),
            )
            cursor.execute(
                "SELECT external_id, registrar FROM agreements "
                "WHERE external_id IN ('ag-outside', 'ag-raw') ORDER BY external_id"
            )
            assert cursor.fetchall() == [("ag-outside", "outside"), ("ag-raw", "this_module")]
        app.rollback()
    finally:
        app.close()


@pytest.mark.guarantee("G43")
def test_a_role_that_cannot_see_every_row_is_refused_by_name_and_the_flipped_default_too(
    schema_0004,
):
    """The L3's F-A: under an owner that is neither superuser nor BYPASSRLS the
    count check compared 0 with 0, and the FLIPPED default applied, leaving
    every version reading 'outside' on disk. The file now refuses that role by
    name before its BEGIN -- shipped and flipped alike, because the refusal is
    about who is reading, not what the default says -- and writes nothing."""
    import psycopg

    owner = schema_0004
    tenant_id = new_tenant(owner)
    _seed_through_0004(owner, tenant_id, versions=(("ag-A", 1, HOME.id), ("ag-B", 1, OTHER.id)))
    shipped = migration_path("0005").read_text()
    flipped = shipped.replace("DEFAULT 'this_module'", "DEFAULT 'outside'")
    assert flipped != shipped
    with as_blind_owner(owner) as blind:
        blind.execute("SELECT count(*) FROM agreements")
        assert blind.fetchone() == (0,), "the control: the blind owner sees no version row"
        for text in (shipped, flipped):
            with cluster_lock(DSN), pytest.raises(psycopg.errors.RaiseException) as refused:
                try:
                    blind.execute(text)
                except Exception:
                    blind.execute("ROLLBACK")
                    raise
            message = str(refused.value)
            assert f"running as role {BLIND_OWNER}" in message and "BYPASSRLS" in message
            assert "Nothing was written" in message
    assert _owner_rows(
        owner,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'agreements' AND column_name = 'registrar'",
    ) == [(0,)]
    # The real owner sees both rows; the shipped file states both.
    apply_migration(owner, "0005")
    assert _owner_rows(
        owner, "SELECT count(*) FROM agreements WHERE registrar = 'this_module'"
    ) == [(2,)]


@pytest.mark.guarantee("G43")
def test_the_empty_case_applies_cleanly_and_states_nothing(schema_0004):
    owner = schema_0004
    assert _owner_rows(owner, "SELECT count(*) FROM agreements") == [(0,)]
    apply_migration(owner, "0005")
    assert _owner_rows(
        owner,
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'agreements' AND column_name = 'registrar'",
    ) == [("NO", "'this_module'::text")]


@pytest.mark.guarantee("G43")
def test_a_default_that_leaves_a_version_stating_otherwise_fails_by_count_and_writes_nothing(
    schema_0004,
):
    import psycopg

    owner = schema_0004
    tenant_id = new_tenant(owner)
    _seed_through_0004(owner, tenant_id, versions=(("ag-A", 1, HOME.id), ("ag-B", 1, OTHER.id)))

    shipped = migration_path("0005").read_text()
    anchor = "DEFAULT 'this_module'"
    assert shipped.count(anchor) == 1, "the plant's anchor is the shipped default, once"
    planted = shipped.replace(anchor, "DEFAULT 'outside'")

    with pytest.raises(psycopg.errors.RaiseException) as failed:
        _apply_text(owner, planted)
    message = str(failed.value)
    assert "2 of 2 agreement version(s)" in message, message
    assert "Nothing is committed" in message
    # The whole file is one transaction: no column, the schema as it was.
    assert _owner_rows(
        owner,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'agreements' AND column_name = 'registrar'",
    ) == [(0,)]
    # The shipped file applies to the same rows, and states this module for both.
    apply_migration(owner, "0005")
    assert _owner_rows(
        owner, "SELECT count(*) FROM agreements WHERE registrar = 'this_module'"
    ) == [(2,)]


@pytest.mark.guarantee("G43")
def test_the_column_is_stated_or_refused_and_the_door_needs_no_new_grant(schema_0004):
    """The CHECK is the database's backstop for the enum; and 0003's grant on
    ``vehicle_registrations`` is what the door writes through -- 0005 adds no
    grant, and the grant table is the same before and after it."""
    import psycopg

    owner = schema_0004
    grants = (
        "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = 'monthly_billing_app' AND table_name = 'vehicle_registrations'"
    )
    before = _owner_rows(owner, grants)
    apply_migration(owner, "0005")
    assert _owner_rows(owner, grants) == before == [("DELETE,INSERT,SELECT,UPDATE",)]
    tenant_id = new_tenant(owner)
    app = app_connection(DSN)
    try:
        with tenant(app, tenant_id) as cursor:
            garage = store_garage(cursor, tenant_id, HOME)
            payer = store_payer(cursor, tenant_id, "payer-acme", "Acme")
            with pytest.raises(psycopg.errors.CheckViolation) as refused:
                cursor.execute(
                    "INSERT INTO agreements (tenant_id, external_id, version, garage_id, "
                    "payer_id, spots, monthly_price_minor, start_day, registrar) "
                    "VALUES (%s, 'ag-x', 1, %s, %s, 1, 1000, '2026-01-05', 'elsewhere')",
                    (tenant_id, garage, payer),
                )
            assert refused.value.diag.constraint_name == "agreements_registrar_is_stated"
        app.rollback()
    finally:
        app.close()
