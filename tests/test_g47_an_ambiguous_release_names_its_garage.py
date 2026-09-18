"""G47 -- an ambiguous release names its garage.

The defect this closes (B3 of the outside round): an ordinary operator
command, ``release-vehicle`` without ``--garage``, silently destroyed a live
car's coverage and exited 0. With the home folded and the other garage
exact, a car re-registered under a plate that differs only in formatting
leaves a stale row at the exact garage and ONE row -- the live car's -- at
the folded one, under the same text; the fan-out took both. G46 gave the
operator a way to name the garage. This makes the door insist on it in the
one state where the unnamed form cannot mean one thing.

The rule, over identities and folds -- the store has no car, only text: for
each covered garage ``g`` let ``n_g`` be the text in ``g``'s form and
``rows[g]`` this agreement's identities there. The release is AMBIGUOUS when
there are a ``g`` the fan-out would delete at (``n_g`` in ``rows[g]``), a
DIFFERENT covered garage ``h``, and a row ``m`` in ``rows[h]`` other than
``n_h`` itself, with ``m`` in ``g``'s form equal to ``n_g``. Then the door
refuses by name, before any DELETE, and writes nothing.

The eight states of the brief are the eight cases here, a-h: the L3's pair
both directions (a, b); the partial state where the text reaches one garage
and folds onto the other's different row (c); the healthy states with one
car and with two (d, e); the one-garage agreement (f), including a
non-canonical row raw at its one garage, which is what the ``h is not g``
half of the rule is for; a covered garage added after the car (g); and the
named form in state a (h). Then: the detail names both identities and both
garages; the rows are byte-identical after a refusal, read on the refusing
cursor before rollback; another agreement's colliding identity refuses
nothing; a stored form that normalises to nothing under the other garage's
rule is the fold of nothing; and the command line presents the refusal as it
presents every other -- exit 2, ``REFUSED —`` and the code on stderr, nothing
on stdout, never a traceback.

Controls: the check dropped; the check run for the named form too (G46's own
suite); the refusal raised after the rows have gone; the ``m != n_h``
exclusion dropped; the ``h is not g`` exclusion dropped; the row read not
filtered to this agreement; the new code replaced by the old one.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import MULTI_HOME, MULTI_OTHER, outside_registrar_agreement
from monthly_billing.cli import main
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import (
    REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES,
    REFUSAL_VEHICLE_NOT_REGISTERED,
    REFUSALS,
    Refused,
)
from monthly_billing.garage import BillingDay, Garage
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    RegisteredIdentity,
    _refuse_an_ambiguous_release,
    register_from_outside,
    registrations_at_garage,
    release_from_outside,
    store_agreement,
)
from store_harness import APP_PASSWORD, DSN, needs_postgres, seed_garages
from test_g45_the_register_can_be_read import _digests

HOME = MULTI_HOME()  # Denver, USD, FOLDED -- the L3's garage-a
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT -- the L3's garage-b
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)
OUTSIDE_ID = "ag-outside"
#: A second outside registrar's agreement, covering the exact garage alone.
OTHER_OUTSIDE_ID = "ag-outside-2"

#: A third garage, folded like the home, for the covered set to grow into.
NEW = Garage(
    id="garage-new", timezone=HOME.timezone, currency="USD",
    billing_day=BillingDay.LAST_DAY_OF_MONTH, payment_grace_days=5,
    identity_rule=HOME.identity_rule,
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
    """The library release, committed; on a refusal, rolled back and re-raised."""
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


def _refused(app, tenant_id, seeded, identity, agreement_id=OUTSIDE_ID) -> Refused:
    """The unnamed release refused by name -- and the rows read ON THE REFUSING
    CURSOR, before the rollback, so a refusal that deleted first and raised
    second is seen here and not hidden by the caller's rollback."""
    garages = [g for g in (HOME, OTHER, NEW) if g.id in seeded.garage_uuids]
    before = {g.id: _rows(app, tenant_id, seeded, g) for g in garages}
    with tenant(app, tenant_id) as cursor:
        with pytest.raises(Refused) as caught:
            release_from_outside(cursor, tenant_id, agreement_id, identity)
        assert caught.value.code == REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES
        for garage in garages:
            inside = sorted(registrations_at_garage(cursor, seeded.garage_uuids[garage.id]))
            assert inside == before[garage.id], f"the refusal wrote at {garage.id}"
    app.rollback()
    for garage in garages:
        assert _rows(app, tenant_id, seeded, garage) == before[garage.id]
    return caught.value


