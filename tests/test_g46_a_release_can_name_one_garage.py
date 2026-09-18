"""G46 -- a release can name ONE covered garage and reach it alone.

The defect, measured by an outside registrar's review: ``release_from_outside``
deletes the identity at EVERY garage of the latest covered set, each under its
own rule, in one call. With the home folded and the other garage exact, a car
re-registered under a plate that differs only in formatting leaves a STALE row
at the exact garage and ONE row -- the live car's -- at the folded one, under
the same text. Releasing the stale text would remove the stale row and the
live row together; the only string that reaches an exact-rule row is its own
text, and by construction that text folds to the live form, so no caller could
release the stale row alone. Since G47 the unnamed release REFUSES in that
state rather than take both -- the premise of the first test, measured -- and
this option is the way through.

The change: ``release-vehicle --garage G`` (``garage_id=`` on the library
call, the same code) reaches that one covered garage, under its rule, and
answers the same header and exactly one line. Without the option nothing is
different -- the parity of the unscoped verb at the base and at the head is a
script's measurement, not this module's; here the shape of the seam.

The refusals, in order, before any write: no such agreement (NOT FOUND); a
registrar that is this module; a garage the tenant does not hold (NOT FOUND --
found by tenant AND id in this path's own lookup, the row policy on or off);
a garage the latest version does not cover (``REFUSAL_GARAGE_NOT_COVERED``);
no row of this agreement for this identity there
(``REFUSAL_VEHICLE_NOT_REGISTERED``). Each is proven to write nothing by the
count and digest of every row of every table, xmin included, before and after.

Controls: the option parsed and dropped (the command line); the option read
and the fan-out kept (the library); the lookup's tenant predicate removed; a
garage outside the covered set accepted; a garage the tenant does not hold
refused under the wrong name; the garage looked up before the agreement; the
rows deleted before the refusals; the identity normalised under the home's
rule instead of the named garage's; a per-garage release that found no row
reported as done.
"""

from __future__ import annotations

import inspect
import io
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from fixtures import MULTI_HOME, MULTI_OTHER, multi_garage_agreement, outside_registrar_agreement
from monthly_billing.cli import main
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import (
    REFUSAL_GARAGE_NOT_COVERED,
    REFUSAL_REGISTRAR_IS_THIS_MODULE,
    REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES,
    REFUSAL_VEHICLE_NOT_REGISTERED,
    Refused,
)
from monthly_billing.garage import BillingDay, Garage, IdentityRule
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    AgreementNotFound,
    GarageNotFound,
    RegisteredIdentity,
    _covered_garage_named,
    register_from_outside,
    registrations_at_garage,
    release_from_outside,
    store_agreement,
)
from store_harness import APP_PASSWORD, DSN, needs_postgres, new_tenant, query, seed_garages
from test_g45_the_register_can_be_read import _digests, _row_security_off

HOME = MULTI_HOME()  # Denver, USD, FOLDED -- the L3's garage-a
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT -- the L3's garage-b
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)
OUTSIDE_ID = "ag-outside"

#: A garage the tenant holds and the agreement does not cover.
THIRD = Garage(
    id="garage-third", timezone=HOME.timezone, currency="USD",
    billing_day=BillingDay.LAST_DAY_OF_MONTH, payment_grace_days=5,
    identity_rule=IdentityRule.EXACT,
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


def _release(app, tenant_id, agreement_id, identity, garage_id=None):
    """The library release, committed; on a refusal, rolled back and re-raised
    -- the caller's part of 'a refusal writes nothing'."""
    with tenant(app, tenant_id) as cursor:
        try:
            released = release_from_outside(
                cursor, tenant_id, agreement_id, identity, garage_id=garage_id
            )
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return released


def _store(app, tenant_id, seeded, agreement, *, now=DAY):
    with tenant(app, tenant_id) as cursor:
        try:
            store_agreement(
                cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id],
                seeded.payer_uuids[agreement.payer_id], agreement, now=now,
            )
        except BaseException:
            app.rollback()
            raise
    app.commit()


def _raw(app, tenant_id, statement, parameters=()):
    with tenant(app, tenant_id) as cursor:
        try:
            cursor.execute(statement, parameters)
        except BaseException:
            app.rollback()
            raise
    app.commit()


