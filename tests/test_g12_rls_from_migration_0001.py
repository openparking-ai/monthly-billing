"""G12 -- every table carries a tenant column, ENABLE, FORCE and a policy.

**READ FROM THE CATALOGUE, NEVER FROM A LIST OF TABLE NAMES.** A check that
walked a list somebody typed cannot notice a table added without protection,
which is the entire failure it exists to catch.

**AND THE SUPERUSER TRAP IS CHECKED BEFORE ANYTHING ELSE.** A Postgres superuser
bypasses row-level security unconditionally; `FORCE` does not stop one, it only
closes the table-owner hole. The stock `postgres` service container in CI hands
you a superuser by default, and an isolation test run as that user sees every
tenant's rows whether the policies exist or not -- it fails on correct code, which
invites somebody to weaken the assertion instead of fixing the connection. So
`assert_role_cannot_bypass_rls` runs first: the test proves it is connected as a
role that COULD be stopped, before it proves it was stopped.

**THE ONE ALLOWANCE IN THE SUITE IS HERE.** There is no way to read a database
catalogue without a database, so these skip when `MONTHLY_BILLING_TEST_DSN` is
unset. CI always sets it, so CI never skips them -- and `MONTHLY_BILLING_ALLOW_UNRUN`
is empty in CI, so a run where these did not execute FAILS rather than passing
quietly. That is the difference between an allowance and a hole.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
DSN = os.environ.get("MONTHLY_BILLING_TEST_DSN")

pytestmark = [
    pytest.mark.needs_postgres,
    pytest.mark.skipif(not DSN, reason="MONTHLY_BILLING_TEST_DSN is not set"),
]


@pytest.fixture(scope="module")
def migrated():
    """A database with the migrations applied, as the OWNER.

    Applied here rather than by a script CI runs separately, so that the schema
    under test is the schema in `migrations/` and not whatever a fixture built.
    """
    from store_harness import migrate

    # store_harness.migrate is the same drop-and-rebuild from ``migrations/``,
    # and it gives the app role its login as scripts/ensure-app-role.py would.
    # It also holds the cluster-wide migration lock: the role is cluster-global.
    owner = migrate(DSN)
    yield owner
    owner.close()


@pytest.fixture(scope="module")
def as_app(migrated):
    """A connection as the application role -- NOSUPERUSER, NOBYPASSRLS."""
    from monthly_billing.store.postgres import connect

    app_dsn = f"{DSN} user=monthly_billing_app password=test-only-password"
    connection = connect(app_dsn)
    connection.autocommit = False
    yield connection
    connection.close()


@pytest.mark.guarantee("G12")
def test_the_connection_could_actually_be_stopped(as_app):
    """THE CONTROL ON EVERY ISOLATION ASSERTION BELOW.

    Without it, a green isolation test proves the connection is a superuser and
    nothing else.
    """
    from monthly_billing.store.postgres import assert_role_cannot_bypass_rls

    assert_role_cannot_bypass_rls(as_app)


@pytest.mark.guarantee("G12")
def test_the_owner_connection_would_have_failed_that_check(migrated):
    """The positive control for the check itself.

    The migration runs as a superuser in CI, so this asserts the guard FIRES on a
    connection that can bypass -- otherwise the assertion above could be green
    because the guard never says no to anything.
    """
    from monthly_billing.store.postgres import assert_role_cannot_bypass_rls

    with pytest.raises(AssertionError, match="inert"):
        assert_role_cannot_bypass_rls(migrated)


@pytest.mark.guarantee("G12")
def test_no_table_ships_without_row_level_security(as_app):
    from monthly_billing.store.postgres import tables_without_rls

    failures = tables_without_rls(as_app)
    assert failures == [], "\n".join(failures)


@pytest.mark.guarantee("G12")
def test_every_table_but_tenants_carries_a_tenant_column(as_app):
    """A policy on a table with no tenant column can only compare something else,
    and "something else" is where a cross-tenant read lives."""
    from monthly_billing.store.postgres import tables_without_tenant_column

    assert tables_without_tenant_column(as_app) == []


@pytest.mark.guarantee("G12")
def test_the_coverage_check_is_not_measuring_an_empty_schema(as_app):
    """If the migration had not run, every check above would pass on zero tables."""
    with as_app.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )
        (count,) = cursor.fetchone()
    assert count >= 10, f"only {count} tables found; the migration did not run"


@pytest.mark.guarantee("G12")
def test_one_tenant_cannot_read_another_tenants_rows(as_app, migrated):
    from monthly_billing.store.postgres import assert_role_cannot_bypass_rls, tenant

    assert_role_cannot_bypass_rls(as_app)

    with migrated.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (slug, name) VALUES ('alpha', 'Alpha'), ('beta', 'Beta') "
            "ON CONFLICT (slug) DO NOTHING"
        )
        cursor.execute("SELECT id, slug FROM tenants ORDER BY slug")
        ids = dict((slug, tid) for tid, slug in cursor.fetchall())
        for slug, tid in ids.items():
            cursor.execute(
                "INSERT INTO payers (tenant_id, external_id, name) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (tid, f"payer-{slug}", f"Payer {slug}"),
            )

    with tenant(as_app, str(ids["alpha"])) as cursor:
        cursor.execute("SELECT external_id FROM payers")
        visible = sorted(row[0] for row in cursor.fetchall())
    as_app.rollback()

    assert visible == ["payer-alpha"], (
        f"tenant alpha saw {visible}; the isolation policy is not holding"
    )


@pytest.mark.guarantee("G12")
def test_a_connection_with_no_tenant_context_sees_nothing(as_app):
    """Fail closed. `tenant_id = NULL` is NULL, not true, so a connection that
    forgot to set the context reads no rows rather than all of them."""
    from monthly_billing.store.postgres import assert_role_cannot_bypass_rls

    assert_role_cannot_bypass_rls(as_app)
    with as_app.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM payers")
        (count,) = cursor.fetchone()
    as_app.rollback()
    assert count == 0


@pytest.mark.guarantee("G12")
def test_a_tenant_cannot_write_a_row_attributed_to_another(as_app, migrated):
    """WITH CHECK, not just USING. Omit it and a tenant can insert rows belonging
    to somebody else -- writes unguarded while reads look fine, which is the worst
    version of this bug because it tests clean from the reading side."""
    import psycopg

    from monthly_billing.store.postgres import tenant

    with migrated.cursor() as cursor:
        cursor.execute("SELECT id, slug FROM tenants ORDER BY slug")
        ids = dict((slug, tid) for tid, slug in cursor.fetchall())

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant(as_app, str(ids["alpha"])) as cursor:
            cursor.execute(
                "INSERT INTO payers (tenant_id, external_id, name) VALUES (%s, %s, %s)",
                (ids["beta"], "smuggled", "Smuggled"),
            )
    as_app.rollback()


@pytest.mark.guarantee("G12")
def test_a_migration_that_refuses_leaves_the_prior_schema_and_a_usable_connection():
    """0002 replaces ``charge_attempts`` and refuses to run if the old table holds
    a row. The refusal must leave the catalogue at the 0001 state -- no partial
    DDL -- and the harness must end the aborted transaction the migration's own
    BEGIN opened, so the next statement is not "current transaction is aborted".
    The documented apply command carries ``ON_ERROR_STOP=1`` for the same reason:
    the L3 measured the bare command exiting 0 on this exact refusal."""
    import psycopg

    from monthly_billing.store.postgres import connect
    from store_harness import DSN, migrate

    def catalogue(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname, "
                "(SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_attribute a "
                " WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped), "
                "(SELECT string_agg(k.conname, ',' ORDER BY k.conname) FROM pg_constraint k "
                " WHERE k.conrelid = c.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1"
            )
            return cursor.fetchall()

    from store_harness import cluster_lock

    owner = connect(DSN)
    owner.autocommit = True
    try:
        with cluster_lock(DSN), owner.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            cursor.execute((MIGRATIONS / "0001_tenants_agreements_and_rls.sql").read_text())
            cursor.execute("INSERT INTO tenants (slug, name) VALUES ('t', 't') RETURNING id")
            (tenant_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO garages (tenant_id, external_id, timezone, currency, billing_day, "
                "payment_grace_days, identity_rule) VALUES (%s, 'g', 'UTC', 'USD', "
                "'last_day_of_month', 5, 'exact') RETURNING id",
                (tenant_id,),
            )
            (garage_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO payers (tenant_id, external_id, name) VALUES (%s, 'p', 'p') "
                "RETURNING id",
                (tenant_id,),
            )
            (payer_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
                "period_start_day, due_at) VALUES (%s, 'r', %s, %s, 'USD', '2026-01-01', now()) "
                "RETURNING id",
                (tenant_id, payer_id, garage_id),
            )
            (invoice_id,) = cursor.fetchone()
            cursor.execute(
                "INSERT INTO charge_attempts (tenant_id, invoice_id, attempts) VALUES (%s, %s, 1)",
                (tenant_id, invoice_id),
            )
        before = catalogue(owner)
        with pytest.raises(psycopg.errors.RaiseException) as refused:
            with owner.cursor() as cursor:
                try:
                    cursor.execute(
                        (MIGRATIONS / "0002_billing_run_payments_and_reversals.sql").read_text()
                    )
                except Exception:
                    cursor.execute("ROLLBACK")  # what store_harness.migrate does
                    raise
        assert "charge_attempts holds 1 row(s)" in str(refused.value)
        assert catalogue(owner) == before, "the refused migration left partial DDL behind"
        assert len(before) == 12
    finally:
        owner.close()
    # and the harness's own migrate() rebuilds cleanly from here for the next module
    migrate(DSN).close()
