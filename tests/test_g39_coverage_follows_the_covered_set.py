"""G39 -- an agreement is billed at one HOME garage and COVERS the garages the
owner lists. Coverage is membership of that set; the unpaid, exception and
grace reads follow the home; registrations fan out one row per covered garage
under that garage's own rule, all or none; the entitlement is across the set.

**THE DEFECT THIS ROUND WOULD OTHERWISE SHIP, PLANTED BACK AS A CONTROL.** Before
0004 the store-backed call read the payer's unpaid invoices at the ASKING
garage. The invoice lives at the home garage. The moment an agreement covers a
second garage, that read finds nothing there and calls an unpaid monthly
COVERED at every door except the one that bills it -- and an owner's block
recorded on the home's invoice does not reach the other doors. Both queries
still compile and still pass every single-garage test. The controls here plant
each of them back and require red at the OTHER garage specifically.

**THE FIXTURE IS THE MEASUREMENT.** ``multi_garage_agreement`` is homed at the
month-end garage (Denver, USD, folded) and covers the first-of-month garage
(Phoenix, JPY, exact) -- two garages that differ on every axis a door decides
by, and ``test_fixture_axes.py`` is the control that they do. A pair that
agreed on the identity rule could not show the fan-out normalising differently;
one that agreed on the zone could not show whose clock counts the grace.

Controls: the equality planted back over the membership; the asking garage's
grace and clock over the home's; the asking garage's unpaid and exceptions
queries over the home's; a payer-wide unpaid read (A1.2); the money loader in
the access door; registrations at the home only; the home's rule at every
garage; collisions checked at the home only; a dropped garage keeping its rows;
the holder's release read on the asking garage's home rows; the covered set
defaulted to the home when the document omits it; the home not required in the
set; the home garage not required at a non-home door.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import (
    MULTI_HOME,
    MULTI_OTHER,
    agreement_document,
    multi_garage_agreement,
    simple_agreement,
    tenth_of_month_garage,
)
from monthly_billing.agreement import Agreement, InvalidAgreement, Status, load_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement import Answer, is_covered
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.findings import (
    NOT_COVERED_BLOCKED_BY_OWNER,
    NOT_COVERED_NO_AGREEMENT,
    NOT_COVERED_UNPAID_PAST_GRACE,
    REFUSAL_HOME_GARAGE_NOT_GIVEN,
    REFUSAL_VEHICLE_ALREADY_REGISTERED,
    Refused,
)
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import (
    GarageNotFound,
    registrations_at_garage,
    store_agreement,
)
from store_harness import needs_postgres, query, seed_garages

HOME = MULTI_HOME()  # garage-month-end: Denver, USD, folded, grace 5, bills month-end
OTHER = MULTI_OTHER()  # garage-first: Phoenix, JPY, exact, grace 10, bills the 1st
THIRD = tenth_of_month_garage()  # covered by nothing here
HOME_TZ = ZoneInfo(HOME.timezone)
OTHER_TZ = ZoneInfo(OTHER.timezone)

MULTI = multi_garage_agreement(start_day=date(2026, 1, 5))
PLATE = MULTI.vehicles[1]  # "CAR001"
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=HOME_TZ)


def _at(day: date, hour: int = 9, tz: ZoneInfo = HOME_TZ) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)


def _fixture_holds() -> None:
    """The measurement's own precondition, asserted where it is used."""
    assert HOME.timezone != OTHER.timezone
    assert HOME.currency != OTHER.currency
    assert HOME.identity_rule is not OTHER.identity_rule
    assert HOME.payment_grace_days != OTHER.payment_grace_days
    assert MULTI.garage_id == HOME.id and set(MULTI.covered_garage_ids) == {HOME.id, OTHER.id}


# ---------------------------------------------------------------------------
# The pure call
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G39")
def test_the_pure_call_covers_at_every_listed_garage_and_nowhere_else():
    """Membership, not equality: covered at the home, covered at the other,
    NO_AGREEMENT at a garage the set does not hold. The positive control on
    the negative is the same call at the two covered garages."""
    _fixture_holds()
    at = _at(date(2026, 5, 3))
    home = is_covered(garage=HOME, agreements=(MULTI,), vehicle_identity=PLATE, at=at)
    other = is_covered(garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=at)
    third = is_covered(garage=THIRD, agreements=(MULTI,), vehicle_identity=PLATE, at=at)
    assert home.covered and other.covered
    assert home.agreement_id == other.agreement_id == MULTI.id
    assert not third.covered and third.reason_code == NOT_COVERED_NO_AGREEMENT


