"""G45 -- the register can be read, by any reader.

Until this round nothing outside the module could read which cars an agreement
has registered. Eleven verbs, and none returned an agreement's registrations or
its covered set; the two plain reads in ``records`` were unreachable from the
command line, keyed by the garage's internal uuid rather than the agreement,
and outside the contract; and the door's own answer says what ONE call stored,
never a row the reader did not put there -- which is the one row a
reconciliation exists to find.

``show-register`` (``show_register`` beneath it, the same code) answers from
the agreement's LATEST version, picked by the same helper the registration door
picks its own with: the agreement, its version, registrar, status and
cancellation day, the home garage, the covered garages, every registration row
naming the agreement at ANY garage as {garage, identity as stored}, and the
garages of rows the latest version does not cover. Nine keys, and the field set
is derived from the answer class so a tenth is seen the day it exists.

**IT VALIDATES NOTHING IT DOES NOT RETURN.** No ``Agreement`` and no ``Garage``
is built: every field is a column's own value under that column's constraint,
so a garage stored with an unreadable option, or a version the loaders refuse,
still shows its register in full -- and the control is that the loaders and the
door DO refuse on the same rows. It takes no instant, derives nothing, writes
nothing, and sorts in Python by code point because the database's order on
text is the machine's collation (the G42 lesson: green here, red in CI).

Controls: the read given its own version rule; a write planted into the read;
the tenant predicate planted away (the policy off shows another tenant's
version); the registrations' tenant predicate planted away; rows outside the
covered set hidden; the read routed through ``load_garage``; the Python sort
removed; a tenth key; the refusal swallowed.
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import fields
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from fixtures import (
    MULTI_HOME,
    MULTI_OTHER,
    multi_garage_agreement,
    outside_registrar_agreement,
)
from monthly_billing.agreement import InvalidAgreement, Registrar, Status
from monthly_billing.cli import main
from monthly_billing.garage import BillingDay, Garage, IdentityRule
from monthly_billing.localday import UnknownTimezone
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    AgreementNotFound,
    AgreementRegister,
    RegisteredIdentity,
    _latest_version_of,
    _outside_registrars_covered_set,
    load_agreements_at_garage,
    load_garage,
    register_from_outside,
    show_register,
    store_agreement,
)
from store_harness import APP_PASSWORD, DSN, needs_postgres, new_tenant, query, seed_garages

HOME = MULTI_HOME()  # Denver, USD, FOLDED
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)
OUTSIDE_ID = "ag-outside"

#: R2, and A1.1 of the brief: nine keys, the tenant NOT among them -- the
#: reader supplied it, and an answer does not echo its own arguments beyond
#: the agreement id that names the thing shown.
THE_NINE = (
    "agreement",
    "version",
    "registrar",
    "status",
    "cancelled_effective_day",
    "home_garage",
    "covered_garages",
    "registrations",
    "garages_not_covered",
)

#: What must NOT travel (R2), as fragments a key would contain.
NEVER_TRAVELS = (
    "price", "payer", "spots", "fee", "pause", "mandate", "access", "start",
    "vehicles", "registered_at", "invoice", "payment", "tenant",
)


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


def _read(app, tenant_id, agreement_id=OUTSIDE_ID) -> AgreementRegister:
    """The library read, COMMITTED afterwards rather than rolled back: T1's
    instrument must see anything the read wrote."""
    with tenant(app, tenant_id) as cursor:
        try:
            register = show_register(cursor, tenant_id, agreement_id)
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return register


def _raw(app, tenant_id, statement, parameters=()):
    """A raw write as the APPLICATION role inside the tenant's context, the
    route a system past the module would take. Committed."""
    with tenant(app, tenant_id) as cursor:
        try:
            cursor.execute(statement, parameters)
        except BaseException:
            app.rollback()
            raise
    app.commit()


def _cli(tenant_id, agreement_id, *extra) -> tuple[int, str, str]:
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([
            "show-register", "--tenant", str(tenant_id), "--dsn", dsn,
            "--agreement", agreement_id, *extra,
        ])
    return code, out.getvalue(), err.getvalue()


def entries(*pairs: tuple[str, str]) -> tuple[RegisteredIdentity, ...]:
    return tuple(RegisteredIdentity(garage_id=g, identity_normalised=i) for g, i in pairs)


# ---------------------------------------------------------------------------
# The answer's shape. No database.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G45")
def test_the_answer_is_nine_fields_and_nothing_more_travels():
    """R2 and A1.1: derived from the class, so a field added to it is seen
    here the day it exists, and the document the command line prints has
    exactly the same nine keys."""
    assert tuple(f.name for f in fields(AgreementRegister)) == THE_NINE
    register = AgreementRegister(
        agreement="ag-x", version=3, registrar="outside", status="cancelled",
        cancelled_effective_day=date(2026, 2, 1), home_garage="g-home",
        covered_garages=("g-home", "g-other"),
        registrations=entries(("g-other", "AB-123"), ("g-home", "ab123")),
        garages_not_covered=("g-gone",),
    )
    document = register.as_document()
    assert tuple(document) == THE_NINE
    assert document["cancelled_effective_day"] == "2026-02-01"
    assert document["registrations"] == [
        {"garage": "g-other", "identity_normalised": "AB-123"},
        {"garage": "g-home", "identity_normalised": "ab123"},
    ]
    assert all(
        tuple(entry) == ("garage", "identity_normalised") for entry in document["registrations"]
    )
    for fragment in NEVER_TRAVELS:
        assert not any(fragment in key for key in document), (fragment, list(document))
        assert not any(fragment in key for entry in document["registrations"] for key in entry)
    # And the JSON the verb prints is this document with sorted keys, nothing added.
    assert json.loads(json.dumps(document, sort_keys=True)) == document
    active = AgreementRegister(**{**register.__dict__, "status": "active",
                                  "cancelled_effective_day": None})
    assert active.as_document()["cancelled_effective_day"] is None


@pytest.mark.guarantee("G45")
def test_the_read_builds_no_engine_value_and_picks_its_version_where_the_door_does():
    """R4 and R5, statically: the read names no loader and no engine
    constructor -- ``Agreement``, ``Garage``, ``load_garage``, ``_as_stored``,
    ``load_agreements_*`` -- and the door and the read both name the one
    latest-version helper. The behavioural halves are the store tests below."""
    read_names = set(show_register.__code__.co_names)
    door_names = set(_outside_registrars_covered_set.__code__.co_names)
    assert "_latest_version_of" in read_names and "_latest_version_of" in door_names
    for validator in (
        "Agreement", "Garage", "load_garage", "_as_stored", "load_agreements_at_garage",
        "load_agreements_covering_garage", "load_agreements_on_invoice", "Registrar",
        "Status", "zone",
    ):
        assert validator not in read_names, validator
    # The helper itself picks the row by the tenant AND the id, highest version first.
    sql = " ".join(c for c in _latest_version_of.__code__.co_consts if isinstance(c, str))
    assert "WHERE tenant_id = %s AND external_id = %s" in sql
    assert "ORDER BY version DESC LIMIT 1" in sql


# ---------------------------------------------------------------------------
# The store.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G45")
@store_backed
def test_the_read_shows_the_register_as_stored_at_every_covered_garage(app, tenant_id):
    """§5.1: two garages that disagree on the identity rule, cars through the
    door -- one plate, two stored forms -- and the command line prints the
    same document: JSON with sorted keys on stdout, exit 0, nothing on stderr."""
    _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    register = _read(app, tenant_id)
    assert register == AgreementRegister(
        agreement=OUTSIDE_ID, version=1, registrar="outside", status="active",
        cancelled_effective_day=None, home_garage=HOME.id,
        covered_garages=(OTHER.id, HOME.id),  # code point: 'f' < 'm'
        registrations=entries(
            (OTHER.id, "AB-123"), (OTHER.id, "CD-456"), (HOME.id, "ab123"), (HOME.id, "cd456"),
        ),
        garages_not_covered=(),
    )
    code, out, err = _cli(tenant_id, OUTSIDE_ID)
    assert (code, err) == (0, "")
    assert out == json.dumps(register.as_document(), sort_keys=True, indent=2) + "\n"
    assert json.loads(out)["registrations"] == [
        {"garage": OTHER.id, "identity_normalised": "AB-123"},
        {"garage": OTHER.id, "identity_normalised": "CD-456"},
        {"garage": HOME.id, "identity_normalised": "ab123"},
        {"garage": HOME.id, "identity_normalised": "cd456"},
    ]


@pytest.mark.guarantee("G45")
@store_backed
def test_a_version_that_adds_a_garage_shows_the_row_absent_there(app, tenant_id):
    """§5.2 and R5: the car was registered when only the home was covered;
    the version that adds the other garage does not register it there, and
    the read shows the store as it is -- from the LATEST version, the one the
    door now fans out over."""
    seeded = _seed(app, tenant_id, outside(covered_garage_ids=(HOME.id,)))
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(version=2))
    register = _read(app, tenant_id)
    assert register.version == 2
    assert register.covered_garages == (OTHER.id, HOME.id)
    assert register.registrations == entries((HOME.id, "ab123"))
    assert register.garages_not_covered == ()
    # The door's next registration lands at both, and the read follows it.
    _register(app, tenant_id, OUTSIDE_ID, "EF-789")
    assert _read(app, tenant_id).registrations == entries(
        (OTHER.id, "EF-789"), (HOME.id, "ab123"), (HOME.id, "ef789"),
    )
    with tenant(app, tenant_id) as cursor:
        latest = _latest_version_of(cursor, tenant_id, OUTSIDE_ID)
        (door_home, *_door_rest) = _outside_registrars_covered_set(cursor, tenant_id, OUTSIDE_ID)
    app.rollback()
    assert latest.version == 2 and latest.uuid != seeded.agreement_uuids[OUTSIDE_ID]  # v1's uuid
    assert door_home.uuid == latest.home_uuid


@pytest.mark.guarantee("G45")
@store_backed
def test_a_cancelled_agreement_past_its_day_is_shown_with_its_rows(app, tenant_id):
    """§5.3 and R4: no clock. The day has passed; the rows are still stored
    (the handover releases them on the next registration, G32); the read
    shows ``cancelled``, the day, and the rows."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(
        version=2, status=Status.CANCELLED, cancelled_effective_day=date(2026, 2, 1),
    ))
    register = _read(app, tenant_id)
    assert (register.version, register.status) == (2, "cancelled")
    assert register.cancelled_effective_day == date(2026, 2, 1)
    assert register.registrations == entries((OTHER.id, "AB-123"), (HOME.id, "ab123"))
    assert json.loads(_cli(tenant_id, OUTSIDE_ID)[1])["cancelled_effective_day"] == "2026-02-01"


