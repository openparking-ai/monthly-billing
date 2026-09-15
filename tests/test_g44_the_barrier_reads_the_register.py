"""G44 -- the barrier reads the agreement's REGISTER, and who keeps it is stated.

The L3's B1, measured at 4c14f0c with three controls: both coverage doors
required the presented car on the VERSION'S OWN LIST, which an outside
registrar's agreement leaves empty by rule, so a car the door had registered
read NO_AGREEMENT at every garage -- the identical reason string as a car
nobody ever registered -- while the self-written car beside it was covered.
None of the money state at the lane (unpaid, blocked, in grace) could reach a
connected monthly, which is what the connection exists for.

**THE REGISTER IS DECIDED IN ONE PLACE.** ``entitlement.register_of`` answers
which identities belong to an agreement at a garage: the version's list under
this module (the default -- the answer the list gave before the parameter
existed, byte for byte), the store's registration rows under an OUTSIDE
registrar. Both doors call it; the store door supplies the rows as a STATED
parameter (``registrations``), even when empty, because ``{}`` is "nothing is
registered" and ``None`` is "the question was not asked". The pure call, which
has no database, refuses by name (``REFUSAL_REGISTRATIONS_NOT_GIVEN``) an
outside registrar's agreement handed in without them -- the
``REFUSAL_HOME_GARAGE_NOT_GIVEN`` shape, failing closed -- and never answers
"no agreement" for a car it could not have looked up.

**THE OVER-REACH IS THE HALF THAT MATTERS.** A car with no row under an outside
registrar's agreement is NOT covered; a self-written agreement answers exactly
as it did; the money doors read none of this.

Controls: the outside branch reads the list again (B1 back, exactly as
measured); the refusal planted away (an unasked question answered "no
agreement"); every car covered under an outside registrar (the no-row control
goes red); the store door passes nothing (the door car is refused rather than
answered).
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import MULTI_HOME, MULTI_OTHER, multi_garage_agreement, outside_registrar_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.cli import main
from monthly_billing.entitlement import Answer, is_covered, register_of
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.findings import (
    NOT_COVERED_NO_AGREEMENT,
    NOT_COVERED_UNPAID_PAST_GRACE,
    REFUSAL_REGISTRATIONS_NOT_GIVEN,
    Refused,
)
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import register_from_outside
from store_harness import APP_PASSWORD, DSN, needs_postgres, seed_garages

HOME = MULTI_HOME()  # Denver, USD, FOLDED
OTHER = MULTI_OTHER()  # Phoenix, JPY, EXACT
HOME_TZ = ZoneInfo(HOME.timezone)
DAY = datetime(2026, 5, 10, 9, 0, tzinfo=HOME_TZ)
OUTSIDE_ID = "ag-outside"


def outside(**overrides: object):
    return outside_registrar_agreement(**{"start_day": date(2026, 1, 5), **overrides})


def mine(**overrides: object):
    return multi_garage_agreement(
        **{"id": "ag-self", "payer_id": "payer-self", "vehicles": ("SELF-1",),
           "start_day": date(2026, 1, 5), **overrides}
    )


# ---------------------------------------------------------------------------
# The pure call. No database: the register is stated, or refused by name.
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G44")
def test_the_register_is_the_list_under_this_module_and_the_stated_rows_under_outside():
    self_written, door = mine(), outside()
    # This module: the list, and the parameter is not read at all.
    assert register_of(self_written, HOME, None) == self_written.vehicles
    stated = {"ag-self": ("SOMETHING-ELSE",)}
    assert register_of(self_written, HOME, stated) == self_written.vehicles
    # Outside: the stated rows -- and only this agreement's.
    assert register_of(door, HOME, {OUTSIDE_ID: ("door1",), "ag-self": ("self1",)}) == ("door1",)
    assert register_of(door, HOME, {}) == ()
    with pytest.raises(Refused) as refused:
        register_of(door, HOME, None)
    assert refused.value.code == REFUSAL_REGISTRATIONS_NOT_GIVEN
    assert f"{OUTSIDE_ID!r}" in refused.value.detail and HOME.id in refused.value.detail


@pytest.mark.guarantee("G44")
def test_the_pure_call_refuses_an_outside_agreement_without_the_register_and_answers_with_it():
    door = outside()
    with pytest.raises(Refused) as refused:
        is_covered(garage=HOME, agreements=(door,), vehicle_identity="DOOR-1", at=DAY)
    assert refused.value.code == REFUSAL_REGISTRATIONS_NOT_GIVEN
    # Stated and empty: not covered, and the reason is the ordinary one.
    nobody = is_covered(garage=HOME, agreements=(door,), vehicle_identity="DOOR-1", at=DAY,
                        registrations={})
    assert not nobody.covered and nobody.reason_code == NOT_COVERED_NO_AGREEMENT
    # Stated and registered: covered, citing the outside agreement.
    for garage, form in ((HOME, "door1"), (OTHER, "DOOR-1")):
        answer = is_covered(garage=garage, agreements=(door,), vehicle_identity="DOOR-1", at=DAY,
                            registrations={OUTSIDE_ID: (form,)})
        assert answer.covered and answer.agreement_id == OUTSIDE_ID, garage.id
    # Registered to ANOTHER agreement's id: not this one's car.
    other = is_covered(garage=HOME, agreements=(door,), vehicle_identity="DOOR-1", at=DAY,
                       registrations={"ag-somebody": ("door1",)})
    assert not other.covered and other.reason_code == NOT_COVERED_NO_AGREEMENT
    # An outside agreement that does not cover the asking garage is not a
    # candidate, so it is not refused either -- the third garage's question
    # is answered from the agreements that do cover it.
    elsewhere = outside(covered_garage_ids=(HOME.id,))
    assert not is_covered(garage=OTHER, agreements=(elsewhere,), vehicle_identity="DOOR-1",
                          at=DAY).covered


@pytest.mark.guarantee("G44")
def test_a_self_written_agreement_answers_exactly_as_it_did_with_or_without_the_parameter():
    """Not asserted -- compared: the whole Answer, field for field, for a
    covered car and for a stranger, with the parameter absent, empty, and
    naming the agreement."""
    self_written = mine()
    for car in ("SELF-1", "self 1", "NOBODY-1"):
        for garage in (HOME, OTHER):
            plain = is_covered(
                garage=garage, agreements=(self_written,), vehicle_identity=car, at=DAY
            )
            for registrations in ({}, {"ag-self": ("self1",)}, {"ag-self": ()}):
                stated = is_covered(garage=garage, agreements=(self_written,), vehicle_identity=car,
                                    at=DAY, registrations=registrations)
                assert stated == plain, (car, garage.id, registrations)
            assert type(plain) is Answer


# ---------------------------------------------------------------------------
# The store door: the rows the door wrote are what the barrier reads.
# ---------------------------------------------------------------------------


def store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


def _seed(app, tenant_id, *agreements):
    return seed_garages(app, tenant_id, (HOME, OTHER), agreements, now=DAY)


def _register(app, tenant_id, agreement_id, identity):
    with tenant(app, tenant_id) as cursor:
        stored = register_from_outside(cursor, tenant_id, agreement_id, identity, now=DAY)
    app.commit()
    return stored


@pytest.mark.guarantee("G44")
@store_backed
def test_a_door_registered_car_is_covered_at_every_covered_garage_and_a_stranger_is_not(
    app, tenant_id
):
    """B1's own probe, as a guarantee: three cars, two garages, and the door
    car answers like the self-written one -- not like the stranger."""
    _seed(app, tenant_id, mine(), outside())
    _register(app, tenant_id, OUTSIDE_ID, "DOOR-1")
    for garage in (HOME, OTHER):
        self_written = covered_from_store(app, tenant_id, garage.id, "SELF-1", DAY)
        door = covered_from_store(app, tenant_id, garage.id, "DOOR-1", DAY)
        stranger = covered_from_store(app, tenant_id, garage.id, "NOBODY-1", DAY)
        assert self_written.covered and self_written.agreement_id == "ag-self", garage.id
        assert door.covered and door.agreement_id == OUTSIDE_ID, (garage.id, door.reason)
        assert door.entitlement == outside().spots
        assert not stranger.covered and stranger.reason_code == NOT_COVERED_NO_AGREEMENT
        # The over-reach control: the stranger's reason is the same sentence
        # it always was -- and the door car's is not it.
        assert door.reason != stranger.reason
    # Released, the car is a stranger again at both doors.
    from monthly_billing.store.records import release_from_outside

    with tenant(app, tenant_id) as cursor:
        release_from_outside(cursor, tenant_id, OUTSIDE_ID, "DOOR-1")
    app.commit()
    for garage in (HOME, OTHER):
        gone = covered_from_store(app, tenant_id, garage.id, "DOOR-1", DAY)
        assert not gone.covered and gone.reason_code == NOT_COVERED_NO_AGREEMENT


@pytest.mark.guarantee("G44")
@store_backed
def test_the_money_state_reaches_a_door_registered_car_at_every_door(app, tenant_id):
    """What the connection exists for: the outside registrar's agreement is
    billed at the home like any other, and its unpaid invoice past grace stops
    the door car at BOTH garages."""
    _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "DOOR-1")
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 1), now=DAY).lines
    assert line.reference is not None
    inside = datetime(2026, 5, 3, 9, 0, tzinfo=HOME_TZ)
    past = datetime(2026, 5, 20, 9, 0, tzinfo=HOME_TZ)
    for garage in (HOME, OTHER):
        assert covered_from_store(app, tenant_id, garage.id, "DOOR-1", inside).covered, garage.id
        answer = covered_from_store(app, tenant_id, garage.id, "DOOR-1", past)
        assert not answer.covered and answer.reason_code == NOT_COVERED_UNPAID_PAST_GRACE, (
            garage.id, answer.reason,
        )


@pytest.mark.guarantee("G44")
@store_backed
def test_the_command_line_answers_covered_for_a_door_registered_car(app, tenant_id):
    _seed(app, tenant_id, outside())
    _register(app, tenant_id, OUTSIDE_ID, "DOOR-1")
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    for car, code in (("DOOR-1", 0), ("NOBODY-1", 1)):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = main(["covered-in-store", "--tenant", str(tenant_id), "--dsn", dsn,
                        "--garage", OTHER.id, "--vehicle", car, "--at", DAY.isoformat()])
        assert got == code, (car, out.getvalue(), err.getvalue())