def _rows(app, tenant_id, seeded, garage) -> list[tuple[str, str]]:
    """Sorted here, by code point, never by the database's collation (G42)."""
    with tenant(app, tenant_id) as cursor:
        rows = sorted(registrations_at_garage(cursor, seeded.garage_uuids[garage.id]))
    app.rollback()
    return rows


def _cli(tenant_id, *args) -> tuple[int, str, str]:
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["release-vehicle", "--tenant", str(tenant_id), "--dsn", dsn, *args])
    return code, out.getvalue(), err.getvalue()


def _covered(app, tenant_id, vehicle, garages=(HOME, OTHER)) -> dict[str, bool]:
    return {g.id: covered_from_store(app, tenant_id, g.id, vehicle, DAY).covered for g in garages}


def entries(*pairs: tuple[str, str]) -> tuple[RegisteredIdentity, ...]:
    return tuple(RegisteredIdentity(garage_id=g, identity_normalised=i) for g, i in pairs)


# ---------------------------------------------------------------------------
# The shape of the seam. No database.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G46")
def test_the_option_defaults_off_and_the_lookup_is_this_paths_own_and_tenant_scoped():
    """X1 and A1.3, statically: the parameter defaults to None on the library
    call and on the command line; the named-garage lookup is by tenant AND id
    in its own SQL, names no loader and builds no ``Garage``; and the door
    reaches it only when a garage is named."""
    parameter = inspect.signature(release_from_outside).parameters["garage_id"]
    assert parameter.default is None
    sql = " ".join(c for c in _covered_garage_named.__code__.co_consts if isinstance(c, str))
    assert "SELECT id FROM garages WHERE tenant_id = %s AND external_id = %s" in sql
    names = set(_covered_garage_named.__code__.co_names)
    for validator in ("load_garage", "Garage", "StoredGarage", "_as_stored", "zone"):
        assert validator not in names, validator
    assert {"GarageNotFound", "Refused", "REFUSAL_GARAGE_NOT_COVERED"} <= names
    assert "_covered_garage_named" in release_from_outside.__code__.co_names