@pytest.mark.guarantee("G45")
@store_backed
def test_a_self_written_agreement_is_read_too_and_says_so(app, tenant_id):
    """R3: the single-writer rule governs WRITES. The read answers for an
    agreement this module registers and names the registrar."""
    mine = multi_garage_agreement(start_day=date(2026, 1, 5))
    assert mine.registrar is Registrar.THIS_MODULE
    _seed(app, tenant_id, mine)
    register = _read(app, tenant_id, mine.id)
    assert register.registrar == "this_module"
    assert register.registrations == tuple(sorted(
        entries(*[(g.id, g.normalise_identity(v)) for v in mine.vehicles for g in (HOME, OTHER)]),
        key=lambda e: (e.identity_normalised, e.garage_id),
    ))
    assert len(register.registrations) == 2 * len(mine.vehicles)
    code, out, err = _cli(tenant_id, mine.id)
    assert (code, err) == (0, "") and json.loads(out)["registrar"] == "this_module"


@pytest.mark.guarantee("G45")
@store_backed
def test_no_such_agreement_is_not_found_and_no_rows_is_an_empty_register(app, tenant_id):
    """R6 and A1.4: the door's refusal, unchanged -- NOT FOUND on stderr, exit
    2, nothing on stdout; and an agreement with no rows is an answer."""
    _seed(app, tenant_id, outside())
    register = _read(app, tenant_id)
    assert register.registrations == () and register.garages_not_covered == ()
    code, out, err = _cli(tenant_id, OUTSIDE_ID)
    assert code == 0 and json.loads(out)["registrations"] == [] and err == ""
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(AgreementNotFound):
            show_register(cursor, tenant_id, "ag-nowhere")
    app.rollback()
    code, out, err = _cli(tenant_id, "ag-nowhere")
    assert code == 2 and out == ""
    assert err.startswith("NOT FOUND — ") and "'ag-nowhere'" in err and "Traceback" not in err


