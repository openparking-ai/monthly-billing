"""G48 -- the register can be read by garage, by any reader, without an
agreement id.

``show-register`` (G45) takes an agreement id. A reader that has to hold every
entitlement at a garage -- a lane refreshing what it decides from -- was never
told one, and asking ``covered-in-store`` once per known vehicle is a cost that
grows with every car ever seen. ``show-garage-register``
(``show_garage_register`` beneath it, the same code) answers from the garage:
every ``vehicle_registrations`` row at it as {identity as stored, agreement},
and for every agreement those rows name, from its LATEST version -- picked by
the same helper the door and G45 pick theirs with -- the id, version,
registrar, status and cancellation day; the agreements whose latest version
does not cover the garage; and the ids the rows name that the tenant holds no
version of. Five keys, derived from the answer class.

**IT VALIDATES NOTHING IT DOES NOT RETURN**, like G45: no ``Agreement`` and no
``Garage`` is built; a garage stored with an unreadable option or a version the
loaders refuse still shows its register, and the control is that the loaders
and the door DO refuse on the same rows. It takes no instant, derives nothing,
writes nothing, and sorts in Python by code point.

Controls, each with a plant in ``scripts/fail_controls.py``: the read given
its own version rule; a write planted into the read; the garage's tenant
predicate planted away and the rows' tenant predicate planted away (the policy
off shows another tenant's garage of the same id); rows read through the
covered set; a row naming an unknown agreement refusing the whole read; an
agreement not covering the garage left unnamed; the read routed through
``load_garage``; the Python sorts removed; a sixth key; the refusal swallowed;
a line on stderr.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import (
    MULTI_HOME,
    MULTI_OTHER,
    multi_garage_agreement,
    outside_registrar_agreement,
)
from monthly_billing.agreement import InvalidAgreement, Status
from monthly_billing.cli import main
from monthly_billing.garage import BillingDay, Garage, IdentityRule
from monthly_billing.localday import UnknownTimezone
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    GarageNotFound,
    GarageRegister,
    GarageRegisterEntry,
    RegisteredAgreement,
    _latest_version_of,
    _outside_registrars_covered_set,
    load_agreements_at_garage,
    load_garage,
    register_from_outside,
    show_garage_register,
    show_register,
    store_agreement,
)
from store_harness import APP_PASSWORD, DSN, needs_postgres, new_tenant, query, seed_garages
from test_g45_the_register_can_be_read import (
    NEVER_TRAVELS,
    _digests,
    _own_writes,
    _raw,
    _row_security_off,
)

HOME = MULTI_HOME()  # Denver, USD, FOLDED
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)
OUTSIDE_ID = "ag-outside"

#: Five keys, the tenant NOT among them -- the reader supplied it, and an
#: answer does not echo its own arguments beyond the garage id that names the
#: thing shown.
THE_FIVE = (
    "garage",
    "registrations",
    "agreements",
    "agreements_not_covering_garage",
    "agreements_not_found",
)
THE_AGREEMENT_FIVE = ("agreement", "version", "registrar", "status", "cancelled_effective_day")

#: G45's list, and what a read BY GARAGE must not echo either: the home garage
#: and the covered set are where else the agreement answers, not what it holds
#: here.
NEVER_TRAVELS_BY_GARAGE = (*NEVER_TRAVELS, "home", "covered")


def outside(**overrides: object):
    return outside_registrar_agreement(**{"start_day": date(2026, 1, 5), **overrides})


def store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


def _seed(app, tenant_id, *agreements, garages=(HOME, OTHER), now=DAY):
    return seed_garages(app, tenant_id, garages, agreements, now=now)


def _register(app, tenant_id, agreement_id, identity, *, now=DAY):
    with tenant(app, tenant_id) as cursor:
        try:
            stored = register_from_outside(cursor, tenant_id, agreement_id, identity, now=now)
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return stored


def _store(app, tenant_id, seeded, agreement, *, now=DAY):
    home = HOME if agreement.garage_id == HOME.id else None
    assert home is not None, "these tests home every agreement at HOME"
    with tenant(app, tenant_id) as cursor:
        try:
            store_agreement(
                cursor, tenant_id, home, seeded.garage_uuids[home.id],
                seeded.payer_uuids[agreement.payer_id], agreement, now=now,
            )
        except BaseException:
            app.rollback()
            raise
    app.commit()


def _read(app, tenant_id, garage_id=HOME.id) -> GarageRegister:
    """The library read, COMMITTED afterwards rather than rolled back: the
    write-nothing instrument must see anything the read wrote."""
    with tenant(app, tenant_id) as cursor:
        try:
            register = show_garage_register(cursor, tenant_id, garage_id)
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return register


def _cli(tenant_id, garage_id, *extra) -> tuple[int, str, str]:
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([
            "show-garage-register", "--tenant", str(tenant_id), "--dsn", dsn,
            "--garage", garage_id, *extra,
        ])
    return code, out.getvalue(), err.getvalue()


def rows(*pairs: tuple[str, str]) -> tuple[GarageRegisterEntry, ...]:
    return tuple(GarageRegisterEntry(identity_normalised=i, agreement=a) for i, a in pairs)


def agreement_entry(agreement_id: str, **overrides) -> RegisteredAgreement:
    return RegisteredAgreement(**{
        "agreement": agreement_id, "version": 1, "registrar": "outside", "status": "active",
        "cancelled_effective_day": None, **overrides,
    })


# ---------------------------------------------------------------------------
# The answer's shape. No database.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G48")
def test_the_answer_is_five_fields_and_nothing_more_travels():
    """Derived from the classes, so a field added to either is seen here the
    day it exists, and the document the command line prints has exactly the
    same keys."""
    assert tuple(f.name for f in fields(GarageRegister)) == THE_FIVE
    assert tuple(f.name for f in fields(RegisteredAgreement)) == THE_AGREEMENT_FIVE
    assert tuple(f.name for f in fields(GarageRegisterEntry)) == (
        "identity_normalised", "agreement")
    register = GarageRegister(
        garage="g-home",
        registrations=rows(("AB-123", "ag-b"), ("ab123", "ag-a")),
        agreements=(
            agreement_entry("ag-a", version=3, status="cancelled",
                            cancelled_effective_day=date(2026, 2, 1)),
            agreement_entry("ag-b", registrar="this_module"),
        ),
        agreements_not_covering_garage=("ag-b",),
        agreements_not_found=("ag-gone",),
    )
    document = register.as_document()
    assert tuple(document) == THE_FIVE
    assert document["registrations"] == [
        {"identity_normalised": "AB-123", "agreement": "ag-b"},
        {"identity_normalised": "ab123", "agreement": "ag-a"},
    ]
    assert document["agreements"] == [
        {"agreement": "ag-a", "version": 3, "registrar": "outside", "status": "cancelled",
         "cancelled_effective_day": "2026-02-01"},
        {"agreement": "ag-b", "version": 1, "registrar": "this_module", "status": "active",
         "cancelled_effective_day": None},
    ]
    assert all(tuple(entry) == THE_AGREEMENT_FIVE for entry in document["agreements"])
    for fragment in NEVER_TRAVELS_BY_GARAGE:
        assert not any(fragment in key for key in document), (fragment, list(document))
        assert not any(fragment in key for entry in document["registrations"] for key in entry)
        assert not any(fragment in key for entry in document["agreements"] for key in entry)
    # And the JSON the verb prints is this document with sorted keys, nothing added.
    assert json.loads(json.dumps(document, sort_keys=True)) == document


@pytest.mark.guarantee("G48")
def test_the_read_builds_no_engine_value_and_picks_its_version_where_the_door_does():
    """Statically: the read names no loader and no engine constructor, and
    the door, G45's read and this read all name the one latest-version
    helper. The behavioural halves are the store tests below."""
    read_names = set(show_garage_register.__code__.co_names)
    door_names = set(_outside_registrars_covered_set.__code__.co_names)
    sibling_names = set(show_register.__code__.co_names)
    assert "_latest_version_of" in read_names
    assert "_latest_version_of" in door_names and "_latest_version_of" in sibling_names
    assert "_covered_garage_ids" in read_names and "_covered_garage_ids" in sibling_names
    for validator in (
        "Agreement", "Garage", "load_garage", "_as_stored", "load_agreements_at_garage",
        "load_agreements_covering_garage", "load_agreements_on_invoice", "Registrar",
        "Status", "zone", "_registrar_must_be",
    ):
        assert validator not in read_names, validator
    # The garage and the rows are each read by the tenant AND the id.
    statements = [c for c in show_garage_register.__code__.co_consts
                  if isinstance(c, str) and c != show_garage_register.__doc__]
    sql = " ".join(statements)
    assert "SELECT id FROM garages WHERE external_id = %s AND tenant_id = %s" in sql
    assert "WHERE r.tenant_id = %s AND r.garage_id = %s" in sql
    assert "ORDER BY" not in sql, "the queries carry no ORDER BY; the sorts are Python's"


# ---------------------------------------------------------------------------
# The store.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G48")
@store_backed
def test_the_read_shows_the_register_at_the_garage_for_either_registrar(app, tenant_id):
    """Two garages that disagree on the identity rule; an outside-registered
    agreement and a self-written one, both covering both. Asked at each
    garage: that garage's rows in that garage's stored form, both agreements
    named with their registrar, and the command line prints the same
    document -- JSON with sorted keys on stdout, exit 0, nothing on stderr."""
    mine = multi_garage_agreement(start_day=date(2026, 1, 5), vehicles=("CAR-9",))
    _seed(app, tenant_id, outside(), mine)
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    expected_agreements = (
        agreement_entry(mine.id, registrar="this_module"),
        agreement_entry(OUTSIDE_ID),
    )
    at_home = _read(app, tenant_id, HOME.id)
    assert at_home == GarageRegister(
        garage=HOME.id,
        registrations=rows(("ab123", OUTSIDE_ID), ("car9", mine.id), ("cd456", OUTSIDE_ID)),
        agreements=expected_agreements,
        agreements_not_covering_garage=(), agreements_not_found=(),
    )
    at_other = _read(app, tenant_id, OTHER.id)
    assert at_other == GarageRegister(
        garage=OTHER.id,
        registrations=rows(("AB-123", OUTSIDE_ID), ("CAR-9", mine.id), ("CD-456", OUTSIDE_ID)),
        agreements=expected_agreements,
        agreements_not_covering_garage=(), agreements_not_found=(),
    )
    # the same rows G45 shows, read the other way round
    with tenant(app, tenant_id) as cursor:
        by_agreement = show_register(cursor, tenant_id, OUTSIDE_ID)
    app.rollback()
    assert {(e.garage_id, e.identity_normalised) for e in by_agreement.registrations} == {
        (HOME.id, "ab123"), (HOME.id, "cd456"), (OTHER.id, "AB-123"), (OTHER.id, "CD-456")}
    for garage, register in ((HOME, at_home), (OTHER, at_other)):
        code, out, err = _cli(tenant_id, garage.id)
        assert (code, err) == (0, "")
        assert out == json.dumps(register.as_document(), sort_keys=True, indent=2) + "\n"
    assert json.loads(_cli(tenant_id, HOME.id)[1])["registrations"][0] == {
        "identity_normalised": "ab123", "agreement": OUTSIDE_ID}


@pytest.mark.guarantee("G48")
@store_backed
def test_a_cancelled_agreement_past_its_day_is_shown_with_its_rows(app, tenant_id):
    """No clock. The day has passed; the rows are still stored; the read at
    the garage shows the latest version's ``cancelled`` and its day beside
    the rows -- and a second, live agreement is unaffected."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(
        version=2, status=Status.CANCELLED, cancelled_effective_day=date(2026, 2, 1),
    ))
    register = _read(app, tenant_id)
    assert register.registrations == rows(("ab123", OUTSIDE_ID))
    assert register.agreements == (agreement_entry(
        OUTSIDE_ID, version=2, status="cancelled", cancelled_effective_day=date(2026, 2, 1)),)
    printed = json.loads(_cli(tenant_id, HOME.id)[1])
    assert printed["agreements"] == [{
        "agreement": OUTSIDE_ID, "version": 2, "registrar": "outside", "status": "cancelled",
        "cancelled_effective_day": "2026-02-01"}]


