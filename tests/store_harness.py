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
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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


def migrate(dsn: str) -> Any:
    """Drop and rebuild the schema from ``migrations/`` as the owner."""
    owner = connect(dsn)
    owner.autocommit = True
    with owner.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            cursor.execute(path.read_text())
        cursor.execute(f"ALTER ROLE monthly_billing_app LOGIN PASSWORD '{APP_PASSWORD}'")
    return owner


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
    garage: Garage
    garage_uuid: UUID
    payer_uuids: dict[str, UUID]
    agreement_uuids: dict[str, UUID]


def seed(
    app: Any,
    tenant_id: UUID,
    garage: Garage,
    agreements: tuple[Agreement, ...],
) -> Seeded:
    """The garage, every payer the agreements name, and the agreements."""
    payer_uuids: dict[str, UUID] = {}
    agreement_uuids: dict[str, UUID] = {}
    with tenant(app, tenant_id) as cursor:
        garage_uuid = store_garage(cursor, tenant_id, garage)
        for agreement in agreements:
            if agreement.payer_id not in payer_uuids:
                payer_uuids[agreement.payer_id] = store_payer(
                    cursor, tenant_id, agreement.payer_id, f"Payer {agreement.payer_id}"
                )
            agreement_uuids[agreement.id] = store_agreement(
                cursor, tenant_id, garage, garage_uuid, payer_uuids[agreement.payer_id], agreement
            )
    app.commit()
    return Seeded(tenant_id, garage, garage_uuid, payer_uuids, agreement_uuids)


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