@pytest.mark.guarantee("G45")
@store_backed
def test_another_tenants_agreement_of_the_same_id_is_not_shown_with_the_policy_on_and_off(
    app, owner, tenant_id
):
    """T3: the other tenant holds the same id at a HIGHER version with rows of
    its own. With the row policy ON the app role cannot see them (the control
    that the policy is doing its job); with it OFF -- the owner disables it on
    the four tables, restored in ``finally`` -- the app role sees both tenants'
    rows, and the read still shows only the tenant it was asked for."""
    other_tenant = new_tenant(owner)
    _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "MINE-1")
    seeded_other = _seed(app, other_tenant, outside())
    _register(app, other_tenant, OUTSIDE_ID, "THEIRS-1")
    _store(app, other_tenant, seeded_other, outside(version=2))
    expected = AgreementRegister(
        agreement=OUTSIDE_ID, version=1, registrar="outside", status="active",
        cancelled_effective_day=None, home_garage=HOME.id, covered_garages=(OTHER.id, HOME.id),
        registrations=entries((OTHER.id, "MINE-1"), (HOME.id, "mine1")), garages_not_covered=(),
    )

    def app_sees():
        versions = query(app, tenant_id, "SELECT version FROM agreements WHERE external_id = %s "
                         "ORDER BY version", (OUTSIDE_ID,))
        rows = query(app, tenant_id, "SELECT identity_normalised FROM vehicle_registrations "
                     "WHERE agreement_external_id = %s", (OUTSIDE_ID,))
        return sorted(v for (v,) in versions), sorted(i for (i,) in rows)

    # ON: the policy hides the other tenant, and the read agrees with it.
    assert app_sees() == ([1], ["MINE-1", "mine1"])
    assert _read(app, tenant_id) == expected

    tables = ("agreements", "agreement_garages", "garages", "vehicle_registrations")
    with _row_security_off(owner, tables):
        # OFF: the premise -- the app role now sees the other tenant's rows
        # too (and every earlier test's tenant in this module's database).
        versions, rows = app_sees()
        assert 2 in versions and {"THEIRS-1", "theirs1", "MINE-1", "mine1"} <= set(rows)
        # And the read is still the tenant's own: version 1, its rows, not
        # the other tenant's version 2 that ``LIMIT 1`` would otherwise pick.
        assert _read(app, tenant_id) == expected
        assert json.loads(_cli(tenant_id, OUTSIDE_ID)[1])["version"] == 1
        assert _read(app, other_tenant).version == 2
        assert _read(app, other_tenant).registrations == entries(
            (OTHER.id, "THEIRS-1"), (HOME.id, "theirs1")
        )
    # Restored: the policy hides the other tenant again.
    assert app_sees() == ([1], ["MINE-1", "mine1"])
    assert _read(app, tenant_id) == expected