@pytest.mark.guarantee("G39")
def test_the_asking_garage_still_compares_the_plate_under_its_own_rule():
    """The reader at the barrier decides how a plate compares. At the folded
    home "car 001" is CAR001; at the exact other garage it is not."""
    _fixture_holds()
    at = _at(date(2026, 5, 3))
    folded = is_covered(garage=HOME, agreements=(MULTI,), vehicle_identity="car 001", at=at)
    exact = is_covered(garage=OTHER, agreements=(MULTI,), vehicle_identity="car 001", at=at)
    assert folded.covered
    assert not exact.covered and exact.reason_code == NOT_COVERED_NO_AGREEMENT
    assert is_covered(garage=OTHER, agreements=(MULTI,), vehicle_identity="CAR001", at=at).covered


@pytest.mark.guarantee("G39")
def test_the_entitlement_is_across_the_covered_set_not_per_garage():
    """Ten spots is ten cars inside across the garages the agreement covers,
    stated to the platform in the answer's own sentence at every door -- not
    ten at each, which the same number at each door would otherwise read as.
    The single-garage answer carries no such clause: it has one garage."""
    at = _at(date(2026, 5, 3))
    for garage in (HOME, OTHER):
        answer = is_covered(garage=garage, agreements=(MULTI,), vehicle_identity=PLATE, at=at)
        assert answer.covered and answer.entitlement == MULTI.spots == 10
        assert "across the 2 garages it covers" in answer.reason, answer.reason
    single = simple_agreement(start_day=date(2026, 1, 5))
    alone = is_covered(garage=HOME, agreements=(single,), vehicle_identity=PLATE, at=at)
    assert alone.covered and alone.entitlement == 10 and "across" not in alone.reason


@pytest.mark.guarantee("G39")
def test_grace_at_the_other_door_is_the_home_garages_not_the_asking_garages():
    """Unpaid since 04-30 at the home (grace 5). At the other garage (grace
    10) the car is covered through 05-05 and not on 05-06 -- the HOME's grace.
    The asking garage's own grace would have covered it through the 10th, and
    that is the plant."""
    _fixture_holds()
    due = _at(date(2026, 4, 30), 0)
    for garage in (HOME, OTHER):
        inside = is_covered(
            garage=garage, agreements=(MULTI,), vehicle_identity=PLATE, at=_at(date(2026, 5, 5)),
            has_unpaid_invoice_since=due, home_garage=HOME,
        )
        past = is_covered(
            garage=garage, agreements=(MULTI,), vehicle_identity=PLATE, at=_at(date(2026, 5, 6)),
            has_unpaid_invoice_since=due, home_garage=HOME,
        )
        assert inside.covered, garage.id
        assert not past.covered and past.reason_code == NOT_COVERED_UNPAID_PAST_GRACE, garage.id
    # The control on the instrument: the other garage's own grace IS longer, so
    # the same question with the roles swapped answers the other way.
    swapped = is_covered(
        garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=_at(date(2026, 5, 6)),
        has_unpaid_invoice_since=due, home_garage=replace(OTHER, id=HOME.id),
    )
    assert swapped.covered, "with a 10-day grace the 6th is inside; the instrument can tell"


@pytest.mark.guarantee("G39")
def test_the_days_of_grace_are_counted_on_the_home_garages_clock():
    """Due at the home's midnight 04-30 (Denver, MDT). On the other garage's
    clock (Phoenix, MST, one hour behind in summer) that instant is still
    04-29 -- so counting to noon on 05-06 reads 7 days there and 6 at home.
    With a 6-day grace the home's clock says covered and the asking garage's
    says not: ONE invoice, ONE lapse instant, whichever door the car is at."""
    _fixture_holds()
    due = datetime(2026, 4, 30, 0, 0, tzinfo=HOME_TZ)
    assert due.astimezone(OTHER_TZ).date() == date(2026, 4, 29), "the fixture premise"
    at = datetime(2026, 5, 6, 12, 0, tzinfo=HOME_TZ)
    assert at.astimezone(OTHER_TZ).date() == date(2026, 5, 6)
    home_six = replace(HOME, payment_grace_days=6)
    at_other = is_covered(
        garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=at,
        has_unpaid_invoice_since=due, home_garage=home_six,
    )
    at_home = is_covered(
        garage=home_six, agreements=(MULTI,), vehicle_identity=PLATE, at=at,
        has_unpaid_invoice_since=due,
    )
    assert at_home.covered, "6 days from 04-30 to 05-06 is not past a 6-day grace"
    assert at_other.covered, "the other door counted the days on its own clock"
    # One more hour of grace exhausted: both doors lapse together.
    later = datetime(2026, 5, 7, 0, 30, tzinfo=HOME_TZ)
    assert not is_covered(
        garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=later,
        has_unpaid_invoice_since=due, home_garage=home_six,
    ).covered