def _store(app, tenant_id, seeded, agreement, *, now=DAY):
    home = HOME if agreement.garage_id == HOME.id else OTHER
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


def _raw(app, tenant_id, statement, parameters=()):
    with tenant(app, tenant_id) as cursor:
        try:
            cursor.execute(statement, parameters)
        except BaseException:
            app.rollback()
            raise
    app.commit()


def _raw_row(app, tenant_id, seeded, garage, form, agreement_id=OUTSIDE_ID):
    """A row put in raw, as a system past the module would -- the door's own
    normalisation is what the check must not assume."""
    _raw(
        app, tenant_id,
        "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
        "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, seeded.garage_uuids[garage.id], form, agreement_id, DAY),
    )


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


@pytest.mark.guarantee("G47")
def test_the_check_is_on_the_unnamed_path_and_writes_nothing_by_construction():
    """The door reaches the check by name; the check's own code holds no
    DELETE, INSERT or UPDATE and names no writer -- it reads and raises; and
    the refusal is registered, so the contract publishes it."""
    assert "_refuse_an_ambiguous_release" in release_from_outside.__code__.co_names
    consts = " ".join(
        c for c in _refuse_an_ambiguous_release.__code__.co_consts if isinstance(c, str)
    )
    for verb in ("DELETE", "INSERT", "UPDATE"):
        assert verb not in consts, verb
    names = set(_refuse_an_ambiguous_release.__code__.co_names)
    for writer in ("guarded_insert", "guarded_update", "execute"):
        assert writer not in names, writer
    assert {"registrations_at_garage", "Refused"} <= names
    assert "REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES" in names
    assert REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES in REFUSALS
    sentence = REFUSALS[REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES]
    assert "show-register" in sentence and "--garage" in sentence