@contextmanager
def _row_security_off(owner, tables):
    """DISABLE ROW LEVEL SECURITY on the tables, as the owner, and put it back
    -- the catalogue asserted before and after, so a restore that failed
    would be a failed test and not a database left open for the next one."""
    def flags():
        with owner.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = ANY(%s) ORDER BY relname", (list(tables),),
            )
            return cursor.fetchall()

    before = flags()
    assert all(rls and force for _name, rls, force in before), before
    try:
        with owner.cursor() as cursor:
            for table in tables:
                cursor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        assert all(not rls for _name, rls, _force in flags())
        yield
    finally:
        with owner.cursor() as cursor:
            for table in tables:
                cursor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        assert flags() == before, flags()


@pytest.mark.guarantee("G45")
@store_backed
def test_a_row_outside_the_covered_set_is_shown_and_its_garage_named(app, tenant_id):
    """T4. The schema does NOT forbid the state: ``vehicle_registrations``
    keys to ``tenants`` and ``garages`` and nothing else, so only the module's
    code (the release at a dropped garage) keeps rows inside the covered set.
    The cheapest route to the state is therefore a raw INSERT as the app role
    -- the row a system past the module would leave -- and the read shows it
    and names its garage."""
    referenced = query(
        app, tenant_id,
        "SELECT DISTINCT confrelid::regclass::text FROM pg_constraint "
        "WHERE conrelid = 'vehicle_registrations'::regclass AND contype = 'f' ORDER BY 1",
    )
    assert referenced == [("garages",), ("tenants",)], referenced
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(app, tenant_id, seeded, outside(version=2, covered_garage_ids=(HOME.id,)))
    assert _read(app, tenant_id).registrations == entries((HOME.id, "ab123"))
    _raw(
        app, tenant_id,
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, seeded.garage_uuids[OTHER.id], "STRAY-1", OUTSIDE_ID, DAY),
    )
    register = _read(app, tenant_id)
    assert register.covered_garages == (HOME.id,)
    assert register.registrations == entries((OTHER.id, "STRAY-1"), (HOME.id, "ab123"))
    assert register.garages_not_covered == (OTHER.id,)
    assert json.loads(_cli(tenant_id, OUTSIDE_ID)[1])["garages_not_covered"] == [OTHER.id]