@pytest.mark.guarantee("G39")
def test_asked_at_a_non_home_garage_with_an_unpaid_instant_the_home_is_required():
    """No default to the asking garage's grace: refused by name, and refused
    again when the home handed in is not the agreement's home. At the home
    itself nothing new is required -- every single-garage call is unchanged."""
    due = _at(date(2026, 4, 30), 0)
    at = _at(date(2026, 5, 6))
    with pytest.raises(Refused) as refused:
        is_covered(
            garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=at,
            has_unpaid_invoice_since=due,
        )
    assert refused.value.code == REFUSAL_HOME_GARAGE_NOT_GIVEN
    assert f"{HOME.id!r}" in refused.value.detail and f"{OTHER.id!r}" in refused.value.detail
    with pytest.raises(Refused) as wrong:
        is_covered(
            garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=at,
            has_unpaid_invoice_since=due, home_garage=THIRD,
        )
    assert wrong.value.code == REFUSAL_HOME_GARAGE_NOT_GIVEN
    assert f"{THIRD.id!r}" in wrong.value.detail
    # Controls: at the home, and at the other garage with no unpaid instant.
    assert not is_covered(
        garage=HOME, agreements=(MULTI,), vehicle_identity=PLATE, at=at,
        has_unpaid_invoice_since=due,
    ).covered
    assert is_covered(garage=OTHER, agreements=(MULTI,), vehicle_identity=PLATE, at=at).covered


# ---------------------------------------------------------------------------
# The document and the constructor
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G39")
def test_a_document_without_the_covered_set_is_refused_by_name_not_defaulted():
    document = agreement_document()
    del document["covered_garage_ids"]
    with pytest.raises(InvalidAgreement) as refused:
        load_agreement(document)
    assert "covered_garage_ids" in str(refused.value)
    assert "never defaulted" in str(refused.value)
    # The same document with the set present loads -- the control.
    loaded = load_agreement(agreement_document())
    assert loaded.covered_garage_ids == (loaded.garage_id,)


@pytest.mark.guarantee("G39")
@pytest.mark.parametrize(
    "covered, fragment",
    [
        ((), "lists no covered garages"),
        (("garage-first",), "do not include it"),
        (("garage-month-end", "garage-month-end"), "twice"),
        (("garage-month-end", ""), "non-empty string"),
        ("garage-month-end", "lists no covered garages"),
    ],
    ids=["empty", "home-not-in-set", "duplicate", "blank-id", "not-a-list"],
)
def test_the_constructor_refuses_a_covered_set_that_is_not_a_set_holding_the_home(
    covered, fragment
):
    with pytest.raises(InvalidAgreement) as refused:
        simple_agreement(covered_garage_ids=covered)
    assert fragment in str(refused.value)


@pytest.mark.guarantee("G39")
def test_the_covered_set_round_trips_through_the_document_loader():
    document = copy.deepcopy(agreement_document())
    document["covered_garage_ids"] = ["garage-month-end", "garage-first"]
    loaded = load_agreement(document)
    assert isinstance(loaded, Agreement)
    assert loaded.covered_garage_ids == ("garage-month-end", "garage-first")
    assert loaded.covers_garage("garage-first") and not loaded.covers_garage("garage-tenth")


# ---------------------------------------------------------------------------
# The store: registrations fan out, the reads follow the home
# ---------------------------------------------------------------------------

def store_backed(test):
    """The store-backed half of this module needs a database; the pure half
    does not, so the marks go on the tests rather than on the module."""
    for mark in needs_postgres:
        test = mark(test)
    return test


def _seed_multi(app, tenant_id, *agreements, now=NOW):
    return seed_garages(app, tenant_id, (HOME, OTHER, THIRD), agreements or (MULTI,), now=now)