# ---------------------------------------------------------------------------
# The store.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G46")
@store_backed
@pytest.mark.parametrize(
    ("first", "second"),
    [("ab123", "AB-123"), ("AB-123", "ab123")],
    ids=["stale-is-the-folded-text", "stale-is-the-dashed-text"],
)
def test_the_l3_case_the_stale_row_goes_and_the_live_row_stays_covered_at_both(
    app, tenant_id, first, second
):
    """T4, both directions. The car is registered as ``first``; the registrar
    swaps it to ``second``, a plate that differs only in formatting. At the
    folded home the two are ONE row; at the exact other garage they are two,
    and ``first``'s is now stale. The premise: the unnamed release of
    ``first`` now refuses by name and writes nothing (G47) -- it would take the
    stale row AND the home's live row, and the door no longer guesses. The
    fix: named at the other garage, the release takes the stale row alone,
    and the car is covered at both garages before, during and after."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, first)
    _register(app, tenant_id, OUTSIDE_ID, second)
    live_home = HOME.normalise_identity(second)
    assert live_home == HOME.normalise_identity(first) == "ab123"  # one row at the home
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == sorted(
        [(first, OUTSIDE_ID), (second, OUTSIDE_ID)]
    )
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}

    # THE PREMISE: the unscoped release would take the live row with the stale
    # one, and refuses by name instead (G47); the rows and the coverage stand.
    with pytest.raises(Refused) as ambiguous:
        _release(app, tenant_id, OUTSIDE_ID, first)
    assert ambiguous.value.code == REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == sorted(
        [(first, OUTSIDE_ID), (second, OUTSIDE_ID)]
    )
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}

    # THE FIX: the stale row, by its garage; the live rows stay; covered throughout.
    released = _release(app, tenant_id, OUTSIDE_ID, first, garage_id=OTHER.id)
    assert released == entries((OTHER.id, first))
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [(second, OUTSIDE_ID)]
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}
    # The stale text now reaches nothing at the other garage -- and, named
    # there, does not touch the home's row it folds to.
    with pytest.raises(Refused) as refused:
        _release(app, tenant_id, OUTSIDE_ID, first, garage_id=OTHER.id)
    assert refused.value.code == REFUSAL_VEHICLE_NOT_REGISTERED
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}


@pytest.mark.guarantee("G46")
@store_backed
def test_named_the_release_is_under_that_garages_rule_and_touches_no_other_row(app, tenant_id):
    """X2: the identity is normalised under the NAMED garage's rule -- at the
    folded home 'AB-123' is 'ab123', so naming the home releases the home's
    row and leaves the exact garage's 'AB-123' standing -- and a second car's
    rows are untouched either way."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    released = _release(app, tenant_id, OUTSIDE_ID, "AB-123", garage_id=HOME.id)
    assert released == entries((HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, HOME) == [("cd456", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID), ("CD-456", OUTSIDE_ID)]
    assert _covered(app, tenant_id, "AB-123") == {HOME.id: False, OTHER.id: True}
    assert _covered(app, tenant_id, "CD-456") == {HOME.id: True, OTHER.id: True}
    # And the unscoped release afterwards is what it always was: the row at
    # the one garage that still holds it, the other absent from the answer.
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries((OTHER.id, "AB-123"))
    assert _rows(app, tenant_id, seeded, OTHER) == [("CD-456", OUTSIDE_ID)]


@pytest.mark.guarantee("G46")
@store_backed
def test_the_refusals_come_by_name_in_order_and_each_writes_nothing(app, owner, tenant_id):
    """X3 and §4.2. Each refusal with a LATER refusal's fact also true, so the
    order is measured and not read: no such agreement with a garage nobody
    holds; this module's agreement with that garage; a garage nobody holds
    that is also not covered; a held garage the agreement does not cover; a
    covered garage where this agreement holds no row for the identity -- the
    exact text that folds to a row at the home. Before and after each, the
    count and digest of every row of every table (xmin included) are equal;
    the positive control first: a release that lands moves them."""
    mine = multi_garage_agreement(start_day=date(2026, 1, 5))
    seeded = _seed(app, tenant_id, outside(), mine, garages=(HOME, OTHER, THIRD))
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert _rows(app, tenant_id, seeded, THIRD) == []

    before = _digests(owner)
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123", garage_id=OTHER.id) == entries(
        (OTHER.id, "AB-123")
    )
    assert _digests(owner) != before, "the premise: the digest sees a release that lands"
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")  # back at both, for the cases below
    still = _digests(owner)

    def refused_by(identity, garage_id, agreement_id=OUTSIDE_ID):
        with pytest.raises((Refused, LookupError)) as caught:
            _release(app, tenant_id, agreement_id, identity, garage_id=garage_id)
        assert _digests(owner) == still, "a refusal wrote something"
        return caught.value

    # 1. No such agreement -- before the garage is even looked at.
    caught = refused_by("AB-123", "garage-nowhere", agreement_id="ag-nowhere")
    assert isinstance(caught, AgreementNotFound) and "'ag-nowhere'" in str(caught)
    # 2. A registrar that is this module -- still before the garage.
    caught = refused_by("AB-123", "garage-nowhere", agreement_id=mine.id)
    assert isinstance(caught, Refused) and caught.code == REFUSAL_REGISTRAR_IS_THIS_MODULE
    # 3. A garage the tenant does not hold -- before 'not covered'.
    caught = refused_by("AB-123", "garage-nowhere")
    assert isinstance(caught, GarageNotFound) and "'garage-nowhere'" in str(caught)
    # 4. A garage the tenant holds and the latest version does not cover.
    caught = refused_by("AB-123", THIRD.id)
    assert isinstance(caught, Refused) and caught.code == REFUSAL_GARAGE_NOT_COVERED
    assert f"'{THIRD.id}'" in caught.detail and f"'{HOME.id}'" in caught.detail
    # 5. No row at that garage: 'ab123' is the home's row, and at the exact
    #    other garage it is nobody's -- so the home's row is NOT touched.
    caught = refused_by("ab123", OTHER.id)
    assert isinstance(caught, Refused) and caught.code == REFUSAL_VEHICLE_NOT_REGISTERED
    assert "'ab123'" in caught.detail and f"'{OTHER.id}'" in caught.detail
    assert "none of" not in caught.detail  # the per-garage sentence, not the fan-out's
    assert [r for r in _rows(app, tenant_id, seeded, HOME) if r[1] == OUTSIDE_ID] == [
        ("ab123", OUTSIDE_ID)
    ]
    # 5 again, at a covered garage with no row of this identity at all.
    caught = refused_by("EF-789", HOME.id)
    assert isinstance(caught, Refused) and caught.code == REFUSAL_VEHICLE_NOT_REGISTERED

    # The same five on the command line: exit 2, the sentence on stderr,
    # nothing on stdout, never a traceback; and nothing written.
    for args, prefix, word in (
        (("--agreement", "ag-nowhere", "--vehicle", "AB-123", "--garage", "garage-nowhere"),
         "NOT FOUND — ", "'ag-nowhere'"),
        (("--agreement", mine.id, "--vehicle", "AB-123", "--garage", "garage-nowhere"),
         "REFUSED — ", REFUSAL_REGISTRAR_IS_THIS_MODULE),
        (("--agreement", OUTSIDE_ID, "--vehicle", "AB-123", "--garage", "garage-nowhere"),
         "NOT FOUND — ", "'garage-nowhere'"),
        (("--agreement", OUTSIDE_ID, "--vehicle", "AB-123", "--garage", THIRD.id),
         "REFUSED — ", REFUSAL_GARAGE_NOT_COVERED),
        (("--agreement", OUTSIDE_ID, "--vehicle", "ab123", "--garage", OTHER.id),
         "REFUSED — ", REFUSAL_VEHICLE_NOT_REGISTERED),
        # G18 binds the option: an identity that normalises to nothing under the
        # NAMED garage's rule (the folded home; at the exact garage '- -' is a
        # legal identity) is the sentence and exit 2, never a traceback.
        (("--agreement", OUTSIDE_ID, "--vehicle=- -", "--garage", HOME.id),
         "REFUSED — ", "normalises to nothing"),
    ):
        code, out, err = _cli(tenant_id, *args)
        assert code == 2 and out == "", (args, out, err)
        assert err.startswith(prefix) and word in err and "Traceback" not in err, (args, err)
        assert _digests(owner) == still, args


