"""Is this vehicle covered right now, and for how many cars?

**THIS IS AN ACCESS FACT. NO FEE, NO AMOUNT, NO BALANCE EVER CROSSES IT.** Not as
a field, not as a number inside a sentence, not as a hint. A parking lane asks
whether a vehicle is covered and gets covered or not-covered with a plain reason,
and that is the entire surface. ``Answer`` is frozen with a fixed field set and a
test derives that set from the class rather than from a list somebody typed, so a
monetary field cannot be added without a guarantee going red.

**THE MODULE STATES THE ENTITLEMENT. IT DOES NOT COUNT.** ``spots`` is how many of
this account's cars may be inside at once. Which cars ARE inside is live session
state that belongs to whatever runs the garage; a module that tracked it would
need to be told about every entry and exit, and would have stopped being
standalone. So the answer carries N and the platform decides that the (N+1)th car
is a transient.

**NOT COVERED NEVER MEANS REFUSE, AND IT NEVER MEANS REFUSE EXIT.** A stay that
is not covered is an ordinary transient stay that something else prices. That
sentence travels on every not-covered answer, from one place, because seven
copies of it would drift and the drifted one would be read at a barrier.

**ONE CAR, ONE AGREEMENT PER GARAGE -- AND WHERE THAT DOES NOT HOLD, REFUSE.**
His ruling. The store registers a vehicle identity to one agreement per garage
and refuses a second; so the agreements this call is handed should list a
vehicle under ONE agreement identity, at whatever versions. Among versions of
one identity the latest wins -- chosen BEFORE the coverage question is asked of
it, so a garage a newer version dropped is not answered on an older one. If a
library caller hands in two different identities listing the same vehicle, this
call REFUSES by name
(``REFUSAL_VEHICLE_ON_TWO_AGREEMENTS``) rather than picking one: the two may
disagree about coverage, and a pick is a wrong answer given confidently. The
refusal is a first-class third result beside covered and not-covered.

**COVERAGE IS MEMBERSHIP OF THE AGREEMENT'S COVERED SET; MONEY IS NOT.** An
agreement is billed at one HOME garage and may cover others the owner listed
(``Agreement.covered_garage_ids``). This call selects the agreements whose
covered set holds the asking garage -- not the ones homed there -- and the asking
garage still decides everything else about the door: the clock the agreement
axes are judged on, and the identity rule the plate is compared under, because
that is a property of the reader at that barrier. The GRACE is the exception, and
it is the home's: an unpaid invoice lives at the garage that billed it, so its
grace and its clock decide the day coverage lapses at EVERY door, or one invoice
would expire on two different days depending on which door the car is at. A
caller asking at a non-home garage with an unpaid instant therefore hands in the
home garage, or is refused by name (``REFUSAL_HOME_GARAGE_NOT_GIVEN``) rather
than answered with the asking garage's grace. The entitlement is ACROSS the
covered set: ten spots is ten cars inside across those garages, not ten at each.

**THE ANSWER SAYS WHAT IT COULD NOT CHECK.** Access hours are a condition on a
STAY -- entry no earlier than, exit no later than -- and at the moment a car
arrives, the exit half is unknowable. An answer that quietly reported "covered"
on the entry half alone would be a confident answer to a question nobody asked;
the caller would read it as a promise about the whole stay, and it is not one. So
``unchecked`` names every condition this call could not evaluate, and it is empty
only when the answer really did evaluate all of them.

This is the project's standing acceptance applied to a coverage question: where a
module is unsure it says so, and an unsure answer is a first-class result rather
than an error. It is also the honest form of the sibling rate module's settled
all-conditions-or-nothing rule -- a special applies only if the stay meets EVERY
one of its conditions, entry and exit -- which cannot be evaluated at entry, and
so must be reported as not-yet-evaluated rather than assumed either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .agreement import Agreement, Status
from .findings import (
    NOT_COVERED_BLOCKED_BY_OWNER,
    NOT_COVERED_CANCELLED,
    NOT_COVERED_MEANS,
    NOT_COVERED_NO_AGREEMENT,
    NOT_COVERED_NOT_STARTED,
    NOT_COVERED_OUTSIDE_ACCESS_HOURS,
    NOT_COVERED_PAUSED,
    NOT_COVERED_REASONS,
    NOT_COVERED_UNPAID_PAST_GRACE,
    REFUSAL_HOME_GARAGE_NOT_GIVEN,
    REFUSAL_VEHICLE_ON_TWO_AGREEMENTS,
)
from .findings import Refused as _Refused  # not exported: this module decides no exit
from .garage import Garage
from .localday import day_of, zone

#: The conditions this call evaluates. Named so ``unchecked`` can refer to one
#: without a string appearing at two sites.
CONDITION_ACCESS_HOURS_AT_EXIT = "access_hours_at_exit"


@dataclass(frozen=True)
class Answer:
    """Covered or not, why, and for how many cars.

    **EVERY FIELD ON THIS CLASS IS AN ACCESS FACT.** The guarantee that no money
    crosses this call is enforced against this class's own field set -- see
    ``tests/test_g6_no_money_crosses_the_entitlement_call.py``, which derives the
    names from the dataclass rather than from a list, so a field added tomorrow
    is covered the day it exists.

    ``entitlement`` is present only on a covered answer. On a not-covered one it
    is None, deliberately: reporting "you are entitled to 10 spots, and no" is
    two answers, and the second is the one that was asked for.
    """

    covered: bool
    #: The plain-English sentence, always. On a covered answer it says so; on a
    #: not-covered one it is the reason plus what not-covered means.
    reason: str
    #: The machine-readable reason code, on a not-covered answer only.
    reason_code: str | None = None
    #: How many of this account's vehicles may be inside at once.
    entitlement: int | None = None
    #: Which agreement, and which version of it, decided this.
    agreement_id: str | None = None
    agreement_version: int | None = None
    #: Conditions this call could not evaluate from what it was given.
    unchecked: tuple[str, ...] = ()


def _not_covered(code: str, **extra: object) -> Answer:
    return Answer(
        covered=False,
        reason=f"{NOT_COVERED_REASONS[code]} {NOT_COVERED_MEANS}",
        reason_code=code,
        **extra,  # type: ignore[arg-type]
    )


def is_covered(
    *,
    garage: Garage,
    agreements: tuple[Agreement, ...],
    vehicle_identity: str,
    at: datetime,
    stay_entered_at: datetime | None = None,
    has_unpaid_invoice_since: datetime | None = None,
    blocked_by_owner: bool = False,
    home_garage: Garage | None = None,
) -> Answer:
    """The one call. An access fact, and nothing else.

    ``garage`` is the ASKING garage -- the barrier the car is at. ``home_garage``
    is the garage that BILLS the agreement, whose grace period and clock decide
    an unpaid invoice; it is only consulted when ``has_unpaid_invoice_since`` is
    given, and when the asking garage is the home it may be left unset. Asked at
    another garage the agreement covers, with an unpaid instant and no home, the
    call refuses by name rather than reading the asking garage's grace.

    ``at`` is the instant asked about. ``stay_entered_at`` is the entry instant
    when one is known -- supplied at exit, absent at entry. Where access hours are
    stated and no entry instant is given, the exit half of the condition cannot
    be evaluated and the answer says so in ``unchecked`` rather than assuming it.

    ``has_unpaid_invoice_since`` is the instant an unpaid invoice fell due, from
    the caller. It is passed in rather than looked up because the entitlement
    question and the store are separable, and a lane asking this question should
    not require this module to hold a database connection.
    """
    tz = zone(garage.timezone)
    today = day_of(at, tz)

    # Among the versions of one identity the LATEST wins -- chosen BEFORE the
    # coverage filter, never after it. Filtering first and then taking the
    # latest survivor answered a garage the newer version DROPPED on the
    # strength of the older version that listed it: the rule the store's
    # access door already states (records.load_agreements_covering_garage),
    # brought here so that one rule lives in one shape.
    mine = [
        a
        for a in _latest_per_identity(agreements)
        if a.covers_garage(garage.id)
        and any(garage.identities_match(v, vehicle_identity) for v in a.vehicles)
    ]
    if not mine:
        return _not_covered(NOT_COVERED_NO_AGREEMENT)

    # Judged on the latest versions: an identity whose newer version dropped
    # the vehicle no longer lists it, so it is not a second claimant.
    identities = sorted({a.id for a in mine})
    if len(identities) > 1:
        raise _Refused(
            REFUSAL_VEHICLE_ON_TWO_AGREEMENTS,
            f"vehicle {vehicle_identity!r} at garage {garage.id!r} is listed by "
            f"agreements {', '.join(repr(i) for i in identities)}.",
        )
    # One identity, one version -- its latest -- and the answer cites it.
    (agreement,) = mine

    cited = {
        "agreement_id": agreement.id,
        "agreement_version": agreement.version,
    }

    if today < agreement.start_day:
        return _not_covered(NOT_COVERED_NOT_STARTED, **cited)

    if (
        agreement.status is Status.CANCELLED
        and agreement.cancelled_effective_day is not None
        and today >= agreement.cancelled_effective_day
    ):
        return _not_covered(NOT_COVERED_CANCELLED, **cited)

    if agreement.is_paused_on(today):
        return _not_covered(NOT_COVERED_PAUSED, **cited)

    if blocked_by_owner:
        # An owner's exception, and it reaches the barrier as a plain reason like
        # any other. It still does not refuse exit -- see NOT_COVERED_MEANS.
        return _not_covered(NOT_COVERED_BLOCKED_BY_OWNER, **cited)

    if has_unpaid_invoice_since is not None:
        home = _home_of(agreement, garage, home_garage)
        # The home's clock for BOTH days: the invoice fell due in the home's zone,
        # and counting from there in the asking garage's zone would move the day
        # coverage lapses by a day for a door a few zones away.
        home_tz = zone(home.timezone)
        due_day = day_of(has_unpaid_invoice_since, home_tz)
        if (day_of(at, home_tz) - due_day).days > home.payment_grace_days:
            return _not_covered(NOT_COVERED_UNPAID_PAST_GRACE, **cited)

    unchecked: tuple[str, ...] = ()
    if agreement.access_hours is not None:
        local = at.astimezone(tz).timetz().replace(tzinfo=None)
        hours = agreement.access_hours

        if stay_entered_at is None:
            # Asked at entry: only the entry half is knowable.
            if local < hours.entry_from:
                return _not_covered(NOT_COVERED_OUTSIDE_ACCESS_HOURS, **cited)
            unchecked = (CONDITION_ACCESS_HOURS_AT_EXIT,)
        else:
            entry_local = stay_entered_at.astimezone(tz).timetz().replace(tzinfo=None)
            if entry_local < hours.entry_from or local > hours.exit_by:
                return _not_covered(NOT_COVERED_OUTSIDE_ACCESS_HOURS, **cited)

    return Answer(
        covered=True,
        reason=(
            f"Covered: this vehicle is on an agreement entitling {agreement.spots} "
            f"of the account's vehicles to be inside at once"
            + (
                f" across the {len(agreement.covered_garage_ids)} garages it covers."
                if len(agreement.covered_garage_ids) > 1
                else "."
            )
        ),
        entitlement=agreement.spots,
        unchecked=unchecked,
        **cited,
    )


def _latest_per_identity(agreements: tuple[Agreement, ...]) -> tuple[Agreement, ...]:
    """One agreement per identity: its highest version among those handed in.
    Order of the input is irrelevant to the result."""
    latest: dict[str, Agreement] = {}
    for a in agreements:
        held = latest.get(a.id)
        if held is None or a.version > held.version:
            latest[a.id] = a
    return tuple(latest.values())


def _home_of(agreement: Agreement, garage: Garage, home_garage: Garage | None) -> Garage:
    """The garage whose grace and clock judge this agreement's unpaid invoice.

    The asking garage IS the home when the agreement is billed there, which is
    every single-garage agreement; otherwise the caller must have handed the home
    in, and one that names a garage other than the agreement's home is refused
    too -- it would be somebody else's grace wearing the right parameter name.
    """
    if home_garage is None:
        if agreement.garage_id == garage.id:
            return garage
        raise _Refused(
            REFUSAL_HOME_GARAGE_NOT_GIVEN,
            f"agreement {agreement.id!r} is billed at garage {agreement.garage_id!r} and "
            f"was asked about at garage {garage.id!r} with an unpaid invoice to weigh; "
            "pass home_garage.",
        )
    if home_garage.id != agreement.garage_id:
        raise _Refused(
            REFUSAL_HOME_GARAGE_NOT_GIVEN,
            f"agreement {agreement.id!r} is billed at garage {agreement.garage_id!r}, but "
            f"the home_garage handed in is {home_garage.id!r}.",
        )
    return home_garage