@pytest.mark.guarantee("G45")
@store_backed
def test_an_unreadable_garage_or_version_still_shows_its_register(app, tenant_id):
    """T5, and a brief correction. A covered garage's timezone set raw to a
    zone that does not exist; the latest version given two overlapping pauses
    raw -- legal per row under 0001's CHECK, refused by the engine as a set.
    The loaders and the door refuse on those rows (the control); the read
    answers in full. A 'malformed money field' cannot be planted on this
    schema: ``monthly_price_minor`` is ``bigint CHECK (>= 0)`` and the engine
    accepts every value the CHECK admits -- measured below by the CHECK refusing
    the plant -- so the version is made unreadable by its pauses instead."""
    import psycopg

    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    home_uuid = seeded.garage_uuids[HOME.id]
    with pytest.raises(psycopg.errors.CheckViolation):
        _raw(
            app, tenant_id,
            "UPDATE agreements SET monthly_price_minor = -1 WHERE external_id = %s", (OUTSIDE_ID,),
        )
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

    # The controls: the garage cannot be loaded, the version cannot be loaded,
    # the door cannot fan out -- each refusing on exactly the planted rows.
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(UnknownTimezone):
            load_garage(cursor, OTHER.id)
        with pytest.raises(InvalidAgreement, match="pause"):
            load_agreements_at_garage(cursor, home_uuid)
        with pytest.raises(UnknownTimezone):
            register_from_outside(cursor, tenant_id, OUTSIDE_ID, "CD-456", now=DAY)
    app.rollback()

    register = _read(app, tenant_id)
    assert register == AgreementRegister(
        agreement=OUTSIDE_ID, version=1, registrar="outside", status="active",
        cancelled_effective_day=None, home_garage=HOME.id, covered_garages=(OTHER.id, HOME.id),
        registrations=entries((OTHER.id, "AB-123"), (HOME.id, "ab123")), garages_not_covered=(),
    )
    code, out, err = _cli(tenant_id, OUTSIDE_ID)
    assert (code, err) == (0, "") and json.loads(out) == register.as_document()


