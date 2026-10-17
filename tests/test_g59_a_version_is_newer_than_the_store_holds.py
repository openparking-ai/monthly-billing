"""G59 -- a version is stored only if it is newer than every version the store
holds.

Registering a version's list releases every car not on it. So a late or
retried save of an OLDER version rewrote the garage's registered cars to the
older list while the newest version still named them: v5 lists CAR1 and CAR2,
a late v3 lists CAR1 alone, v5 is still the newest -- and CAR2's registration
was gone, so CAR2 was priced as a transient. The store now refuses, by name and
before any write, a version whose number is not above the newest it holds; the
same number again is refused the same way, where it used to reach the driver as
a raw unique violation.

The number check reads the newest stored version, so two saves at the same
moment -- v6 not yet committed, v4 arriving -- would both read the same newest
and both pass. A save therefore takes a transaction-scoped lock on the
agreement's identity before it reads: the second waits for the first and is
judged against what it committed.

Controls, each with a plant in ``scripts/fail_controls.py``: the refusal
removed (the late v3 is stored and CAR2 loses its registration); the lock
removed (the v4 saved while v6 is uncommitted is stored too).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import REFUSAL_VERSION_NOT_NEWER, Refused
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_agreement
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 10, 9, 0, tzinfo=TZ)


def _version(number: int, *vehicles: str):
    return simple_agreement(version=number, start_day=date(2026, 1, 5), vehicles=vehicles)


def _store(app, tenant_id, seeded, agreement):
    try:
        with tenant(app, tenant_id) as cursor:
            store_agreement(cursor, tenant_id, GARAGE, seeded.garage_uuid,
                            seeded.payer_uuids[agreement.payer_id], agreement, now=NOW)
    except BaseException:
        app.rollback()
        raise
    app.commit()


def _registered(app, tenant_id):
    return sorted(i for (i,) in query(
        app, tenant_id, "SELECT identity_normalised FROM vehicle_registrations"))


@pytest.mark.guarantee("G59")
def test_a_late_older_version_is_refused_by_name_and_the_newest_list_stands(app, tenant_id):
    """FO-L69's sequence: v5 is the newest, a late v3 arrives. Refused, nothing
    written, and both of v5's cars are still registered and covered."""
    seeded = seed(app, tenant_id, GARAGE, (_version(1, "CAR1"),))
    _store(app, tenant_id, seeded, _version(5, "CAR1", "CAR2"))
    assert _registered(app, tenant_id) == ["car1", "car2"]
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, seeded, _version(3, "CAR1"))
    assert refused.value.code == REFUSAL_VERSION_NOT_NEWER
    assert "version 3" in refused.value.detail and "version 5" in refused.value.detail
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (1,), (5,)
    ]
    assert _registered(app, tenant_id) == ["car1", "car2"]
    for car in ("CAR1", "CAR2"):
        assert covered_from_store(app, tenant_id, GARAGE.id, car, NOW).covered, car


@pytest.mark.guarantee("G59")
def test_the_same_version_again_is_refused_by_name_not_by_the_driver(app, tenant_id):
    """A retried save of the newest version: the same refusal by name, before
    the UNIQUE (tenant, agreement, version) has to fire."""
    seeded = seed(app, tenant_id, GARAGE, (_version(1, "CAR1"),))
    with pytest.raises(Refused) as refused:
        _store(app, tenant_id, seeded, _version(1, "CAR1", "CAR2"))
    assert refused.value.code == REFUSAL_VERSION_NOT_NEWER
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(1,)]
    assert _registered(app, tenant_id) == ["car1"]


@pytest.mark.guarantee("G59")
def test_a_newer_version_still_stores_and_numbers_may_skip(app, tenant_id):
    """The over-reach control, must stay green: v1 then v4 then v7 -- each
    newer than the newest stored -- are all stored, and the newest list is the
    register."""
    seeded = seed(app, tenant_id, GARAGE, (_version(1, "CAR1"),))
    _store(app, tenant_id, seeded, _version(4, "CAR1", "CAR2"))
    _store(app, tenant_id, seeded, _version(7, "CAR2"))
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (1,), (4,), (7,)
    ]
    assert _registered(app, tenant_id) == ["car2"]


def _ungranted_lock_of(owner, pid: int) -> str | None:
    """The kind of lock backend ``pid`` is waiting for, or None."""
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT locktype FROM pg_locks WHERE pid = %s AND NOT granted LIMIT 1", (pid,)
        )
        row = cursor.fetchone()
    return row[0] if row else None


@pytest.mark.guarantee("G59")
def test_two_saves_at_once_are_put_in_order_and_the_older_is_refused(app, owner, tenant_id):
    """The race the number check alone cannot see. v3 is the newest stored. One
    connection stores v6 (CAR1, CAR2) and has not committed; a second stores
    v4 (CAR1). Both would read v3 as the newest and both pass -- an older
    version accepted beside a newer one (measured without the lock: versions
    3, 4 and 6 all stored). The second save waits for the first's transaction
    instead; once v6 is committed it reads v6 as the newest and is refused by
    name. The end state is v6's: both cars registered and covered."""
    import threading
    import time

    from monthly_billing.store.records import store_agreement as store
    from store_harness import DSN, app_connection

    seeded = seed(app, tenant_id, GARAGE, (_version(3, "CAR1"),))
    payer = seeded.payer_uuids[_version(3, "CAR1").payer_id]
    second_pid: dict[str, int] = {}
    ready = threading.Event()
    outcome: dict[str, object] = {}

    def second_save():
        c = app_connection(DSN)
        try:
            second_pid["pid"] = c.info.backend_pid
            ready.set()
            with tenant(c, tenant_id) as cursor:
                store(cursor, tenant_id, GARAGE, seeded.garage_uuid, payer,
                      _version(4, "CAR1"), now=NOW)
            c.commit()
            outcome["second"] = "stored"
        except Refused as refusal:
            c.rollback()
            outcome["second"] = refusal
        except Exception as exc:  # noqa: BLE001 -- the shape of the failure is the finding
            c.rollback()
            outcome["second"] = exc
        finally:
            c.close()

    first = app_connection(DSN)
    worker = threading.Thread(target=second_save)
    try:
        with tenant(first, tenant_id) as cursor:
            store(cursor, tenant_id, GARAGE, seeded.garage_uuid, payer,
                  _version(6, "CAR1", "CAR2"), now=NOW)
        # v6 is written and NOT committed. Now the second save starts.
        worker.start()
        assert ready.wait(10), "the second connection never opened"
        waited_on = None
        deadline = time.monotonic() + 10
        while worker.is_alive() and time.monotonic() < deadline:
            waited_on = _ungranted_lock_of(owner, second_pid["pid"])
            if waited_on:
                break
            time.sleep(0.05)
        still_running = worker.is_alive()
        first.commit()
        worker.join(30)
        assert not worker.is_alive(), "the second save never finished"
    finally:
        first.close()

    second = outcome["second"]
    assert isinstance(second, Refused) and second.code == REFUSAL_VERSION_NOT_NEWER, second
    assert "version 4" in second.detail and "version 6" in second.detail
    assert query(app, tenant_id, "SELECT version FROM agreements ORDER BY version") == [
        (3,), (6,)
    ]
    assert _registered(app, tenant_id) == ["car1", "car2"]
    for car in ("CAR1", "CAR2"):
        assert covered_from_store(app, tenant_id, GARAGE.id, car, NOW).covered, car
    # The premise, after the outcome: the second save really overlapped the
    # first -- it was still waiting, on a lock, while v6 was uncommitted.
    assert still_running and waited_on == "advisory", (still_running, waited_on)
