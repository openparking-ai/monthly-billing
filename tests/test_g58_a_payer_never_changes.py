"""G58 -- an agreement's payer never changes between versions.

The invoice, its one-invoice-per-period lock, the unpaid check at the barrier
and the charge's mandate are all keyed on the PAYER. A version that switched
the payer was stored without a word, and then: a re-run of a period already
invoiced to the old payer invoiced it again to the new one (the lock is per
payer); the old payer's unpaid invoice stopped counting at the barrier (the
check follows the newest payer); and the old payer's invoice was charged under
the new payer's mandate. The store now refuses the version by name, as it
refuses a home move and a registrar change, before anything is written.

Controls, each with a plant in ``scripts/fail_controls.py``: the refusal
removed (the version stores, the re-run invoices May twice, the old invoice
stops counting).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import REFUSAL_PAYER_CHANGED, Refused
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_agreement, store_payer
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()  # grace 5 days; bills on the last day of the month
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _counts(app, tenant_id):
    (row,) = query(
        app, tenant_id,
        "SELECT (SELECT count(*) FROM agreements), (SELECT count(*) FROM agreement_garages), "
        "(SELECT count(*) FROM agreement_vehicles), (SELECT count(*) FROM vehicle_registrations), "
        "(SELECT count(*) FROM mandates), (SELECT count(*) FROM invoices)",
    )
    return row


def _alice_invoiced_for_may(app, tenant_id):
    """v1, paid by payer-alice, from January; its May invoice issued 04-30 and
    left unpaid. And a second payer the store knows, for v2 to name."""
    v1 = simple_agreement(start_day=date(2026, 1, 5), payer_id="payer-alice")
    seeded = seed(app, tenant_id, GARAGE, (v1,))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED and line.payer_id == "payer-alice"
    with tenant(app, tenant_id) as cursor:
        acme = store_payer(cursor, tenant_id, "payer-acme", "Acme, the employer")
    app.commit()
    return seeded, acme


def _store(app, tenant_id, seeded, payer_uuid, agreement):
    try:
        with tenant(app, tenant_id) as cursor:
            store_agreement(cursor, tenant_id, GARAGE, seeded.garage_uuid, payer_uuid,
                            agreement, now=_at(date(2026, 5, 10)))
    except BaseException:
        app.rollback()
        raise
    app.commit()


@pytest.mark.guarantee("G58")
def test_a_version_naming_another_payer_is_refused_by_name_and_writes_nothing(app, tenant_id):
    """FO-M16's sequence: on May 10 a v2 makes the employer the payer. Refused
    by name, every table as it was -- and so the three harms cannot follow: a
    re-run of May issues nothing new, and Alice's unpaid May still counts at
    the barrier on May 12."""
    seeded, acme = _alice_invoiced_for_may(app, tenant_id)
    counts = _counts(app, tenant_id)
    v2 = simple_agreement(version=2, start_day=date(2026, 1, 5), payer_id="payer-acme")
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, seeded, acme, v2)
    assert refused.value.code == REFUSAL_PAYER_CHANGED
    assert "'ag-0001' version 2" in refused.value.detail
    assert "'payer-acme'" in refused.value.detail and "never changes" in refused.value.detail
    assert _counts(app, tenant_id) == counts, "the refused version wrote something"

    rerun = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=_at(date(2026, 5, 11)))
    assert [(line.payer_id, line.outcome) for line in rerun.lines] == [
        ("payer-alice", RunOutcome.ALREADY_ISSUED)
    ]
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(1,)]
    unpaid = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 12)))
    assert not unpaid.covered and unpaid.reason_code == "UNPAID_PAST_GRACE"


@pytest.mark.guarantee("G58")
def test_a_later_version_with_the_same_payer_still_stores(app, tenant_id):
    """The over-reach control, must stay green: a price change for the same
    payer is the next version, stored, and the run prices the next period on
    it."""
    seeded, _ = _alice_invoiced_for_may(app, tenant_id)
    v2 = simple_agreement(version=2, start_day=date(2026, 1, 5), payer_id="payer-alice",
                          monthly_price_minor=15000)
    _store(app, tenant_id, seeded, seeded.payer_uuids["payer-alice"], v2)
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (1,), (2,)
    ]
    (june,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 6, 1),
                          now=_at(date(2026, 5, 31), 0)).lines
    assert june.outcome is RunOutcome.ISSUED and june.payer_id == "payer-alice"


@pytest.mark.guarantee("G58")
def test_a_new_agreement_may_name_any_payer(app, tenant_id):
    """The refusal compares with the versions ALREADY STORED for the same
    identity: a different agreement id is a different agreement, at any payer."""
    seeded, acme = _alice_invoiced_for_may(app, tenant_id)
    fresh = simple_agreement(id="ag-acme", start_day=date(2026, 1, 5), payer_id="payer-acme",
                             vehicles=("FRESH-1",))
    _store(app, tenant_id, seeded, acme, fresh)
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(2,)]
