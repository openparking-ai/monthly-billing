"""The owner's exceptions — and refusing rather than inventing.

The garage owner decides what to do about a partial payment, a bounced cheque, a
mid-month refund or a customer who needs another week. This module does not
decide any of them. It records what the owner decided, against a specific
agreement or invoice, with who and when.

**AN EXCEPTION IS AN ACKNOWLEDGEMENT PLUS AN AMOUNT, NEVER A FREE-TEXT NOTE THAT
PRICES SOMETHING.** This is the sharpest rule in the module and it comes from a
real defect in a sibling module here, where a free-text decision note could buy a
surcharge. A note EXPLAINS; an amount CHANGES MONEY; they are different fields
and the amount is typed. Nothing reads the note, ever -- and
``tests/test_g10_a_note_can_never_price.py`` proves it by planting a note and
requiring every figure to be unchanged.

**WHICH KINDS CARRY AN AMOUNT IS A PROPERTY OF THE KIND, NOT OF THE CALLER.** An
exception that changes money and arrives with no amount is REFUSED, by name,
rather than defaulted to zero -- a silent zero is the shape of an owner believing
they credited somebody and nothing having happened.

**BLOCKING DOES NOT REACH THE EXIT LANE, AND NOTHING HERE CAN.** ``BLOCK`` makes
an entitlement answer say not-covered, which means the stay is priced as an
ordinary transient one. It is not a refusal to open a barrier, and this module
has no way to express one: there is no exit call, no deny field, and no
configuration that produces either. See ``entitlement.Answer`` and the guarantee
that derives its field set from the class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .findings import REFUSAL_EXCEPTION_HAS_NO_AMOUNT, Refused
from .money import as_minor
from .sensitive import refuse_instrument_like


class ExceptionKind(Enum):
    """What the owner did. Each carries whether it moves money.

    The two sets are declared here rather than checked at the call site, so
    "does this kind need an amount" has exactly one answer in the codebase.
    """

    EXTEND_GRACE = "extend_grace"
    WAIVE_FEE = "waive_fee"
    CREDIT = "credit"
    REFUND = "refund"
    BLOCK = "block"
    UNBLOCK = "unblock"


#: The kinds that change what somebody pays. Every one of them requires an
#: amount in minor units; the rest refuse one.
MONETARY_KINDS: frozenset[ExceptionKind] = frozenset(
    {ExceptionKind.WAIVE_FEE, ExceptionKind.CREDIT, ExceptionKind.REFUND}
)


@dataclass(frozen=True)
class OwnerException:
    """Who, when, what changed, and why.

    ``note`` is the why, for a person. **Nothing in this module reads it.** It is
    carried, stored and displayed, and it reaches no arithmetic anywhere -- which
    is a property proven by a control rather than by this sentence.
    """

    id: str
    #: Exactly one of these two. An exception against neither is unattached to
    #: anything, and one against both is two exceptions wearing one id.
    agreement_id: str | None
    invoice_reference: str | None
    kind: ExceptionKind
    recorded_by: str
    recorded_at: datetime
    note: str = ""
    amount_minor: int | None = None
    #: Only meaningful for EXTEND_GRACE.
    extra_grace_days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("an exception needs a non-empty id.")
        if not isinstance(self.recorded_by, str) or not self.recorded_by.strip():
            raise ValueError(
                "an exception records WHO made it. An unattributed exception is a "
                "change to somebody's bill that nobody made."
            )
        if not isinstance(self.recorded_at, datetime) or self.recorded_at.tzinfo is None:
            raise ValueError(
                "an exception records WHEN it was made, as an instant carrying its "
                "timezone. A naive one would be read as the server's local time, "
                "which is a property of the server and not of the garage."
            )
        if (self.agreement_id is None) == (self.invoice_reference is None):
            raise ValueError(
                "an exception attaches to exactly one of an agreement or an invoice. "
                "Neither leaves it attached to nothing; both is two exceptions "
                "sharing an id."
            )

        # The note reaches no arithmetic, but it does reach a log and a screen,
        # so it is checked for an instrument like every other free-text field.
        refuse_instrument_like(self.note, f"exception[{self.id}].note")

        if self.kind in MONETARY_KINDS:
            if self.amount_minor is None:
                raise Refused(
                    REFUSAL_EXCEPTION_HAS_NO_AMOUNT,
                    f"exception {self.id!r} is a {self.kind.value} and carries no "
                    "amount_minor.",
                )
            as_minor(self.amount_minor, f"exception[{self.id}].amount_minor")
        elif self.amount_minor is not None:
            raise ValueError(
                f"exception {self.id!r} is a {self.kind.value}, which does not move "
                f"money, and carries amount_minor={self.amount_minor!r}. Refused "
                "rather than ignored: an amount somebody entered and nothing applied "
                "is worse than an error, because the owner believes it took effect."
            )

        if self.kind is ExceptionKind.EXTEND_GRACE:
            days = self.extra_grace_days
            if isinstance(days, bool) or not isinstance(days, int) or days < 1:
                raise ValueError(
                    f"exception {self.id!r} extends the grace period and states "
                    f"extra_grace_days={days!r}. It is a whole number of at least one."
                )
        elif self.extra_grace_days is not None:
            raise ValueError(
                f"exception {self.id!r} is a {self.kind.value} and states "
                "extra_grace_days. One of the two is not what was meant."
            )

    @property
    def changes_money(self) -> bool:
        return self.kind in MONETARY_KINDS


def applied_grace_days(base_days: int, exceptions: tuple[OwnerException, ...]) -> int:
    """The grace period after any EXTEND_GRACE exceptions.

    Extensions add. Two separate decisions to give somebody another week are two
    weeks, which is what an owner who made both of them meant.
    """
    extra = sum(
        e.extra_grace_days or 0
        for e in exceptions
        if e.kind is ExceptionKind.EXTEND_GRACE
    )
    return base_days + extra


def is_blocked(exceptions: tuple[OwnerException, ...]) -> bool:
    """Whether the owner has blocked this agreement, latest decision winning.

    Ordered by when the owner made the decision, not by the order they happen to
    be stored in -- an unblock recorded after a block is the owner changing their
    mind, and reading them in storage order would let a database's idea of
    ordering decide whether somebody gets in.
    """
    relevant = [
        e for e in exceptions if e.kind in (ExceptionKind.BLOCK, ExceptionKind.UNBLOCK)
    ]
    if not relevant:
        return False
    latest = max(relevant, key=lambda e: e.recorded_at)
    return latest.kind is ExceptionKind.BLOCK


def monetary_total_minor(exceptions: tuple[OwnerException, ...]) -> int:
    """What the owner's exceptions change, in minor units.

    Only the monetary kinds contribute, and only through ``amount_minor``. The
    note is not consulted -- not here, and not anywhere.
    """
    return sum(e.amount_minor or 0 for e in exceptions if e.changes_money)