@pytest.mark.guarantee("G48")
@store_backed
def test_an_agreement_whose_latest_version_no_longer_covers_the_garage_is_named(
    app, tenant_id
):
    """The row was registered when both garages were covered; version 2
    drops the other garage. The module releases the row there, so the state
    is reached the way G45's T4 reaches it -- a raw INSERT as the app role,
    the row a system past the module would leave -- and the read at that
    garage shows the row and names the agreement."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(version=2, covered_garage_ids=(HOME.id,)))
    assert _read(app, tenant_id, OTHER.id).registrations == (), "the module released the row"
    _raw(
        app, tenant_id,
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, seeded.garage_uuids[OTHER.id], "STRAY-1", OUTSIDE_ID, DAY),
    )
    register = _read(app, tenant_id, OTHER.id)
    assert register.registrations == rows(("STRAY-1", OUTSIDE_ID))
    assert register.agreements == (agreement_entry(OUTSIDE_ID, version=2),)
    assert register.agreements_not_covering_garage == (OUTSIDE_ID,)
    assert register.agreements_not_found == ()
    # and at the garage it does cover, nothing is named
    at_home = _read(app, tenant_id, HOME.id)
    assert at_home.registrations == rows(("ab123", OUTSIDE_ID))
    assert at_home.agreements_not_covering_garage == ()
    assert json.loads(_cli(tenant_id, OTHER.id)[1])["agreements_not_covering_garage"] == [
        OUTSIDE_ID]


@pytest.mark.guarantee("G48")
@store_backed
def test_a_row_naming_an_agreement_the_store_does_not_hold_is_shown_and_the_id_named(
    app, tenant_id
):
    """``agreement_external_id`` is text keyed to nothing: the catalogue says
    so (the premise), so a raw row can name an id with no version. The read
    shows the row, names the id in ``agreements_not_found``, lists no
    agreement for it, and does not refuse -- beside a known agreement's row
    as the control that the rest of the register is intact. G45's read of
    that id is NOT FOUND (the control that the id really names nothing)."""
    from monthly_billing.store.records import AgreementNotFound

    referenced = query(
        app, tenant_id,
        "SELECT DISTINCT confrelid::regclass::text FROM pg_constraint "
        "WHERE conrelid = 'vehicle_registrations'::regclass AND contype = 'f' ORDER BY 1",
    )
    assert referenced == [("garages",), ("tenants",)], referenced
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _raw(
        app, tenant_id,
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, seeded.garage_uuids[HOME.id], "ghost1", "ag-ghost", DAY),
    )
    register = _read(app, tenant_id, HOME.id)
    assert register.registrations == rows(("ab123", OUTSIDE_ID), ("ghost1", "ag-ghost"))
    assert register.agreements == (agreement_entry(OUTSIDE_ID),)
    assert register.agreements_not_found == ("ag-ghost",)
    assert register.agreements_not_covering_garage == ()
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(AgreementNotFound):
            show_register(cursor, tenant_id, "ag-ghost")
    app.rollback()
    code, out, err = _cli(tenant_id, HOME.id)
    assert (code, err) == (0, "") and json.loads(out)["agreements_not_found"] == ["ag-ghost"]


@pytest.mark.guarantee("G48")
@store_backed
def test_no_such_garage_is_not_found_and_no_rows_is_an_empty_register(app, tenant_id):
    """The door's refusal shape, unchanged -- NOT FOUND on stderr, exit 2,
    nothing on stdout; and a garage with no rows is an answer."""
    _seed(app, tenant_id, outside())
    register = _read(app, tenant_id, OTHER.id)
    assert register == GarageRegister(OTHER.id, (), (), (), ())
    code, out, err = _cli(tenant_id, OTHER.id)
    assert code == 0 and json.loads(out) == register.as_document() and err == ""
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(GarageNotFound):
            show_garage_register(cursor, tenant_id, "garage-nowhere")
    app.rollback()
    code, out, err = _cli(tenant_id, "garage-nowhere")
    assert code == 2 and out == ""
    assert err.startswith("NOT FOUND — ") and "'garage-nowhere'" in err and "Traceback" not in err


@pytest.mark.guarantee("G48")
@store_backed
def test_another_tenants_garage_of_the_same_id_is_not_read_with_the_policy_on_and_off(
    app, owner, tenant_id
):
    """Two tenants, the same garage ids and the same agreement id -- the other
    tenant's at a HIGHER version, with cars of its own. With the row policy
    ON the app role cannot see them (the control that the policy is doing its
    job); with it OFF on the four tables the app role sees both tenants' rows,
    and the read still shows only the tenant it was asked for: its garage's
    rows, its version 1."""
    other_tenant = new_tenant(owner)
    _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "MINE-1")
    seeded_other = _seed(app, other_tenant, outside())
    _register(app, other_tenant, OUTSIDE_ID, "THEIRS-1")
    _store(app, other_tenant, seeded_other, outside(version=2))
    expected = GarageRegister(
        garage=HOME.id, registrations=rows(("mine1", OUTSIDE_ID)),
        agreements=(agreement_entry(OUTSIDE_ID),),
        agreements_not_covering_garage=(), agreements_not_found=(),
    )

    def app_sees():
        garages = query(app, tenant_id, "SELECT count(*) FROM garages WHERE external_id = %s",
                        (HOME.id,))
        rows_ = query(app, tenant_id, "SELECT identity_normalised FROM vehicle_registrations")
        return garages[0][0], sorted(i for (i,) in rows_)

    assert app_sees() == (1, ["MINE-1", "mine1"])
    assert _read(app, tenant_id) == expected

    tables = ("agreements", "agreement_garages", "garages", "vehicle_registrations")
    with _row_security_off(owner, tables):
        count, seen = app_sees()
        assert count >= 2 and {"THEIRS-1", "theirs1", "MINE-1", "mine1"} <= set(seen)
        assert _read(app, tenant_id) == expected
        assert json.loads(_cli(tenant_id, HOME.id)[1])["agreements"][0]["version"] == 1
        theirs = _read(app, other_tenant)
        assert theirs.registrations == rows(("theirs1", OUTSIDE_ID))
        assert theirs.agreements == (agreement_entry(OUTSIDE_ID, version=2),)
    assert app_sees() == (1, ["MINE-1", "mine1"])
    assert _read(app, tenant_id) == expected


@pytest.mark.guarantee("G48")
@store_backed
def test_an_unreadable_garage_or_version_still_shows_its_register(app, tenant_id):
    """The garage's timezone set raw to a zone that does not exist; the
    latest version given two overlapping pauses raw. The loaders and the
    door refuse on those rows (the control); the read at that garage answers
    in full."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    home_uuid = seeded.garage_uuids[HOME.id]
    _raw(
        app, tenant_id,
        "UPDATE garages SET timezone = 'Nowhere/Nowhere' WHERE external_id = %s", (OTHER.id,),
    )
    version_uuid = seeded.agreement_uuids[OUTSIDE_ID]
    overlapping = ((date(2026, 3, 1), date(2026, 3, 20)), (date(2026, 3, 10), date(2026, 3, 30)))
    for from_day, until_day in overlapping:
        _raw(
            app, tenant_id,
            "INSERT INTO agreement_pauses (tenant_id, agreement_id, from_day, until_day) "
            "VALUES (%s, %s, %s, %s)", (tenant_id, version_uuid, from_day, until_day),
        )
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(UnknownTimezone):
            load_garage(cursor, OTHER.id)
        with pytest.raises(InvalidAgreement, match="pause"):
            load_agreements_at_garage(cursor, home_uuid)
        with pytest.raises(UnknownTimezone):
            register_from_outside(cursor, tenant_id, OUTSIDE_ID, "CD-456", now=DAY)
    app.rollback()
    register = _read(app, tenant_id, OTHER.id)
    assert register == GarageRegister(
        garage=OTHER.id, registrations=rows(("AB-123", OUTSIDE_ID)),
        agreements=(agreement_entry(OUTSIDE_ID),),
        agreements_not_covering_garage=(), agreements_not_found=(),
    )
    code, out, err = _cli(tenant_id, OTHER.id)
    assert (code, err) == (0, "") and json.loads(out) == register.as_document()


