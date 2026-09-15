"""A migrated database, an application connection, and the documents seeded --
shared by every store-backed test.

Not a test module (no ``test_`` prefix, so the guarantee guard does not ask it
for a mark) and not a conftest: it is imported by name, so a reader of any test
can see where the database came from. The shape is the one
``test_g12_rls_from_migration_0001.py`` established -- migrations applied as the
OWNER from ``migrations/``, sorted, so the schema under test is the schema that
ships; the application connects as the NOSUPERUSER NOBYPASSRLS role.

**ONE DATABASE, ONE TENANT PER TEST MODULE, FRESH ROWS PER TEST.** Every seed
returns the uuids it created so a test can address its own rows and nothing
else's; the tenant policy stops it reading anything else anyway, and G12 proves
that.

**ONE MIGRATION AT A TIME IN THE CLUSTER.** The migrations ``CREATE`` or
``ALTER`` the application ROLE, and a role is cluster-global: two databases
migrating at once in one cluster collide on it (``tuple concurrently
updated``), which cost the branch L3 four probe re-runs. So ``migrate`` runs
under a cluster-wide advisory lock. An advisory lock is PER DATABASE in
PostgreSQL -- ``pg_locks`` tags it with the database oid, measured: two
sessions on two databases both take key 42 -- so "cluster-wide" means taking
it on ONE shared database of the cluster (``postgres``, else ``template1``),
through a second connection held for the length of the migration. Test harness
only; the migration files are not changed for it. If neither shared database
accepts the connection the lock is taken on the target database and a warning
says the serialisation is then per-database only.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from monthly_billing.agreement import Agreement
from monthly_billing.garage import Garage
from monthly_billing.store.postgres import connect, set_tenant, tenant
from monthly_billing.store.records import store_agreement, store_garage, store_payer
from monthly_billing.store.writes import as_uuid

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
DSN = os.environ.get("MONTHLY_BILLING_TEST_DSN")
APP_PASSWORD = "test-only-password"


#: One key, one meaning: "a migration of this module is running in this cluster".
MIGRATION_LOCK_KEY = 0x6D6F6E7468  # 'month', as a bigint


@contextmanager
def cluster_lock(dsn: str):
    """Hold a cluster-wide advisory lock for the block. See the module docstring
    for why it is taken on a SHARED database and not on ``dsn``'s own."""
    import psycopg
    from psycopg import conninfo

    params = conninfo.conninfo_to_dict(dsn)
    holder = None
    for shared in ("postgres", "template1"):
        try:
            holder = connect(conninfo.make_conninfo(**{**params, "dbname": shared}))
            break
        except psycopg.OperationalError:
            continue
    if holder is None:
        print(
            "store_harness.cluster_lock: no shared database accepted the connection; the "
            "migration lock is taken on the target database and serialises that database "
            "only.",
            file=sys.stderr,
        )
        holder = connect(dsn)
    holder.autocommit = True
    with holder.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            yield
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    holder.close()


def migrate(dsn: str, *, through: str | None = None) -> Any:
    """Drop and rebuild the schema from ``migrations/`` as the owner -- one
    migration at a time in the cluster.

    ``through`` names the last migration to apply, by its four-digit prefix
    (``"0003"``), for a test that seeds a schema and then applies the next
    migration itself with ``apply_migration`` -- the only way to prove a
    backfill did what it says against rows that were there before it ran.
    """
    owner = connect(dsn)
    owner.autocommit = True
    with cluster_lock(dsn), owner.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if through is not None and path.name[:4] > through:
                break
            _apply(cursor, path)
        cursor.execute(f"ALTER ROLE monthly_billing_app LOGIN PASSWORD '{APP_PASSWORD}'")
    return owner


def migration_path(prefix: str) -> Path:
    """The one migration file with this four-digit prefix."""
    (path,) = MIGRATIONS.glob(f"{prefix}_*.sql")
    return path