@pytest.mark.guarantee("G46")
@store_backed
def test_another_tenants_garage_of_the_same_id_is_not_found_with_the_policy_on_and_off(
    app, owner, tenant_id
):
    """T2 and A1.3. The other tenant holds a garage this tenant does not, and
    an agreement of the same id covering it. Naming that garage is NOT FOUND
    with the row policy ON -- the control that the policy alone would answer
    so -- and OFF, where ``load_garage``'s predicate would find it: the lookup
    carries the tenant in its own SQL. With the policy OFF the app role sees
    the other tenant's garage (the premise), and this tenant's own named
    release still lands at its own garage and nowhere else.

    The garage ids are unique to this test: the covered set is loaded by
    ``load_garage``, which is scoped by the policy alone (T1: not touched),
    and with the policy off an id every earlier test's tenant also holds
    would be found for whichever tenant came first off the heap."""
    tag = uuid4().hex[:8]
    home = replace(HOME, id=f"garage-home-{tag}")
    other = replace(OTHER, id=f"garage-other-{tag}")
    theirs = replace(OTHER, id=f"garage-theirs-{tag}")
    agreement = outside(garage_id=home.id, covered_garage_ids=(home.id, other.id))
    seeded = _seed(app, tenant_id, agreement, garages=(home, other))
    _register(app, tenant_id, OUTSIDE_ID, "MINE-1")
    other_tenant = new_tenant(owner)
    seeded_theirs = _seed(
        app, other_tenant,
        outside(garage_id=theirs.id, covered_garage_ids=(theirs.id,)), garages=(theirs,),
    )
    _register(app, other_tenant, OUTSIDE_ID, "THEIRS-1")

    def app_sees_garages():
        rows = query(app, tenant_id, "SELECT external_id FROM garages WHERE external_id = ANY(%s)",
                     ([home.id, other.id, theirs.id],))
        return sorted(g for (g,) in rows)

    def not_found():
        with pytest.raises(GarageNotFound) as caught:
            _release(app, tenant_id, OUTSIDE_ID, "MINE-1", garage_id=theirs.id)
        assert f"'{theirs.id}'" in str(caught.value)
        code, out, err = _cli(tenant_id, "--agreement", OUTSIDE_ID, "--vehicle", "MINE-1",
                              "--garage", theirs.id)
        assert (code, out) == (2, "") and err.startswith("NOT FOUND — ")
        assert _rows(app, tenant_id, seeded, home) == [("mine1", OUTSIDE_ID)]
        assert _rows(app, tenant_id, seeded, other) == [("MINE-1", OUTSIDE_ID)]
        assert _rows(app, other_tenant, seeded_theirs, theirs) == [("THEIRS-1", OUTSIDE_ID)]

    # ON: the policy hides the other tenant's garage, and the release agrees.
    assert app_sees_garages() == sorted([home.id, other.id])
    not_found()

    tables = ("agreements", "agreement_garages", "garages", "vehicle_registrations")
    with _row_security_off(owner, tables):
        # OFF: the premise -- the app role now sees the other tenant's garage.
        assert app_sees_garages() == sorted([home.id, other.id, theirs.id])
        not_found()
        # And this tenant's own named release lands at its own garage alone.
        assert _release(app, tenant_id, OUTSIDE_ID, "MINE-1", garage_id=other.id) == entries(
            (other.id, "MINE-1")
        )
        assert _rows(app, tenant_id, seeded, home) == [("mine1", OUTSIDE_ID)]
        assert _rows(app, tenant_id, seeded, other) == []
        assert _rows(app, other_tenant, seeded_theirs, theirs) == [("THEIRS-1", OUTSIDE_ID)]
    # Restored.
    assert app_sees_garages() == sorted([home.id, other.id])
    _register(app, tenant_id, OUTSIDE_ID, "MINE-1")
    not_found()