def _regs(app, tenant_id, garage_uuid):
    with tenant(app, tenant_id) as cursor:
        rows = list(registrations_at_garage(cursor, garage_uuid))
    app.rollback()
    return rows


def _issue_home_invoice(app, tenant_id) -> str:
    """The May invoice for the multi-garage agreement, issued at the HOME on
    04-30 and left unpaid. The run at the home is the only place it can be
    issued -- see test_g40."""
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 1), now=NOW).lines
    assert line.reference is not None
    return line.reference


@pytest.mark.guarantee("G39")
@store_backed
def test_registrations_fan_out_one_row_per_covered_garage_under_its_own_rule(app, tenant_id):
    seeded = _seed_multi(app, tenant_id)
    _fixture_holds()
    home_rows = _regs(app, tenant_id, seeded.garage_uuids[HOME.id])
    other_rows = _regs(app, tenant_id, seeded.garage_uuids[OTHER.id])
    third_rows = _regs(app, tenant_id, seeded.garage_uuids[THIRD.id])
    assert len(home_rows) == len(other_rows) == len(MULTI.vehicles) == 20
    assert third_rows == [], "a garage the set does not hold got a registration"
    # Each garage's OWN rule: folded at the home, byte-for-byte at the other.
    assert ("car001", MULTI.id) in home_rows and ("CAR001", MULTI.id) in other_rows
    assert ("CAR001", MULTI.id) not in home_rows and ("car001", MULTI.id) not in other_rows
    # And the store-backed door answers at both, and not at the third.
    at = _at(date(2026, 5, 3))
    assert covered_from_store(app, tenant_id, HOME.id, "car 001", at).covered
    assert covered_from_store(app, tenant_id, OTHER.id, "CAR001", at).covered
    assert not covered_from_store(app, tenant_id, OTHER.id, "car 001", at).covered
    third = covered_from_store(app, tenant_id, THIRD.id, "CAR001", at)
    assert not third.covered and third.reason_code == NOT_COVERED_NO_AGREEMENT


@pytest.mark.guarantee("G39")
@store_backed
def test_the_store_backed_answer_is_the_pure_answer_class_at_the_other_door(app, tenant_id):
    """G21's field-set claim, at the door 0004 opened: the same class, no money
    in its prose, whichever garage asks."""
    from test_g6_no_money_crosses_the_entitlement_call import MONEY_IN_PROSE

    _seed_multi(app, tenant_id)
    _issue_home_invoice(app, tenant_id)
    for garage, day in ((OTHER, date(2026, 5, 3)), (OTHER, date(2026, 5, 20))):
        answer = covered_from_store(app, tenant_id, garage.id, PLATE, _at(day))
        assert type(answer) is Answer
        assert not MONEY_IN_PROSE.search(answer.reason), answer.reason


@pytest.mark.guarantee("G39")
@store_backed
def test_an_unpaid_invoice_at_the_home_makes_the_car_not_covered_at_every_door(app, tenant_id):
    """THE DEFECT: the invoice lives at the home. Read at the asking garage it
    is not there, and the other door says covered while the home says not."""
    _seed_multi(app, tenant_id)
    _issue_home_invoice(app, tenant_id)
    inside, past = _at(date(2026, 5, 5)), _at(date(2026, 5, 6))
    for garage in (HOME, OTHER):
        assert covered_from_store(app, tenant_id, garage.id, PLATE, inside).covered, garage.id
        answer = covered_from_store(app, tenant_id, garage.id, PLATE, past)
        assert not answer.covered and answer.reason_code == NOT_COVERED_UNPAID_PAST_GRACE, (
            f"at {garage.id}: {answer.reason}"
        )
    # The other garage's grace is 10 days; had ITS grace applied, 05-06 would
    # still be covered there. It is the home's 5 that lapsed the car.
    assert OTHER.payment_grace_days == 10 and HOME.payment_grace_days == 5


