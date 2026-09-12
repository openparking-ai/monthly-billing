"""G32 -- one car, one agreement per garage. His ruling.

The outside pass put one plate on two agreements at one garage and watched the
highest agreement id decide coverage both ways -- covered against an unpaid
agreement, not covered against a paid one. Now the store registers a vehicle
identity to ONE agreement per garage and refuses a second by name, the pure
call refuses two identities by name, and the pick is gone.

Controls: the store's refusal planted away (the second agreement is accepted);
the pure call's refusal planted away (it picks again).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.agreement import Status
from monthly_billing.entitlement import is_covered
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import (
    REFUSAL_VEHICLE_ALREADY_REGISTERED,
    REFUSAL_VEHICLE_ON_TWO_AGREEMENTS,
    Refused,
)
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    ONE_AGREEMENT_PER_GARAGE,
    registrations_at_garage,
    store_agreement,
    store_garage,
    store_payer,
)
from store_harness import needs_postgres, query

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
PLATE = "SHARED-1"
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=TZ)

A = simple_agreement(id="ag-A", payer_id="payer-a", vehicles=(PLATE, "CAR-A2"),
                     start_day=date(2026, 1, 5))
Z = simple_agreement(id="ag-Z", payer_id="payer-z", vehicles=(PLATE, "CAR-Z2"),
                     start_day=date(2026, 1, 5))


def _garage_and_payers(app, tenant_id):
    with tenant(app, tenant_id) as cursor:
        garage = store_garage(cursor, tenant_id, GARAGE)
        payers = {
            "payer-a": store_payer(cursor, tenant_id, "payer-a", "A"),
            "payer-z": store_payer(cursor, tenant_id, "payer-z", "Z"),
        }
    app.commit()
    return garage, payers


def _store(app, tenant_id, garage, payers, agreement, *, now=DAY):
    with tenant(app, tenant_id) as cursor:
        uuid = store_agreement(
            cursor, tenant_id, GARAGE, garage, payers[agreement.payer_id], agreement, now=now
        )
    app.commit()
    return uuid


@pytest.mark.guarantee("G32")
def test_a_second_agreement_listing_the_same_vehicle_is_refused_by_name(app, tenant_id):
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, garage, payers, Z)
    app.rollback()
    assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
    assert "'ag-A'" in refused.value.detail and "active" in refused.value.detail
    assert query(app, tenant_id, "SELECT external_id FROM agreements") == [("ag-A",)], (
        "the refused agreement was stored anyway"
    )
    assert registrations_at_garage_as_app(app, tenant_id, garage) == [
        ("cara2", "ag-A"), ("shared1", "ag-A"),
    ]
    answer = covered_from_store(app, tenant_id, GARAGE.id, PLATE, DAY)
    assert answer.covered and answer.agreement_id == "ag-A"


def registrations_at_garage_as_app(app, tenant_id, garage):
    with tenant(app, tenant_id) as cursor:
        rows = list(registrations_at_garage(cursor, garage))
    app.rollback()
    return rows


@pytest.mark.guarantee("G32")
def test_a_new_version_of_the_same_agreement_keeps_its_vehicle(app, tenant_id):
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    v2 = simple_agreement(id="ag-A", version=2, payer_id="payer-a", vehicles=(PLATE,),
                          monthly_price_minor=13000, start_day=date(2026, 1, 5))
    _store(app, tenant_id, garage, payers, v2)
    assert registrations_at_garage_as_app(app, tenant_id, garage) == [("shared1", "ag-A")], (
        "the vehicle the new version dropped was not released"
    )


@pytest.mark.guarantee("G32")
def test_a_version_that_drops_the_vehicle_releases_it_to_another_agreement(app, tenant_id):
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    without = simple_agreement(id="ag-A", version=2, payer_id="payer-a", vehicles=("CAR-A2",),
                               start_day=date(2026, 1, 5))
    _store(app, tenant_id, garage, payers, without)
    _store(app, tenant_id, garage, payers, Z)
    assert registrations_at_garage_as_app(app, tenant_id, garage) == [
        ("cara2", "ag-A"), ("carz2", "ag-Z"), ("shared1", "ag-Z"),
    ]


@pytest.mark.guarantee("G32")
def test_a_cancelled_agreement_frees_its_vehicle_on_its_effective_day_and_not_before(
    app, tenant_id
):
    """The car is still covered until the effective day, so the registration
    stays until then: on D-1 the second agreement is refused NAMING D; on D it
    is accepted and the old row is gone."""
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    cancelled = simple_agreement(
        id="ag-A", version=2, payer_id="payer-a", vehicles=(PLATE, "CAR-A2"),
        start_day=date(2026, 1, 5), status=Status.CANCELLED,
        cancelled_effective_day=date(2026, 6, 1),
    )
    _store(app, tenant_id, garage, payers, cancelled)
    day_before = datetime(2026, 5, 31, 23, 0, tzinfo=TZ)
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, garage, payers, Z, now=day_before)
    app.rollback()
    assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
    assert "2026-06-01" in refused.value.detail
    on_the_day = datetime(2026, 6, 1, 0, 30, tzinfo=TZ)
    _store(app, tenant_id, garage, payers, Z, now=on_the_day)
    assert registrations_at_garage_as_app(app, tenant_id, garage) == [
        ("cara2", "ag-A"), ("carz2", "ag-Z"), ("shared1", "ag-Z"),
    ]
    answer = covered_from_store(app, tenant_id, GARAGE.id, PLATE, on_the_day)
    assert answer.covered and answer.agreement_id == "ag-Z"


@pytest.mark.guarantee("G32")
def test_a_raw_insert_as_the_application_role_hits_the_unique_backstop(app, tenant_id):
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    with pytest.raises(psycopg.errors.UniqueViolation) as violation:
        with tenant(app, tenant_id) as cursor:
            cursor.execute(
                "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
                "agreement_external_id, registered_at) VALUES (%s, %s, 'shared1', 'ag-Z', now())",
                (tenant_id, garage),
            )
    app.rollback()
    assert violation.value.diag.constraint_name == ONE_AGREEMENT_PER_GARAGE


@pytest.mark.guarantee("G32")
def test_the_pure_call_refuses_two_agreement_identities_for_one_vehicle():
    """A library caller can hand in what the store no longer produces. The
    call refuses by name rather than picking -- in either order."""
    cancelled_z = simple_agreement(
        id="ag-Z", payer_id="payer-z", vehicles=(PLATE,), start_day=date(2026, 1, 5),
        status=Status.CANCELLED, cancelled_effective_day=date(2026, 3, 1),
    )
    for agreements in ((A, cancelled_z), (cancelled_z, A), (A, Z)):
        with pytest.raises(Refused) as refused:
            is_covered(garage=GARAGE, agreements=agreements, vehicle_identity=PLATE, at=DAY)
        assert refused.value.code == REFUSAL_VEHICLE_ON_TWO_AGREEMENTS
        assert "'ag-A'" in refused.value.detail and "'ag-Z'" in refused.value.detail


@pytest.mark.guarantee("G32")
def test_the_pure_call_takes_the_latest_version_of_one_identity():
    v2 = simple_agreement(id="ag-A", version=2, payer_id="payer-a", vehicles=(PLATE,),
                          spots=3, start_day=date(2026, 1, 5))
    for agreements in ((A, v2), (v2, A)):
        answer = is_covered(garage=GARAGE, agreements=agreements, vehicle_identity=PLATE, at=DAY)
        assert answer.covered and answer.agreement_version == 2 and answer.entitlement == 3


@pytest.mark.guarantee("G32")
def test_the_registration_decides_coverage_not_a_scan_of_vehicle_lists(app, tenant_id):
    """A second agreement row listing the plate, written past the module: the
    REGISTRATION says ag-A, and the lane is answered by ag-A -- no pick, no
    scan of every list. (The pure call, handed both, refuses -- above.)"""
    garage, payers = _garage_and_payers(app, tenant_id)
    _store(app, tenant_id, garage, payers, A)
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, spots, "
            "monthly_price_minor, start_day) VALUES (%s, 'ag-Z', 1, %s, %s, 1, 1000, '2026-01-05') "
            "RETURNING id",
            (tenant_id, garage, payers["payer-z"]),
        )
        (z_uuid,) = cursor.fetchone()
        cursor.execute(
            "INSERT INTO agreement_vehicles "
            "(tenant_id, agreement_id, identity, identity_normalised) "
            "VALUES (%s, %s, %s, 'shared1')",
            (tenant_id, z_uuid, PLATE),
        )
    app.commit()
    answer = covered_from_store(app, tenant_id, GARAGE.id, PLATE, DAY)
    assert answer.covered and answer.agreement_id == "ag-A"


@pytest.mark.guarantee("G32")
def test_a_vehicle_with_no_registration_is_on_no_agreement(app, tenant_id):
    """The control on the registration read: an agreement row that lists a
    plate but was never registered (written past the module) covers nothing."""
    garage, payers = _garage_and_payers(app, tenant_id)
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO agreements (tenant_id, external_id, version, garage_id, payer_id, spots, "
            "monthly_price_minor, start_day) VALUES (%s, 'ag-Z', 1, %s, %s, 1, 1000, '2026-01-05') "
            "RETURNING id",
            (tenant_id, garage, payers["payer-z"]),
        )
        (z_uuid,) = cursor.fetchone()
        cursor.execute(
            "INSERT INTO agreement_vehicles "
            "(tenant_id, agreement_id, identity, identity_normalised) "
            "VALUES (%s, %s, %s, 'shared1')",
            (tenant_id, z_uuid, PLATE),
        )
    app.commit()
    answer = covered_from_store(app, tenant_id, GARAGE.id, PLATE, DAY)
    assert not answer.covered and answer.reason_code == "NO_AGREEMENT"