def apply_migration(owner: Any, prefix: str) -> None:
    """Apply one migration file to an already-migrated schema, as the owner,
    under the same cluster lock ``migrate`` takes."""
    with cluster_lock(DSN), owner.cursor() as cursor:
        _apply(cursor, migration_path(prefix))


def _apply(cursor: Any, path: Path) -> None:
    try:
        cursor.execute(path.read_text())
    except Exception:
        # A migration file opens its own BEGIN and never reaches its COMMIT
        # when it raises, so the connection is left inside an aborted
        # transaction. End it here, so the failure is the migration's
        # message and not "current transaction is aborted" on the next
        # statement -- and so the catalogue is the pre-migration state.
        cursor.execute("ROLLBACK")
        raise


def app_connection(dsn: str) -> Any:
    connection = connect(f"{dsn} user=monthly_billing_app password={APP_PASSWORD}")
    connection.autocommit = False
    return connection


def new_tenant(owner: Any, slug: str | None = None) -> UUID:
    slug = slug or f"t-{uuid4().hex[:8]}"
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenants (slug, name) VALUES (%s, %s) RETURNING id", (slug, slug)
        )
        (tenant_id,) = cursor.fetchone()
    return as_uuid(tenant_id)


@dataclass(frozen=True)
class Seeded:
    tenant_id: UUID
    #: The FIRST garage seeded -- the only one, for a single-garage seed.
    garage: Garage
    garage_uuid: UUID
    payer_uuids: dict[str, UUID]
    agreement_uuids: dict[str, UUID]
    #: Every garage seeded, by its id. ``garage_uuids[garage.id] == garage_uuid``.
    garage_uuids: dict[str, UUID] = field(default_factory=dict)


def seed(
    app: Any,
    tenant_id: UUID,
    garage: Garage,
    agreements: tuple[Agreement, ...],
) -> Seeded:
    """The garage, every payer the agreements name, and the agreements."""
    return seed_garages(app, tenant_id, (garage,), agreements)


def seed_garages(
    app: Any,
    tenant_id: UUID,
    garages: tuple[Garage, ...],
    agreements: tuple[Agreement, ...],
    *,
    now: datetime | None = None,
) -> Seeded:
    """Several garages, every payer the agreements name, and the agreements --
    each stored under its HOME garage, which must be among ``garages``. The
    shape a multi-garage agreement needs: its covered garages have to be in the
    store before it is."""
    payer_uuids: dict[str, UUID] = {}
    agreement_uuids: dict[str, UUID] = {}
    garage_uuids: dict[str, UUID] = {}
    by_id = {garage.id: garage for garage in garages}
    with tenant(app, tenant_id) as cursor:
        for garage in garages:
            garage_uuids[garage.id] = store_garage(cursor, tenant_id, garage)
        for agreement in agreements:
            if agreement.payer_id not in payer_uuids:
                payer_uuids[agreement.payer_id] = store_payer(
                    cursor, tenant_id, agreement.payer_id, f"Payer {agreement.payer_id}"
                )
            home = by_id[agreement.garage_id]
            agreement_uuids[agreement.id] = store_agreement(
                cursor, tenant_id, home, garage_uuids[home.id],
                payer_uuids[agreement.payer_id], agreement, now=now,
            )
    app.commit()
    first = garages[0]
    return Seeded(
        tenant_id, first, garage_uuids[first.id], payer_uuids, agreement_uuids, garage_uuids
    )


def query(app: Any, tenant_id: Any, statement: str, parameters: tuple = ()) -> list[tuple]:
    """A read inside the tenant's context, rolled back afterwards."""
    with app.cursor() as cursor:
        set_tenant(cursor, tenant_id)
        cursor.execute(statement, parameters)
        rows = cursor.fetchall()
    app.rollback()
    return rows


def instant(day: date, hour: int, tz: Any) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)


# ---------------------------------------------------------------------------
# The marks every store-backed test module carries. The fixtures that hand a
# test its database live in conftest.py, so no module has to import them.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

needs_postgres = [
    pytest.mark.needs_postgres,
    pytest.mark.skipif(not DSN, reason="MONTHLY_BILLING_TEST_DSN is not set"),
]