@pytest.mark.guarantee("G39")
@store_backed
def test_an_owners_block_on_the_homes_invoice_reaches_the_other_door(app, tenant_id):
    _seed_multi(app, tenant_id)
    reference = _issue_home_invoice(app, tenant_id)
    when = _at(date(2026, 5, 3))
    assert covered_from_store(app, tenant_id, OTHER.id, PLATE, when).covered
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="exc-block-home", agreement_id=None, invoice_reference=reference,
            kind=ExceptionKind.BLOCK, recorded_by="the owner", recorded_at=_at(date(2026, 5, 1)),
        ),
    )
    for garage in (HOME, OTHER):
        blocked = covered_from_store(app, tenant_id, garage.id, PLATE, when)
        assert not blocked.covered, garage.id
        assert blocked.reason_code == NOT_COVERED_BLOCKED_BY_OWNER, garage.id


@pytest.mark.guarantee("G39")
@store_backed
def test_an_owners_grace_extension_on_the_homes_invoice_reaches_the_other_door(app, tenant_id):
    _seed_multi(app, tenant_id)
    reference = _issue_home_invoice(app, tenant_id)
    day = _at(date(2026, 5, 7))
    assert not covered_from_store(app, tenant_id, OTHER.id, PLATE, day).covered
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="exc-grace-home", agreement_id=None, invoice_reference=reference,
            kind=ExceptionKind.EXTEND_GRACE, recorded_by="the owner",
            recorded_at=_at(date(2026, 5, 6)), note="another week", extra_grace_days=7,
        ),
    )
    # 5 + 7 = 12 days from 04-30: covered on the 12th, not on the 13th, at BOTH doors.
    for garage in (HOME, OTHER):
        assert covered_from_store(app, tenant_id, garage.id, PLATE, day).covered, garage.id
        assert covered_from_store(app, tenant_id, garage.id, PLATE, _at(date(2026, 5, 12))).covered
        assert not covered_from_store(
            app, tenant_id, garage.id, PLATE, _at(date(2026, 5, 13))
        ).covered


@pytest.mark.guarantee("G39")
@store_backed
def test_two_agreements_of_one_payer_homed_at_two_garages_are_independent(app, tenant_id):
    """A1.2. The payer holds MULTI (homed at HOME, covering OTHER) and a second
    agreement homed at OTHER. MULTI's May invoice at HOME goes unpaid past
    grace; the second agreement reads COVERED at OTHER -- ITS home has no
    unpaid invoice -- while MULTI reads not-covered at both doors. A read
    keyed payer-wide across homes would lapse the second agreement too."""
    second = simple_agreement(
        id="ag-second", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=("SECOND-1",), start_day=date(2026, 1, 5),
    )
    assert second.payer_id == MULTI.payer_id == "payer-acme"
    _seed_multi(app, tenant_id, MULTI, second)
    _issue_home_invoice(app, tenant_id)
    past = _at(date(2026, 5, 20))
    lapsed = covered_from_store(app, tenant_id, OTHER.id, PLATE, past)
    assert not lapsed.covered and lapsed.reason_code == NOT_COVERED_UNPAID_PAST_GRACE
    other = covered_from_store(app, tenant_id, OTHER.id, "SECOND-1", past)
    assert other.covered and other.agreement_id == "ag-second", other.reason
    assert query(
        app, tenant_id, "SELECT count(*) FROM invoices WHERE paid_at IS NULL"
    ) == [(1,)], "the premise: exactly one unpaid invoice, MULTI's, at the home"


