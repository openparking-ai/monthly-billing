"""G25 -- an owner's exception follows the agreement's identity across versions.

The L3 stored a second version of an agreement (a price change) and watched a
block lift and a grace extension vanish at the lane: the exception was attached
to the version ROW and read back by that row's id. It is now read by the
agreement's external id, every version. The control plants the read back onto
the chosen version's row and requires red.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_agreement_exception
from monthly_billing.findings import NOT_COVERED_BLOCKED_BY_OWNER
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_agreement
from store_harness import needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)


def _store_version_two(app, seeded) -> None:
    """A price change: the same agreement, next version, new row."""
    v2 = simple_agreement(start_day=date(2026, 1, 5), version=2, monthly_price_minor=13000)
    with tenant(app, seeded.tenant_id) as cursor:
        store_agreement(
            cursor, seeded.tenant_id, GARAGE, seeded.garage_uuid,
            seeded.payer_uuids["payer-acme"], v2,
        )
    app.commit()


@pytest.mark.guarantee("G25")
def test_a_block_on_version_one_still_holds_after_version_two(app, tenant_id):
    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    record_agreement_exception(
        app, tenant_id, seeded.agreement_uuids["ag-0001"],
        OwnerException(
            id="block-1", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.BLOCK, recorded_by="owner", recorded_at=_at(date(2026, 5, 3)),
        ),
    )
    before = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 4)))
    assert not before.covered and before.reason_code == NOT_COVERED_BLOCKED_BY_OWNER

    _store_version_two(app, seeded)
    after = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 4)))
    assert after.agreement_version == 2, "the lane is not reading the new version"
    assert not after.covered and after.reason_code == NOT_COVERED_BLOCKED_BY_OWNER, (
        "the block lifted on the day the price changed"
    )


@pytest.mark.guarantee("G25")
def test_a_grace_extension_on_version_one_still_applies_after_version_two(app, tenant_id):
    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    record_agreement_exception(
        app, tenant_id, seeded.agreement_uuids["ag-0001"],
        OwnerException(
            id="grace-1", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.EXTEND_GRACE, recorded_by="owner",
            recorded_at=_at(date(2026, 5, 1)), extra_grace_days=10,
        ),
    )
    # base grace 5 from 04-30 ends 05-05; +10 covers 05-12
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 12))).covered
    _store_version_two(app, seeded)
    after = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 12)))
    assert after.covered, f"the extension vanished with the new version: {after.reason_code}"


@pytest.mark.guarantee("G25")
def test_the_exception_row_still_names_the_version_the_owner_was_looking_at(app, tenant_id):
    """Stored against the version row -- a fact worth keeping -- and READ by identity."""
    from store_harness import query

    seeded = seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    record_agreement_exception(
        app, tenant_id, seeded.agreement_uuids["ag-0001"],
        OwnerException(
            id="block-1", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.BLOCK, recorded_by="owner", recorded_at=_at(date(2026, 5, 3)),
        ),
    )
    _store_version_two(app, seeded)
    rows = query(
        app, tenant_id,
        "SELECT a.version FROM owner_exceptions e JOIN agreements a ON a.id = e.agreement_id",
    )
    assert rows == [(1,)]