@pytest.mark.guarantee("G48")
@store_backed
def test_the_lists_are_sorted_by_code_point_in_python_and_not_by_the_database(app, tenant_id):
    """Ids on which code-point order and en_US order disagree, on both axes:
    identities 'AB-123' before 'ab123' by code point and after it under
    glibc's en_US; agreement ids 'ag-Zeta' before 'ag-alpha' by code point
    ('Z' < 'a') and after it under en_US. The premise asserted first: the
    order the rows come off the heap is NOT the published order."""
    exact = Garage(id="garage-exact", timezone=HOME.timezone, currency="USD",
                   billing_day=BillingDay.LAST_DAY_OF_MONTH, payment_grace_days=5,
                   identity_rule=IdentityRule.EXACT)
    assert sorted(["ab123", "AB-123"]) == ["AB-123", "ab123"]
    assert sorted(["ag-alpha", "ag-Zeta"]) == ["ag-Zeta", "ag-alpha"]
    assert sorted(["ag-alpha", "ag-Zeta"], key=str.lower) == ["ag-alpha", "ag-Zeta"]
    alpha = outside(id="ag-alpha", payer_id="payer-a", garage_id=exact.id,
                    covered_garage_ids=(exact.id,))
    zeta = outside(id="ag-Zeta", payer_id="payer-z", garage_id=exact.id,
                   covered_garage_ids=(exact.id,))
    seed_garages(app, tenant_id, (exact,), (alpha, zeta), now=DAY)
    _register(app, tenant_id, "ag-alpha", "ab123")
    _register(app, tenant_id, "ag-Zeta", "AB-123")
    _register(app, tenant_id, "ag-alpha", "AA-000")
    published = _read(app, tenant_id, exact.id)
    assert published.registrations == rows(
        ("AA-000", "ag-alpha"), ("AB-123", "ag-Zeta"), ("ab123", "ag-alpha"))
    assert [a.agreement for a in published.agreements] == ["ag-Zeta", "ag-alpha"]
    heap = query(
        app, tenant_id,
        "SELECT r.identity_normalised, r.agreement_external_id FROM vehicle_registrations r "
        "JOIN garages g ON g.id = r.garage_id WHERE g.external_id = %s", (exact.id,),
    )
    assert [(e.identity_normalised, e.agreement) for e in published.registrations] != heap, heap
    printed = json.loads(_cli(tenant_id, exact.id)[1])
    assert [a["agreement"] for a in printed["agreements"]] == ["ag-Zeta", "ag-alpha"]
    assert printed["registrations"][0]["identity_normalised"] == "AA-000"