@pytest.mark.guarantee("G39")
@store_backed
def test_a_collision_at_a_covered_but_not_home_garage_refuses_by_name_and_writes_nothing(
    app, tenant_id
):
    """A1.3. ag-other, homed at OTHER, holds TAKEN there. MULTI v2 adds TAKEN:
    at the home it is free, at OTHER it is ag-other's, and the whole
    registration is refused naming OTHER and ag-other -- with the transaction
    still open and every registration row exactly as it was, counted before
    and after. The 0003 rule met at each garage the agreement now covers."""
    holder = simple_agreement(
        id="ag-other", payer_id="payer-z", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=("TAKEN",), start_day=date(2026, 1, 5),
    )
    seeded = _seed_multi(app, tenant_id, MULTI, holder)
    home_uuid, other_uuid = seeded.garage_uuids[HOME.id], seeded.garage_uuids[OTHER.id]
    before = {g: _regs(app, tenant_id, g) for g in (home_uuid, other_uuid)}
    v2 = multi_garage_agreement(
        version=2, vehicles=(*MULTI.vehicles, "TAKEN"), start_day=date(2026, 1, 5)
    )
    try:
        with tenant(app, tenant_id) as cursor:
            with pytest.raises(Refused) as refused:
                store_agreement(
                    cursor, tenant_id, HOME, home_uuid, seeded.payer_uuids[MULTI.payer_id], v2,
                    now=NOW,
                )
            assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
            assert f"garage {OTHER.id!r}" in refused.value.detail
            assert "'ag-other'" in refused.value.detail and "active" in refused.value.detail
            # Inside the still-open transaction: nothing changed, and it is usable.
            cursor.execute("SELECT count(*) FROM vehicle_registrations")
            assert cursor.fetchone() == (sum(len(rows) for rows in before.values()),)
            cursor.execute(
                "SELECT max(version) FROM agreements WHERE external_id = %s", (MULTI.id,)
            )
            assert cursor.fetchone() == (1,)
    except BaseException:
        # A failed assertion here leaves the module's shared connection inside
        # an aborted transaction; end it, so this test's red is this test's
        # alone and the ones after it are measured rather than poisoned.
        app.rollback()
        raise
    app.commit()  # the caller that treats Refused as an outcome and goes on
    assert {g: _regs(app, tenant_id, g) for g in (home_uuid, other_uuid)} == before
    assert query(app, tenant_id, "SELECT count(*) FROM agreement_garages") == [(3,)], (
        "MULTI v1 covers two garages and ag-other one; v2 wrote no covered-set row"
    )
    # The control on the instrument: the same v2 with a FREE plate is stored,
    # at both garages.
    free = multi_garage_agreement(
        version=2, vehicles=(*MULTI.vehicles, "FREE-1"), start_day=date(2026, 1, 5)
    )
    with tenant(app, tenant_id) as cursor:
        store_agreement(
            cursor, tenant_id, HOME, home_uuid, seeded.payer_uuids[MULTI.payer_id], free, now=NOW
        )
    app.commit()
    assert ("free1", MULTI.id) in _regs(app, tenant_id, home_uuid)
    assert ("FREE-1", MULTI.id) in _regs(app, tenant_id, other_uuid)


@pytest.mark.guarantee("G39")
@store_backed
def test_a_version_that_drops_a_garage_releases_every_row_there(app, tenant_id):
    seeded = _seed_multi(app, tenant_id)
    other_uuid = seeded.garage_uuids[OTHER.id]
    at = _at(date(2026, 5, 3))
    assert len(_regs(app, tenant_id, other_uuid)) == 20
    assert covered_from_store(app, tenant_id, OTHER.id, PLATE, at).covered
    home_only = simple_agreement(version=2, start_day=date(2026, 1, 5))  # covers HOME alone
    with tenant(app, tenant_id) as cursor:
        store_agreement(
            cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id],
            seeded.payer_uuids[MULTI.payer_id], home_only, now=NOW,
        )
    app.commit()
    assert _regs(app, tenant_id, other_uuid) == [], "the dropped garage kept its rows"
    gone = covered_from_store(app, tenant_id, OTHER.id, PLATE, at)
    assert not gone.covered and gone.reason_code == NOT_COVERED_NO_AGREEMENT
    assert covered_from_store(app, tenant_id, HOME.id, PLATE, at).covered, "the home is untouched"
    # And another agreement can now take the plate at OTHER.
    taker = simple_agreement(
        id="ag-taker", payer_id="payer-z", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=(PLATE,), start_day=date(2026, 1, 5),
    )
    with tenant(app, tenant_id) as cursor:
        from monthly_billing.store.records import store_payer

        payer_z = store_payer(cursor, tenant_id, "payer-z", "Z")
        store_agreement(cursor, tenant_id, OTHER, other_uuid, payer_z, taker, now=NOW)
    app.commit()
    assert covered_from_store(app, tenant_id, OTHER.id, PLATE, at).agreement_id == "ag-taker"


