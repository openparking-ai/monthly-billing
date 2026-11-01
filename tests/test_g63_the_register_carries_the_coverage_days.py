"""G63 -- the garage register carries the days the module decides coverage by.

``show-garage-register`` is what a lane refreshes from, and it said only
"active" or "cancelled". So the lane covered an agreement the module itself
answers NOT COVERED: unpaid past grace, paused, not yet started, blocked by the
owner. Each agreement the register names now also carries its latest
version's ``start_day`` and ``pauses``, the owner's ``blocked``, and
``not_covered_from`` -- the first home-calendar day an unpaid invoice is past
grace, extensions counted -- read through the same reads ``covered-in-store``
makes. Dates and a flag: no amount, no invoice, no payer.

THE PROOF IS AGREEMENT WITH THE MODULE. ``_judge`` decides a day from the
register alone, the way a lane would, and every test compares it with
``covered_from_store`` on the days either side of each boundary.

Controls, each with a plant in ``scripts/fail_controls.py``: the unpaid day
never published (the unpaid-past-grace holder reads covered again); the
owner's grace extensions not counted; the block not read; the pauses not
read.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.agreement import Pause
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_agreement_exception, record_invoice_exception
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import show_garage_register
from store_harness import needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()  # grace 5 days; bills on the last day of the month
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _entry(app, tenant_id) -> dict:
    """The one agreement the register names, as the document the verb prints."""
    with tenant(app, tenant_id) as cursor:
        register = show_garage_register(cursor, tenant_id, GARAGE.id)
    app.rollback()
    (entry,) = register.as_document()["agreements"]
    return entry


def _judge(entry: dict, day: date) -> bool:
    """A lane's reading of one register entry on one garage-local day: dates
    only, nothing of the module's code."""
    def iso(text):
        return date.fromisoformat(text) if text else None

    if entry.get("lane_must_ask"):
        return "ask"  # hour-limited: the days alone cannot say covered (OP-7-1)

    if entry["status"] == "cancelled" and day >= iso(entry["cancelled_effective_day"]):
        return False
    if day < iso(entry["start_day"]):
        return False
    if any(iso(p["from_day"]) <= day < iso(p["until_day"]) for p in entry["pauses"]):
        return False
    if entry["blocked"]:
        return False
    owed = entry.get("first_charge_owed")
    if owed and iso(owed["not_covered_from"]) <= day < iso(owed["until_day"]):
        return False
    stop = iso(entry["not_covered_from"])
    return stop is None or day < stop


def _agrees(app, tenant_id, *days: date) -> None:
    entry = _entry(app, tenant_id)
    for day in days:
        module = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(day))
        assert _judge(entry, day) is module.covered, (day, entry, module.reason)


def _issued_unpaid(app, tenant_id, **overrides):
    agreement = simple_agreement(start_day=date(2026, 1, 5), **overrides)
    seeded = seed(app, tenant_id, GARAGE, (agreement,))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return seeded, line


@pytest.mark.guarantee("G63")
def test_an_unpaid_invoice_names_the_first_day_past_grace_and_a_cheque_clears_it(
    app, tenant_id
):
    """Due 04-30, grace 5: covered through 05-05, not covered from 05-06 --
    the register says 2026-05-06 and agrees with the module on both days.
    Paid on 05-08, the day is gone and the car reads covered again."""
    _seeded, line = _issued_unpaid(app, tenant_id)
    entry = _entry(app, tenant_id)
    assert entry["not_covered_from"] == "2026-05-06" and entry["blocked"] is False
    _agrees(app, tenant_id, date(2026, 5, 5), date(2026, 5, 6), date(2026, 5, 20))
    record_payment(app, tenant_id, line.reference, PaymentMethod.CHEQUE, line.total_minor,
                   _at(date(2026, 5, 8)), recorded_by="operator")
    assert _entry(app, tenant_id)["not_covered_from"] is None
    _agrees(app, tenant_id, date(2026, 5, 9))


@pytest.mark.guarantee("G63")
def test_the_owners_grace_extensions_move_the_day(app, tenant_id):
    """Seven days on the invoice and three on the agreement: 5 + 7 + 3 = 15
    days of grace from 04-30, so the first uncovered day is 05-16."""
    seeded, line = _issued_unpaid(app, tenant_id)
    record_invoice_exception(app, tenant_id, OwnerException(
        id="exc-grace", agreement_id=None, invoice_reference=line.reference,
        kind=ExceptionKind.EXTEND_GRACE, recorded_by="the owner",
        recorded_at=_at(date(2026, 5, 6)), extra_grace_days=7,
    ))
    record_agreement_exception(app, tenant_id, seeded.agreement_uuids["ag-0001"], OwnerException(
        id="exc-grace-2", agreement_id="ag-0001", invoice_reference=None,
        kind=ExceptionKind.EXTEND_GRACE, recorded_by="the owner",
        recorded_at=_at(date(2026, 5, 7)), extra_grace_days=3,
    ))
    assert _entry(app, tenant_id)["not_covered_from"] == "2026-05-16"
    _agrees(app, tenant_id, date(2026, 5, 15), date(2026, 5, 16))