@pytest.mark.guarantee("G48")
@store_backed
def test_the_read_writes_nothing(app, owner, tenant_id):
    """G45's two instruments: the row count and a digest of every row of
    every table (xmin included) before and after the library read and the
    command line; and the app backend's own tuple counters inside the read's
    transaction, which see a write the digest cannot. The positive control
    first: the door's write is seen by both."""
    _seed(app, tenant_id, outside())
    before = _digests(owner)
    _stored, moved = _own_writes(
        app, tenant_id,
        lambda cursor: register_from_outside(cursor, tenant_id, OUTSIDE_ID, "AB-123", now=DAY),
    )
    after_write = _digests(owner)
    assert after_write != before, "the premise: the digest sees a write"
    assert moved == 2, "the premise: the backend's own counters see the door's two rows"

    still = _digests(owner)
    register, moved = _own_writes(
        app, tenant_id, lambda cursor: show_garage_register(cursor, tenant_id, HOME.id),
    )
    assert register.registrations == rows(("ab123", OUTSIDE_ID))
    assert moved == 0, "the read inserted, updated or deleted a row, committed or not"
    assert _digests(owner) == still
    code, _out, err = _cli(tenant_id, HOME.id)
    assert (code, err) == (0, "")
    assert _digests(owner) == still


@pytest.mark.guarantee("G48")
@store_backed
def test_the_version_shown_is_the_one_the_door_and_g45_pick(app, tenant_id):
    """Two versions of the agreement in the store; the read at the garage
    names version 2, the same row ``_latest_version_of`` hands the door and
    ``show_register`` -- one rule, three readers."""
    seeded = _seed(app, tenant_id, outside(covered_garage_ids=(HOME.id,)))
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(version=2))
    register = _read(app, tenant_id, HOME.id)
    assert register.agreements == (agreement_entry(OUTSIDE_ID, version=2),)
    with tenant(app, tenant_id) as cursor:
        latest = _latest_version_of(cursor, tenant_id, OUTSIDE_ID)
        by_agreement = show_register(cursor, tenant_id, OUTSIDE_ID)
    app.rollback()
    assert latest.version == 2 and by_agreement.version == 2
    assert query(app, tenant_id, "SELECT count(*) FROM agreements WHERE external_id = %s",
                 (OUTSIDE_ID,)) == [(2,)], "the premise: two versions are stored"