@pytest.mark.guarantee("G39")
@store_backed
def test_a_cancelled_holder_homed_elsewhere_frees_the_plate_on_its_day(app, tenant_id):
    """MULTI (homed at HOME) holds PLATE at OTHER, then cancels effective 06-01.
    An agreement homed at OTHER listing PLATE is refused on 05-31 naming the
    day, and accepted on 06-01. The holder's release day is read by the
    holder's IDENTITY: a read keyed on OTHER's own home rows would find no
    version of MULTI there and call it active forever."""
    seeded = _seed_multi(app, tenant_id)
    other_uuid = seeded.garage_uuids[OTHER.id]
    cancelled = multi_garage_agreement(
        version=2, start_day=date(2026, 1, 5), status=Status.CANCELLED,
        cancelled_effective_day=date(2026, 6, 1),
    )
    with tenant(app, tenant_id) as cursor:
        store_agreement(
            cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id],
            seeded.payer_uuids[MULTI.payer_id], cancelled, now=NOW,
        )
        from monthly_billing.store.records import store_payer

        payer_z = store_payer(cursor, tenant_id, "payer-z", "Z")
    app.commit()
    taker = simple_agreement(
        id="ag-taker", payer_id="payer-z", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=(PLATE,), start_day=date(2026, 1, 5),
    )
    day_before = datetime(2026, 5, 31, 23, 0, tzinfo=OTHER_TZ)
    with pytest.raises(Refused) as refused:
        with tenant(app, tenant_id) as cursor:
            store_agreement(cursor, tenant_id, OTHER, other_uuid, payer_z, taker, now=day_before)
    app.rollback()
    assert refused.value.code == REFUSAL_VEHICLE_ALREADY_REGISTERED
    assert "2026-06-01" in refused.value.detail, refused.value.detail
    on_the_day = datetime(2026, 6, 1, 0, 30, tzinfo=OTHER_TZ)
    with tenant(app, tenant_id) as cursor:
        store_agreement(cursor, tenant_id, OTHER, other_uuid, payer_z, taker, now=on_the_day)
    app.commit()
    answer = covered_from_store(app, tenant_id, OTHER.id, PLATE, on_the_day)
    assert answer.covered and answer.agreement_id == "ag-taker"


@pytest.mark.guarantee("G39")
@store_backed
def test_a_covered_garage_the_store_does_not_hold_is_refused_by_id(app, tenant_id):
    stranger = multi_garage_agreement(
        covered_garage_ids=(HOME.id, "garage-nowhere"), start_day=date(2026, 1, 5)
    )
    with pytest.raises(GarageNotFound) as missing:
        _seed_multi(app, tenant_id, stranger)
    app.rollback()
    assert "'garage-nowhere'" in str(missing.value)
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(0,)]


@pytest.mark.guarantee("G39")
@store_backed
def test_a_single_garage_agreement_behaves_exactly_as_before(app, tenant_id):
    """The control on the whole round: one garage, one covered-set row of its
    home, the same answers G21 measured -- covered inside grace, not past it."""
    seeded = seed_garages(
        app, tenant_id, (HOME,), (simple_agreement(start_day=date(2026, 1, 5)),), now=NOW
    )
    assert query(
        app, tenant_id,
        "SELECT g.external_id FROM agreement_garages ag JOIN garages g ON g.id = ag.garage_id",
    ) == [(HOME.id,)]
    assert len(_regs(app, tenant_id, seeded.garage_uuid)) == 20
    run_billing(app, tenant_id, HOME.id, date(2026, 5, 1), now=NOW)
    assert covered_from_store(app, tenant_id, HOME.id, PLATE, _at(date(2026, 5, 5))).covered
    past = covered_from_store(app, tenant_id, HOME.id, PLATE, _at(date(2026, 5, 6)))
    assert not past.covered and past.reason_code == NOT_COVERED_UNPAID_PAST_GRACE
    assert "across" not in covered_from_store(
        app, tenant_id, HOME.id, PLATE, _at(date(2026, 5, 3))
    ).reason


@pytest.mark.guarantee("G39")
@store_backed
def test_the_command_line_answers_at_the_other_garage_from_the_store(app, tenant_id, capsys):
    from monthly_billing.cli import main
    from store_harness import APP_PASSWORD, DSN

    _seed_multi(app, tenant_id)
    _issue_home_invoice(app, tenant_id)
    store = ["--tenant", str(tenant_id), "--dsn",
             f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"]
    assert main(["covered-in-store", *store, "--garage", OTHER.id, "--vehicle", PLATE,
                 "--at", _at(date(2026, 5, 3)).isoformat()]) == 0
    assert capsys.readouterr().out.startswith("COVERED")
    assert main(["covered-in-store", *store, "--garage", OTHER.id, "--vehicle", PLATE,
                 "--at", _at(date(2026, 5, 7)).isoformat()]) == 1
    out = capsys.readouterr().out
    assert out.startswith("NOT COVERED") and "home garage's grace" in out