# ---------------------------------------------------------------------------
# The store: the eight states.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G47")
@store_backed
@pytest.mark.parametrize(
    ("first", "second"),
    [("ab123", "AB-123"), ("AB-123", "ab123")],
    ids=["a-stale-is-the-folded-text", "b-stale-is-the-dashed-text"],
)
def test_a_and_b_the_l3_case_refuses_by_name_in_both_directions_and_writes_nothing(
    app, owner, tenant_id, first, second
):
    """States a and b. The car is registered as ``first``, then as ``second``;
    at the folded home one row, at the exact other garage two. The unnamed
    release of ``first`` -- which would take the stale row and the live one --
    is refused by name, with both identities and both garages in the detail;
    the rows and the coverage stand, the digest of every table is equal
    before and after, and the named form is still the way through."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, first)
    _register(app, tenant_id, OUTSIDE_ID, second)
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == sorted(
        [(first, OUTSIDE_ID), (second, OUTSIDE_ID)]
    )
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}

    still = _digests(owner)
    refused = _refused(app, tenant_id, seeded, first)
    assert _digests(owner) == still, "the refusal wrote something"
    # The detail: the identity as given, the garage whose row is shared and
    # the form there, and the other identity with the garage it is stored at.
    assert f"vehicle {first!r}" in refused.detail
    assert "row 'ab123'" in refused.detail and f"garage {HOME.id!r}" in refused.detail
    assert f"{second!r}" in refused.detail and f"garage {OTHER.id!r}" in refused.detail
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}

    # The way through (G46, state h): named at the other garage, the stale
    # row alone; the live rows stay, the car is covered at both.
    assert _release(app, tenant_id, OUTSIDE_ID, first, garage_id=OTHER.id) == entries(
        (OTHER.id, first)
    )
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [(second, OUTSIDE_ID)]
    assert _covered(app, tenant_id, second) == {HOME.id: True, OTHER.id: True}
    # And with the stale row gone the state is d: the unnamed release of the
    # live text is what it always was.
    assert _release(app, tenant_id, OUTSIDE_ID, second) == entries(
        (OTHER.id, second), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == [] and _rows(app, tenant_id, seeded, OTHER) == []


@pytest.mark.guarantee("G47")
@store_backed
def test_c_the_partial_state_refuses_where_the_text_reaches_one_garage_and_folds_onto_the_other(
    app, owner, tenant_id
):
    """State c. One car, registered as 'AB-123': at the home 'ab123', at the
    exact garage 'AB-123'. The unnamed release of 'ab123' reaches the home's
    row alone -- and that row is the fold of 'AB-123', a different identity
    the agreement holds at the other garage. Before G47 the car was left
    covered at one garage and not the other, exit 0; now the release is
    refused by name and nothing moves. The named form releases at one garage
    on purpose."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]
    still = _digests(owner)
    refused = _refused(app, tenant_id, seeded, "ab123")
    assert _digests(owner) == still
    assert "'AB-123'" in refused.detail and f"garage {OTHER.id!r}" in refused.detail
    assert _covered(app, tenant_id, "AB-123") == {HOME.id: True, OTHER.id: True}
    # The same text, dashed differently, is the same partial state.
    _refused(app, tenant_id, seeded, "ab-123")
    assert _digests(owner) == still
    # Named at the home, 'ab123' releases the home's row and nothing else.
    assert _release(app, tenant_id, OUTSIDE_ID, "ab123", garage_id=HOME.id) == entries(
        (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]


@pytest.mark.guarantee("G47")
@store_backed
def test_d_and_e_a_healthy_register_releases_as_it_always_did(app, tenant_id):
    """States d and e. One car, then two: the unnamed release of the text the
    registrar registered takes the row at every covered garage and answers
    each in its form -- no refusal, the other car's rows untouched."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    # d.
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries(
        (OTHER.id, "AB-123"), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == [] and _rows(app, tenant_id, seeded, OTHER) == []
    # e.
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OUTSIDE_ID, "CD-456")
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries(
        (OTHER.id, "AB-123"), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == [("cd456", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("CD-456", OUTSIDE_ID)]
    # And the text with no row anywhere is still REFUSAL_VEHICLE_NOT_REGISTERED,
    # its own sentence: a text that reaches no row cannot be ambiguous.
    with pytest.raises(Refused) as none:
        _release(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert none.value.code == REFUSAL_VEHICLE_NOT_REGISTERED
    assert "none of" in none.value.detail


@pytest.mark.guarantee("G47")
@store_backed
def test_f_a_one_garage_agreement_never_reaches_the_refusal(app, tenant_id):
    """State f. With one covered garage there is no other garage, so the check
    cannot fire -- whatever the one garage's rows hold. The second half is
    what the ``h is not g`` half of the rule is for: a non-canonical form put
    in raw at the folded garage ('AB-123' where the door would have stored
    'ab123') folds onto the text's row at the SAME garage, and that is not an
    ambiguity across garages. The release takes the text's row, answers it,
    and leaves the raw row standing."""
    seeded = _seed(
        app, tenant_id, outside(covered_garage_ids=(HOME.id,)), garages=(HOME,)
    )
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _release(app, tenant_id, OUTSIDE_ID, "ab123") == entries((HOME.id, "ab123"))
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries((HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, HOME) == []
    # The raw non-canonical row at the one garage.
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _raw_row(app, tenant_id, seeded, HOME, "AB-123")
    assert _rows(app, tenant_id, seeded, HOME) == [("AB-123", OUTSIDE_ID), ("ab123", OUTSIDE_ID)]
    assert _release(app, tenant_id, OUTSIDE_ID, "ab123") == entries((HOME.id, "ab123"))
    assert _rows(app, tenant_id, seeded, HOME) == [("AB-123", OUTSIDE_ID)]


@pytest.mark.guarantee("G47")
@store_backed
def test_g_a_covered_garage_added_after_the_car_holds_no_row_and_refuses_nothing(
    app, tenant_id
):
    """State g (G42's covered-set behaviour, kept). A version adds a third
    covered garage after the car was registered: it holds no row, is absent
    from the answer, and the check does not count it -- a garage with no row
    of the agreement is not the other half of anything."""
    seeded = _seed(app, tenant_id, outside(), garages=(HOME, OTHER, NEW))
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _store(
        app, tenant_id, seeded,
        outside(version=2, covered_garage_ids=(HOME.id, OTHER.id, NEW.id)),
    )
    assert _rows(app, tenant_id, seeded, NEW) == []
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries(
        (OTHER.id, "AB-123"), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == [] and _rows(app, tenant_id, seeded, OTHER) == []
    # And the ambiguous state with the empty garage covered is still refused:
    # the empty garage adds nothing and takes nothing away.
    _register(app, tenant_id, OUTSIDE_ID, "ab123")
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _raw(
        app, tenant_id,
        "DELETE FROM vehicle_registrations WHERE garage_id = %s AND agreement_external_id = %s",
        (seeded.garage_uuids[NEW.id], OUTSIDE_ID),
    )
    assert _rows(app, tenant_id, seeded, NEW) == []
    _refused(app, tenant_id, seeded, "ab123")


@pytest.mark.guarantee("G47")
@store_backed
def test_h_the_named_form_is_untouched_in_the_ambiguous_state(app, owner, tenant_id):
    """State h (G46, kept). In state a the named form reaches the named garage
    alone, under its rule, and is never asked the cross-garage question: at
    the other garage the stale row alone; at the home the one row -- the live
    car's, by the operator's explicit word -- and the exact garage's two rows
    stand."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "ab123")
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _refused(app, tenant_id, seeded, "ab123")
    assert _release(app, tenant_id, OUTSIDE_ID, "ab123", garage_id=OTHER.id) == entries(
        (OTHER.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]
    _register(app, tenant_id, OUTSIDE_ID, "ab123")  # back to state a
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123", garage_id=HOME.id) == entries(
        (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, HOME) == []
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID), ("ab123", OUTSIDE_ID)]


# ---------------------------------------------------------------------------
# What the rule does NOT count.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G47")
@store_backed
def test_another_agreements_identity_at_a_covered_garage_refuses_nothing(app, tenant_id):
    """Only this agreement's rows count. A second outside registrar's
    agreement, covering the exact garage alone, holds 'ab123' there -- a
    different row from this agreement's 'AB-123' under the exact rule, and
    the fold of this agreement's home row. Its row is its own: this
    agreement's unnamed release of 'AB-123' is state d, not c."""
    second = outside(
        id=OTHER_OUTSIDE_ID, payer_id="payer-outside-2",
        garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
    )
    seeded = _seed(app, tenant_id, outside(), second)
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _register(app, tenant_id, OTHER_OUTSIDE_ID, "ab123")
    assert _rows(app, tenant_id, seeded, OTHER) == [
        ("AB-123", OUTSIDE_ID), ("ab123", OTHER_OUTSIDE_ID)
    ]
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries(
        (OTHER.id, "AB-123"), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, OTHER) == [("ab123", OTHER_OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, HOME) == []


@pytest.mark.guarantee("G47")
@store_backed
def test_a_stored_form_that_normalises_to_nothing_under_the_other_rule_is_the_fold_of_nothing(
    app, tenant_id
):
    """At the exact garage '- -' is a legal identity; under the folded home's
    rule it normalises to nothing, which that rule refuses rather than
    stores. A row of it at the exact garage (put in raw: the door's fan-out
    would refuse it at the home) is the fold of no text, so it is skipped --
    the release of the healthy car is what it was, and the row stands."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    _raw_row(app, tenant_id, seeded, OTHER, "- -")
    assert _release(app, tenant_id, OUTSIDE_ID, "AB-123") == entries(
        (OTHER.id, "AB-123"), (HOME.id, "ab123")
    )
    assert _rows(app, tenant_id, seeded, OTHER) == [("- -", OUTSIDE_ID)]


# ---------------------------------------------------------------------------
# The command line.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G47")
@store_backed
def test_the_command_line_presents_the_refusal_as_it_presents_every_other(
    app, owner, tenant_id
):
    """Exit 2, ``REFUSED —`` and the code on stderr with both garages named,
    nothing on stdout, never a traceback, nothing written; and the same verb
    with ``--garage`` goes through with the header and its one line."""
    seeded = _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "ab123")
    _register(app, tenant_id, OUTSIDE_ID, "AB-123")
    still = _digests(owner)
    code, out, err = _cli(tenant_id, "--agreement", OUTSIDE_ID, "--vehicle", "ab123")
    assert (code, out) == (2, ""), (out, err)
    assert err.startswith("REFUSED — ") and REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES in err
    assert HOME.id in err and OTHER.id in err and "Traceback" not in err
    assert err.endswith("\n") and err.count("\n") == 1
    assert _digests(owner) == still
    assert _rows(app, tenant_id, seeded, HOME) == [("ab123", OUTSIDE_ID)]
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID), ("ab123", OUTSIDE_ID)]
    code, out, err = _cli(tenant_id, "--agreement", OUTSIDE_ID, "--vehicle", "ab123",
                          "--garage", OTHER.id)
    assert (code, err) == (0, "")
    assert out == f"vehicle released from agreement {OUTSIDE_ID}\n  at garage {OTHER.id}: ab123\n"
    assert _rows(app, tenant_id, seeded, OTHER) == [("AB-123", OUTSIDE_ID)]