@pytest.mark.guarantee("G63")
def test_the_owners_block_is_carried_and_an_unblock_lifts_it(app, tenant_id):
    seeded, line = _issued_unpaid(app, tenant_id)
    record_payment(app, tenant_id, line.reference, PaymentMethod.CHEQUE, line.total_minor,
                   _at(date(2026, 5, 1)), recorded_by="operator")
    assert _entry(app, tenant_id)["blocked"] is False
    record_invoice_exception(app, tenant_id, OwnerException(
        id="exc-block", agreement_id=None, invoice_reference=line.reference,
        kind=ExceptionKind.BLOCK, recorded_by="the owner", recorded_at=_at(date(2026, 5, 2)),
    ))
    assert _entry(app, tenant_id)["blocked"] is True
    _agrees(app, tenant_id, date(2026, 5, 3))
    record_agreement_exception(app, tenant_id, seeded.agreement_uuids["ag-0001"], OwnerException(
        id="exc-unblock", agreement_id="ag-0001", invoice_reference=None,
        kind=ExceptionKind.UNBLOCK, recorded_by="the owner", recorded_at=_at(date(2026, 5, 4)),
    ))
    assert _entry(app, tenant_id)["blocked"] is False
    _agrees(app, tenant_id, date(2026, 5, 5))


@pytest.mark.guarantee("G63")
def test_the_start_day_and_the_pauses_are_the_versions_and_agree_with_the_module(
    app, tenant_id
):
    """An agreement not yet started on 06-09, paused 06-20 to 07-01 (half-open):
    the register carries both as stored, and the module agrees on every day
    either side of each boundary."""
    seed(app, tenant_id, GARAGE, (simple_agreement(
        start_day=date(2026, 6, 10),
        pauses=(Pause(from_day=date(2026, 6, 20), until_day=date(2026, 7, 1)),),
    ),))
    entry = _entry(app, tenant_id)
    assert entry["start_day"] == "2026-06-10"
    assert entry["pauses"] == [{"from_day": "2026-06-20", "until_day": "2026-07-01"}]
    assert entry["not_covered_from"] is None and entry["blocked"] is False
    boundaries = (date(2026, 6, 10), date(2026, 6, 20), date(2026, 7, 1))
    _agrees(app, tenant_id, *(b + timedelta(days=d) for b in boundaries for d in (-1, 0)))


@pytest.mark.guarantee("G63")
def test_an_hour_limited_agreement_carries_its_hours_and_is_never_covered_by_days_alone(
    app, tenant_id
):
    """OP-7-1. The register left out access hours, so a lane reading it covered
    a 07:00-19:00 parker who entered at 18:00 and left at 10:00 the next day --
    covered-in-store says NOT COVERED for that stay, and the barrier opened for
    free. Each agreement now carries ``access_hours`` (local times, or null)
    and ``lane_must_ask``: true whenever there are hours, because no reading of
    the days can decide such a stay -- the lane asks covered-in-store with the
    stay's entry. An agreement with no hours is judged by its days as before."""
    from datetime import time

    from monthly_billing.agreement import AccessHours

    seed(app, tenant_id, GARAGE, (simple_agreement(
        start_day=date(2026, 1, 5),
        access_hours=AccessHours(entry_from=time(7, 0), exit_by=time(19, 0)),
    ),))
    entry = _entry(app, tenant_id)
    assert entry["access_hours"] == {"entry_from": "07:00", "exit_by": "19:00"}
    assert entry["lane_must_ask"] is True
    day = date(2026, 4, 1)
    entered = datetime(2026, 4, 1, 18, 0, tzinfo=TZ)
    leaves = datetime(2026, 4, 2, 10, 0, tzinfo=TZ)
    module = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", leaves,
                                stay_entered_at=entered)
    assert module.covered is False
    assert _judge(entry, day) is not True, "the register's days alone covered the stay"


@pytest.mark.guarantee("G63")
def test_an_agreement_without_hours_is_decided_by_its_days(app, tenant_id):
    """The control on the one above: no hours, ``access_hours`` null,
    ``lane_must_ask`` false, and the days agree with the module."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    entry = _entry(app, tenant_id)
    assert entry["access_hours"] is None and entry["lane_must_ask"] is False
    _agrees(app, tenant_id, date(2026, 4, 1))