@pytest.mark.guarantee("G45")
@store_backed
def test_the_lists_are_sorted_by_code_point_in_python_and_not_by_the_database(app, tenant_id):
    """T6. Ids on which code-point order and en_US order disagree: 'garage-Zeta'
    sorts before 'garage-alpha' by code point ('Z' < 'a') and after it under
    en_US; 'AB-123' before 'ab123' by code point and after it under glibc's
    en_US, which ignores the dash and the case at the first level. The premise
    asserted first: the order the rows come off the heap is NOT the published
    order, so a read that dropped its sort would be seen."""
    alpha = Garage(id="garage-alpha", timezone=HOME.timezone, currency="USD",
                   billing_day=BillingDay.LAST_DAY_OF_MONTH, payment_grace_days=5,
                   identity_rule=IdentityRule.EXACT)
    zeta = Garage(id="garage-Zeta", timezone=HOME.timezone, currency="USD",
                  billing_day=BillingDay.LAST_DAY_OF_MONTH, payment_grace_days=5,
                  identity_rule=IdentityRule.EXACT)
    assert sorted([alpha.id, zeta.id]) == [zeta.id, alpha.id]
    assert sorted([alpha.id, zeta.id], key=str.lower) == [alpha.id, zeta.id]  # en_US's order
    assert sorted(["ab123", "AB-123"]) == ["AB-123", "ab123"]
    agreement = outside(garage_id=alpha.id, covered_garage_ids=(alpha.id, zeta.id))
    seeded = seed_garages(app, tenant_id, (alpha, zeta), (agreement,), now=DAY)
    assert seeded.garage_uuids
    _register(app, tenant_id, OUTSIDE_ID, "ab123")
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")

    published = _read(app, tenant_id)
    assert published.home_garage == alpha.id
    assert published.covered_garages == (zeta.id, alpha.id)
    assert published.registrations == entries(
        (zeta.id, "AB-123"), (alpha.id, "AB-123"), (zeta.id, "ab123"), (alpha.id, "ab123"),
    )
    # The premise: the heap's order (no ORDER BY) differs from the published one.
    heap = query(
        app, tenant_id,
        "SELECT g.external_id, r.identity_normalised FROM vehicle_registrations r "
        "JOIN garages g ON g.id = r.garage_id WHERE r.agreement_external_id = %s", (OUTSIDE_ID,),
    )
    assert [tuple(e.__dict__.values()) for e in published.registrations] != heap, heap
    covered_off_the_store = query(
        app, tenant_id,
        "SELECT g.external_id FROM agreement_garages ag JOIN garages g ON g.id = ag.garage_id "
        "JOIN agreements a ON a.id = ag.agreement_id WHERE a.external_id = %s "
        "ORDER BY (g.id = a.garage_id) DESC, g.external_id", (OUTSIDE_ID,),
    )
    assert [g for (g,) in covered_off_the_store] != list(published.covered_garages)
    assert json.loads(_cli(tenant_id, OUTSIDE_ID)[1])["covered_garages"] == [zeta.id, alpha.id]


@pytest.mark.guarantee("G45")
@store_backed
def test_the_read_writes_nothing(app, owner, tenant_id):
    """T1: the row count and a digest of every row of EVERY table -- xmin
    included, so an update that changed no value is still a change -- before
    and after the library read (committed) and the command line; and the
    cluster's tuple counters for the library read. The positive control first:
    the same instruments see the door's write."""
    _seed(app, tenant_id, outside())
    before = _digests(owner)
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    after_write = _digests(owner)
    assert after_write != before, "the premise: the instrument sees a write"
    assert after_write["vehicle_registrations"][0] == before["vehicle_registrations"][0] + 2

    counters_before = _tuple_counters(owner, app)
    still = _digests(owner)
    register = _read(app, tenant_id)
    assert register.registrations == entries((OTHER.id, "AB-123"), (HOME.id, "ab123"))
    assert _digests(owner) == still
    assert _tuple_counters(owner, app) == counters_before
    code, _out, err = _cli(tenant_id, OUTSIDE_ID)
    assert (code, err) == (0, "")
    assert _digests(owner) == still
    # And the positive control for the counters: a write moves them.
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    assert _tuple_counters(owner, app) != counters_before


def _digests(owner) -> dict[str, tuple[int, str]]:
    """(count, digest of every row with its xmin) per table of the schema, as
    the owner, who sees every tenant's rows."""
    with owner.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        tables = [name for (name,) in cursor.fetchall()]
        out = {}
        for table in tables:
            cursor.execute(
                f"SELECT count(*), md5(coalesce(string_agg(xmin::text || ':' || t::text, '|' "
                f"ORDER BY xmin::text || ':' || t::text), '')) FROM {table} t"
            )
            out[table] = cursor.fetchone()
    assert len(out) >= 12, sorted(out)
    return out