@pytest.mark.guarantee("G46")
@store_backed
def test_a_row_at_a_garage_the_latest_version_dropped_is_not_reachable_by_name(
    app, owner, tenant_id
):
    """T3, confirmed and not changed. The version that drops a garage
    releases the agreement's rows there (G42) -- asserted first. A row put
    back there raw, as a system past the module would, is not reachable by
    naming the garage: ``REFUSAL_GARAGE_NOT_COVERED``, nothing written, the
    stray row still there for the read to show."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]
    _store(app, tenant_id, seeded, outside(version=2, covered_garage_ids=(HOME.id,)))
    assert _rows(app, tenant_id, seeded, OTHER) == [], "G42: the version path released it"
    _raw(
        app, tenant_id,
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, seeded.garage_uuids[OTHER.id], "AB-123", OUTSIDE_ID, DAY),
    )
    still = _digests(owner)
    with pytest.raises(Refused) as refused:
        _release(app, tenant_id, OUTSIDE_ID, "AB-123", garage_id=OTHER.id)
    assert refused.value.code == REFUSAL_GARAGE_NOT_COVERED
    assert _digests(owner) == still
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    # The unscoped release does not reach it either (G42): the home's row
    # goes, the stray stays, and the answer names the home alone.
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries((HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]


@pytest.mark.guarantee("G46")
@store_backed
def test_the_command_line_keeps_its_header_and_prints_exactly_one_line(app, tenant_id):
    """A1.4 and X1's shape: with ``--garage`` the same header, then exactly
    one ``at garage`` line, exit 0, nothing on stderr; without it the header
    and one line per garage where a row went -- the verb as it was."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    code, out, err = _cli(tenant_id, "--agreement", OUTSIDE_ID, "--vehicle", "AB-123",
                          "--garage", OTHER.id)
    assert (code, err) == (0, "")
    assert out == f"vehicle released from agreement {OUTSIDE_ID}\n  at garage {OTHER.id}: AB-123\n"
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID), ("cd456", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("CD-456", OUTSIDE_ID)]
    code, out, err = _cli(tenant_id, "--agreement", OUTSIDE_ID, "--vehicle", "CD-456")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"vehicle released from agreement {OUTSIDE_ID}",
        f"  at garage {OTHER.id}: CD-456",
        f"  at garage {HOME.id}: cd456",
    ]
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == []
