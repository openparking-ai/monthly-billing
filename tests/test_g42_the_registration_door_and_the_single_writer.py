"""G42 -- one agreement, one registrar: the registration door and the switch.

An agreement states who writes its registrations. Under this module (the
default) nothing changes: the version's vehicle list is the register, and
``store_agreement`` keeps ``vehicle_registrations`` in step with it. Under an
OUTSIDE registrar the module writes none of those rows itself -- not the
writes, not the per-garage release, not the covered-set release -- the
document lists no vehicles, and the outside registrar puts cars on and takes
them off ONE AT A TIME through ``register_from_outside`` and
``release_from_outside``. Both halves refuse by name an agreement whose
registrations this module writes: two writers of one agreement's rows would
race, and the other path says so instead.

The door walks the path a version's list walks: the same fan-out over the
covered set, the same per-garage normalisation, every refusal at every garage
before a row changes anywhere, the same handover of a cancelled holder's row on
its day. And it ANSWERS with the identity as stored per covered garage, because
the two fixture garages disagree on what one plate is -- 'AB-123' and 'ab123'
are one row at the folded garage and two at the exact one -- and a registrar on
the other side that could not see the stored form could not reconcile.

The empty-list refusal is a ROUND TRIP: the document at write, and the row on
the way back through ``_as_stored``, which validates on construction. Both read
the mode from the row, and the test that stores an outside registrar's version
with no vehicles LOADS IT BACK, through the money loader and the access door.

Controls: the switch planted away (storing an outside version writes and
releases as if the list were its own -- the door's rows are deleted); the door
accepting any registrar; the empty list refused under an outside registrar;
the non-empty list accepted under one; the row's registrar not read on the way
back; the document key unknown; the door's answer raw instead of stored; the
release at the home only; the door's refusals skipped; the handover planted
away.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import (
    MULTI_HOME,
    MULTI_OTHER,
    agreement_document,
    multi_garage_agreement,
    outside_registrar_agreement,
    simple_agreement,
)
from monthly_billing.agreement import (
    KNOWN_KEYS,
    InvalidAgreement,
    Registrar,
    Status,
    load_agreement,
)
from monthly_billing.cli import main
from monthly_billing.findings import (
    REFUSAL_REGISTRAR_IS_THIS_MODULE,
    REFUSAL_VEHICLE_ALREADY_REGISTERED,
    Refused,
)
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    AgreementNotFound,
    RegisteredIdentity,
    load_agreements_at_garage,
    load_agreements_covering_garage,
    register_from_outside,
    registrations_at_garage,
    release_from_outside,
    store_agreement,
)
from store_harness import APP_PASSWORD, DSN, needs_postgres, query, seed_garages

HOME = MULTI_HOME()  # Denver, USD, FOLDED
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)

OUTSIDE = outside_registrar_agreement(start_day=date(2026, 1, 5))


# ---------------------------------------------------------------------------
# The document: the mode is stated, defaults to this module, and decides what
# the vehicle list may be. No database.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G42")
def test_the_registrar_is_stated_on_the_document_and_defaults_to_this_module():
    assert "registrar" in KNOWN_KEYS
    assert load_agreement(agreement_document()).registrar is Registrar.THIS_MODULE
    outside = load_agreement(agreement_document(registrar="outside", vehicles=[]))
    assert outside.registrar is Registrar.OUTSIDE and outside.vehicles == ()
    with pytest.raises(InvalidAgreement, match="registrar is 'elsewhere'"):
        load_agreement(agreement_document(registrar="elsewhere", vehicles=[]))
    # Stated, never inferred: an empty list on its own is not a mode.
    with pytest.raises(InvalidAgreement, match="lists no vehicles"):
        load_agreement(agreement_document(vehicles=[]))
    # And the dataclass refuses a value that is not the enum, by its own name.
    with pytest.raises(InvalidAgreement, match="has registrar 'outside'"):
        simple_agreement(registrar="outside", vehicles=())  # type: ignore[arg-type]


@pytest.mark.guarantee("G42")
def test_an_outside_registrars_document_lists_no_vehicles_and_a_list_is_refused_by_name():
    assert outside_registrar_agreement().vehicles == ()
    with pytest.raises(InvalidAgreement) as refused:
        outside_registrar_agreement(vehicles=("CAR001",))
    assert "outside registrar" in str(refused.value) and "lists 1 vehicle" in str(refused.value)
    # The default mode's refusal stands, word for word.
    with pytest.raises(InvalidAgreement) as still:
        multi_garage_agreement(vehicles=())
    assert "lists no vehicles" in str(still.value) and "covers nothing" in str(still.value)


# ---------------------------------------------------------------------------
# The store. Two garages that disagree on the identity rule, seeded per test.
# ---------------------------------------------------------------------------


def store_backed(test):
    """The store-backed half of this module needs a database; the pure half
    does not, so the marks go on the tests rather than on the module."""
    for mark in needs_postgres:
        test = mark(test)
    return test


def _seed(app, tenant_id, *agreements, now=DAY):
    return seed_garages(app, tenant_id, (HOME, OTHER), agreements, now=now)


def _rows(app, tenant_id, seeded, garage) -> list[tuple[str, str]]:
    with tenant(app, tenant_id) as cursor:
        rows = list(registrations_at_garage(cursor, seeded.garage_uuids[garage.id]))
    app.rollback()
    return rows


def _register(app, tenant_id, agreement_id, identity, *, now=DAY):
    with tenant(app, tenant_id) as cursor:
        try:
            stored = register_from_outside(cursor, tenant_id, agreement_id, identity, now=now)
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return stored


def _release(app, tenant_id, agreement_id, identity):
    with tenant(app, tenant_id) as cursor:
        try:
            released = release_from_outside(cursor, tenant_id, agreement_id, identity)
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return released


@pytest.mark.guarantee("G42")
@store_backed
def test_storing_an_outside_registrars_version_writes_no_registration_and_releases_none(
    app, tenant_id
):
    seeded = _seed(app, tenant_id, OUTSIDE)
    assert query(app, tenant_id, "SELECT registrar FROM agreements") == [("outside",)]
    assert query(app, tenant_id, "SELECT count(*) FROM agreement_vehicles") == [(0,)]
    assert _rows(app, tenant_id, seeded, HOME) == [] and _rows(app, tenant_id, seeded, OTHER) == []

    # The switch, the half that matters: with a car on through the door, a
    # NEW VERSION of the outside agreement releases nothing -- the version path
    # would have deleted every row not on its (empty) list.
    _register(app, tenant_id, OUTSIDE.id, "AB-123")
    v2 = outside_registrar_agreement(
        version=2, monthly_price_minor=13000, start_day=date(2026, 1, 5)
    )
    with tenant(app, tenant_id) as cursor:
        store_agreement(
            cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id],
            seeded.payer_uuids[OUTSIDE.payer_id], v2, now=DAY,
        )
    app.commit()
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", "ag-outside")]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", "ag-outside")]


@pytest.mark.guarantee("G42")
@store_backed
def test_an_outside_registrars_version_stored_with_no_vehicles_loads_back(app, tenant_id):
    """The round trip: the empty list is legal on the way in AND on the way
    out, at the one hydration site every loader shares."""
    seeded = _seed(app, tenant_id, OUTSIDE)
    with tenant(app, tenant_id) as cursor:
        (billed,) = load_agreements_at_garage(cursor, seeded.garage_uuids[HOME.id])
        (covering,) = load_agreements_covering_garage(cursor, seeded.garage_uuids[OTHER.id])
    app.rollback()
    for item in (billed, covering):
        assert item.agreement.id == OUTSIDE.id
        assert item.agreement.registrar is Registrar.OUTSIDE
        assert item.agreement.vehicles == ()
        assert set(item.agreement.covered_garage_ids) == {HOME.id, OTHER.id}


@pytest.mark.guarantee("G42")
@store_backed
def test_the_door_registers_at_every_covered_garage_under_its_rule_and_says_what_it_stored(
    app, tenant_id
):
    seeded = _seed(app, tenant_id, OUTSIDE)
    stored = _register(app, tenant_id, OUTSIDE.id, "AB-123")
    # Per covered garage, ordered by garage id: the exact garage kept the
    # dashes and the case, the folded one did not.
    assert stored == (
        RegisteredIdentity(OTHER.id, "AB-123"),
        RegisteredIdentity(HOME.id, "ab123"),
    )
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", "ag-outside")]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", "ag-outside")]
    # The same car spelt the other way: ONE row at the folded garage (already
    # this agreement's, left alone), a SECOND at the exact one -- and the
    # answer is what tells the registrar on the other side that it happened.
    again = _register(app, tenant_id, OUTSIDE.id, "ab123")
    assert again == (RegisteredIdentity(OTHER.id, "ab123"), RegisteredIdentity(HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", "ag-outside")]
    assert _rows(app, tenant_id, seeded, OTHER) == [
        ("AB-123", "ag-outside"), ("ab123", "ag-outside"),
    ]
    # And the silent split under the exact rule: a trailing space is a
    # second car there and the same car at the folded garage.
    split = _register(app, tenant_id, OUTSIDE.id, "ABC123 ")
    assert split == (RegisteredIdentity(OTHER.id, "ABC123 "), RegisteredIdentity(HOME.id, "abc123"))
    assert ("ABC123 ", "ag-outside") in _rows(app, tenant_id, seeded, OTHER)


@pytest.mark.guarantee("G42")
@store_backed
def test_the_door_refuses_at_any_covered_garage_before_it_writes_anywhere(app, tenant_id):
    # ag-B is this module's, homed at the EXACT garage, and holds the plate there.
    holder = simple_agreement(
        id="ag-B", payer_id="payer-b", garage_id=OTHER.id, vehicles=("SHARED-1",),
        start_day=date(2026, 1, 5),
    )
    seeded = _seed(app, tenant_id, OUTSIDE, holder)
    with pytest.raises(Refused) as refused:
        _register(app, tenant_id, OUTSIDE.id, "SHARED-1")
    assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
    assert f"garage {OTHER.id!r}" in refused.value.detail and "'ag-B'" in refused.value.detail
    assert "active" in refused.value.detail
    # All or none: the home, where nothing held the plate, got no row either.
    assert _rows(app, tenant_id, seeded, HOME) == []
    assert _rows(app, tenant_id, seeded, OTHER) == [("SHARED-1", "ag-B")]


@pytest.mark.guarantee("G42")
@store_backed
def test_both_halves_of_the_door_refuse_an_agreement_whose_registrations_this_module_writes(
    app, tenant_id
):
    mine = multi_garage_agreement(start_day=date(2026, 1, 5))
    seeded = _seed(app, tenant_id, mine)
    before = _rows(app, tenant_id, seeded, HOME), _rows(app, tenant_id, seeded, OTHER)
    assert before[0], "the premise: this module registered the version's list"
    for half, identity in ((_register, "NEW-CAR"), (_release, mine.vehicles[0])):
        with pytest.raises(Refused) as refused:
            half(app, tenant_id, mine.id, identity)
        assert refused.value.code == REFUSAL_REGISTRAR_IS_THIS_MODULE
        assert "'this_module'" in refused.value.detail
    assert (_rows(app, tenant_id, seeded, HOME), _rows(app, tenant_id, seeded, OTHER)) == before
    # And an agreement the store does not hold is not found, not invented.
    with pytest.raises(AgreementNotFound):
        _register(app, tenant_id, "ag-nowhere", "NEW-CAR")


@pytest.mark.guarantee("G42")
@store_backed
def test_the_door_releases_one_vehicle_at_every_covered_garage(app, tenant_id):
    seeded = _seed(app, tenant_id, OUTSIDE)
    _register(app, tenant_id, OUTSIDE.id, "AB-123")
    _register(app, tenant_id, OUTSIDE.id, "CD-456")
    released = _release(app, tenant_id, OUTSIDE.id, "ab-123")  # spelt as the registrar has it
    # The answer is what was looked for, per garage, in that garage's form.
    assert released == (
        RegisteredIdentity(OTHER.id, "ab-123"), RegisteredIdentity(HOME.id, "ab123"),
    )
    # Folded: 'ab-123' IS 'AB-123', released. Exact: it is another car, and
    # 'AB-123' stays -- the answer above is how the registrar learns that.
    assert _rows(app, tenant_id, seeded, HOME) == [("cd456", "ag-outside")]
    assert _rows(app, tenant_id, seeded, OTHER) == [
        ("AB-123", "ag-outside"), ("CD-456", "ag-outside"),
    ]
    gone = _release(app, tenant_id, OUTSIDE.id, "AB-123")
    assert gone == (RegisteredIdentity(OTHER.id, "AB-123"), RegisteredIdentity(HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, OTHER) == [("CD-456", "ag-outside")]
    # There is no end date: the row is gone, and the car is another agreement's
    # to register.
    assert _rows(app, tenant_id, seeded, HOME) == [("cd456", "ag-outside")]


@pytest.mark.guarantee("G42")
@store_backed
def test_a_cancelled_holders_row_passes_to_the_outside_registrars_agreement_on_its_day(
    app, tenant_id
):
    """T3: a car moving from an agreement this module writes to an outside
    registrar's is the ordinary case -- the same handover, the same day, the
    same row."""
    frees = date(2026, 6, 1)
    cancelled = simple_agreement(
        id="ag-A", payer_id="payer-a", garage_id=HOME.id, vehicles=("HAND-1",),
        start_day=date(2026, 1, 5), status=Status.CANCELLED, cancelled_effective_day=frees,
    )
    seeded = _seed(app, tenant_id, OUTSIDE, cancelled)
    (row_before,) = query(
        app, tenant_id, "SELECT id FROM vehicle_registrations WHERE identity_normalised = 'hand1'"
    )
    eve = datetime(2026, 5, 31, 23, 0, tzinfo=HOME_TZ)
    with pytest.raises(Refused) as refused:
        _register(app, tenant_id, OUTSIDE.id, "HAND-1", now=eve)
    assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
    assert "'ag-A'" in refused.value.detail and str(frees) in refused.value.detail
    assert _rows(app, tenant_id, seeded, HOME) == [("hand1", "ag-A")]

    day = datetime(2026, 6, 1, 0, 30, tzinfo=HOME_TZ)
    passed = _register(app, tenant_id, OUTSIDE.id, "HAND-1", now=day)
    assert passed == (RegisteredIdentity(OTHER.id, "HAND-1"), RegisteredIdentity(HOME.id, "hand1"))
    assert _rows(app, tenant_id, seeded, HOME) == [("hand1", "ag-outside")]
    assert _rows(app, tenant_id, seeded, OTHER) == [("HAND-1", "ag-outside")]
    # The same row, handed over -- not a delete and an insert.
    (row_after,) = query(
        app, tenant_id,
        "SELECT id FROM vehicle_registrations WHERE identity_normalised = 'hand1'",
    )
    assert row_after == row_before


@pytest.mark.guarantee("G42")
@store_backed
def test_the_self_written_path_is_untouched(app, tenant_id):
    """Fixture 3: an agreement that says nothing about its registrar is this
    module's, its list is its register, and its row says so."""
    mine = multi_garage_agreement(start_day=date(2026, 1, 5))
    assert mine.registrar is Registrar.THIS_MODULE
    seeded = _seed(app, tenant_id, mine)
    assert query(app, tenant_id, "SELECT registrar FROM agreements") == [("this_module",)]
    assert len(_rows(app, tenant_id, seeded, HOME)) == len(mine.vehicles)
    assert len(_rows(app, tenant_id, seeded, OTHER)) == len(mine.vehicles)
    listed = len(mine.vehicles)
    assert query(app, tenant_id, "SELECT count(*) FROM agreement_vehicles") == [(listed,)]


@pytest.mark.guarantee("G42")
@store_backed
def test_the_command_line_registers_and_releases_and_prints_the_stored_forms(
    app, tenant_id
):
    seeded = _seed(app, tenant_id, OUTSIDE, multi_garage_agreement(start_day=date(2026, 1, 5)))
    # As the APPLICATION role, as the command line is run: the door reads the
    # agreement by its id under the tenant policy, and the owner bypasses it.
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    common = ["--tenant", str(tenant_id), "--dsn", dsn, "--agreement", OUTSIDE.id]

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["register-vehicle", *common, "--vehicle", "AB-123", "--at", DAY.isoformat()])
    assert code == 0, err.getvalue()
    assert out.getvalue().splitlines() == [
        f"vehicle registered to agreement {OUTSIDE.id}",
        f"  at garage {OTHER.id}: AB-123",
        f"  at garage {HOME.id}: ab123",
    ]
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", "ag-outside")] + [
        (HOME.normalise_identity(v), "ag-0001") for v in sorted(multi_garage_agreement().vehicles)
    ]

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["release-vehicle", *common, "--vehicle", "AB-123"])
    assert code == 0, err.getvalue()
    assert out.getvalue().splitlines()[0] == f"vehicle released from agreement {OUTSIDE.id}"
    assert ("ab123", "ag-outside") not in _rows(app, tenant_id, seeded, HOME)

    # The refusal, by code, exit 2 -- and an unknown agreement is NOT FOUND.
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["register-vehicle", "--tenant", str(tenant_id), "--dsn", dsn,
                     "--agreement", "ag-0001", "--vehicle", "AB-123"])
    assert code == 2 and REFUSAL_REGISTRAR_IS_THIS_MODULE in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["release-vehicle", "--tenant", str(tenant_id), "--dsn", dsn,
                     "--agreement", "ag-nowhere", "--vehicle", "AB-123"])
    assert code == 2 and err.getvalue().startswith("NOT FOUND")
