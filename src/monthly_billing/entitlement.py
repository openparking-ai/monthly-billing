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
)
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
) -> Answer:
    """The one call. An access fact, and nothing else.

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

    mine = [
        a
        for a in agreements
        if a.garage_id == garage.id
        and any(garage.identities_match(v, vehicle_identity) for v in a.vehicles)
    ]
    if not mine:
        return _not_covered(NOT_COVERED_NO_AGREEMENT)

    # The most recent version wins. Two live versions of one agreement is a
    # store-level error, but choosing the highest here is deterministic and
    # traceable -- the answer cites the version it used.
    agreement = max(mine, key=lambda a: (a.id, a.version))

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
        due_day = day_of(has_unpaid_invoice_since, tz)
        if (today - due_day).days > garage.payment_grace_days:
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
            f"of the account's vehicles to be inside at once."
        ),
        entitlement=agreement.spots,
        unchecked=unchecked,
        **cited,
    )