def _tuple_counters(owner, app) -> int:
    """Inserted + updated + deleted tuples over every table of the schema. The
    app backend flushes its pending statistics first, so the figure is the
    library read's own and not a snapshot from before it."""
    with app.cursor() as cursor:
        cursor.execute("SELECT pg_stat_force_next_flush()")
    app.commit()
    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(sum(n_tup_ins + n_tup_upd + n_tup_del), 0)::bigint "
            "FROM pg_stat_user_tables WHERE schemaname = 'public'"
        )
        (total,) = cursor.fetchone()
    return int(total)


@pytest.mark.guarantee("G45")
@store_backed
def test_every_field_is_backed_by_a_column_constraint(app, tenant_id):
    """R4 and A1.3, read off the catalogue of the migrated schema: each key's
    column, its NOT NULL, and the named CHECK, UNIQUE or FOREIGN KEY behind it
    -- so the read has nothing to validate."""
    def column(table, name):
        (row,) = query(
            app, tenant_id,
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, name),
        )
        return row

    def constraint(name):
        (row,) = query(
            app, tenant_id,
            "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
            (name,),
        )
        return row

    assert column("agreements", "external_id") == ("text", "NO")
    assert constraint("agreements_tenant_id_external_id_version_key") == (
        "u", "UNIQUE (tenant_id, external_id, version)")
    assert column("agreements", "version") == ("integer", "NO")
    assert constraint("agreements_version_check") == ("c", "CHECK ((version >= 1))")
    assert column("agreements", "registrar") == ("text", "NO")
    assert constraint("agreements_registrar_is_stated") == (
        "c", "CHECK ((registrar = ANY (ARRAY['this_module'::text, 'outside'::text])))")
    assert column("agreements", "status") == ("text", "NO")
    assert constraint("agreements_status_check") == (
        "c", "CHECK ((status = ANY (ARRAY['active'::text, 'cancelled'::text])))")
    assert column("agreements", "cancelled_effective_day") == ("date", "YES")
    kind, definition = constraint("agreements_cancellation_has_a_date")
    assert kind == "c" and "cancelled_effective_day IS NOT NULL" in definition
    assert column("garages", "external_id") == ("text", "NO")
    assert constraint("garages_tenant_id_external_id_key") == (
        "u", "UNIQUE (tenant_id, external_id)")
    garage_key = "FOREIGN KEY (tenant_id, garage_id) REFERENCES garages(tenant_id, id)"
    for key in ("agreements_garage_in_tenant", "agreement_garages_garage_in_tenant",
                "vehicle_registrations_garage_in_tenant"):
        kind, definition = constraint(key)
        assert kind == "f" and definition.startswith(garage_key), (key, definition)
    assert column("vehicle_registrations", "identity_normalised") == ("text", "NO")
    assert constraint("vehicle_registrations_identity_normalised_check") == (
        "c", "CHECK ((length(btrim(identity_normalised)) > 0))")


@pytest.mark.guarantee("G45")
@store_backed
def test_the_door_is_unchanged_by_sharing_its_version_rule(app, tenant_id):
    """A1.2: the door reads its uuid and registrar from the shared helper and
    behaves as before -- the version path, the refusal by name, the fan-out --
    which G42 proves in full; here the seam itself: the helper's row IS the
    door's row, for a self-written agreement too (the door refuses it AFTER
    picking it, by name)."""
    from monthly_billing.findings import REFUSAL_REGISTRAR_IS_THIS_MODULE, Refused

    mine = multi_garage_agreement(start_day=date(2026, 1, 5))
    seeded = _seed(app, tenant_id, outside(), mine)
    _store(app, tenant_id, seeded, outside(version=2, covered_garage_ids=(HOME.id,)))
    with tenant(app, tenant_id) as cursor:
        latest = _latest_version_of(cursor, tenant_id, OUTSIDE_ID)
        covered = _outside_registrars_covered_set(cursor, tenant_id, OUTSIDE_ID)
        assert latest.version == 2 and [g.garage.id for g in covered] == [HOME.id]
        assert isinstance(latest.uuid, UUID) and latest.registrar == "outside"
        with pytest.raises(Refused) as refused:
            _outside_registrars_covered_set(cursor, tenant_id, mine.id)
        assert refused.value.code == REFUSAL_REGISTRAR_IS_THIS_MODULE
        with pytest.raises(AgreementNotFound):
            _latest_version_of(cursor, tenant_id, "ag-nowhere")
    app.rollback()
