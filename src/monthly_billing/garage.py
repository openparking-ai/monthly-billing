"""The garage: the things one billing day, one timezone and one currency decide.

**WHY THIS RECORD EXISTS AT ALL.** Four fields that decide money are properties
of the GARAGE and not of an account: the billing day ("one day per garage, not
per account"), the timezone every calendar day is measured in, the currency
amounts are denominated in, and the number of days an unpaid account stays
covered. An agreement carries none of them, and a module that read them off an
agreement would let two accounts at one garage bill on different days -- which is
the thing the one-day-per-garage rule exists to prevent.

**THE BILLING DAY IS A PICKER, NOT AN INTEGER.** ``BillingDay`` is a closed set
of options. An integer field would accept 31, and a garage that chose 31 would
have no February billing day at all -- so the option is ``LAST_DAY_OF_MONTH``,
which resolves to the 28th, the 29th, the 30th or the 31st as that month
requires. February is then not a special case anybody has to remember, which is
the entire reason the option set is shaped this way.

**EVERY FIELD HERE IS REQUIRED AND NONE HAS A DEFAULT.** A default billing day
charges somebody on the wrong date; a default grace period either strands a
paying customer at a barrier or covers an unpaid one forever; a default identity
rule decides whether a monthly parker is recognised, and an unrecognised monthly
parker pays transient. The module's disposition throughout is to refuse and name
the missing field rather than assume one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .currency import validate_currency
from .findings import (
    REFUSAL_NO_BILLING_DAY,
    REFUSAL_NO_IDENTITY_RULE,
    REFUSAL_NO_PAYMENT_GRACE,
    Refused,
)
from .localday import add_months, last_day_of_month, zone


class BillingDay(Enum):
    """The offered options. A garage picks one; there is no free integer.

    ``NTH_DAY_OF_MONTH`` carries its own day number and is refused above 28, for
    the reason in the module docstring: 29, 30 and 31 do not exist in every
    month, and a garage that wants the end of the month wants
    ``LAST_DAY_OF_MONTH``, which is a different thing and always exists.
    """

    FIRST_DAY_OF_MONTH = "first_day_of_month"
    LAST_DAY_OF_MONTH = "last_day_of_month"
    NTH_DAY_OF_MONTH = "nth_day_of_month"


class IdentityRule(Enum):
    """How a vehicle identity written on an agreement is compared to one presented.

    **This is a garage decision because it is an operational one.** A garage
    whose identities come from a plate reader wants the forgiving rule; one whose
    identities are opaque tokens issued by an enrolment process wants the exact
    one, because for those two identities differing by a space are two different
    vehicles and folding them together would cover the wrong car.
    """

    #: Byte-for-byte. Nothing is normalised away.
    EXACT = "exact"
    #: Case-folded, and every character that is not a letter or a digit removed.
    #: "ABC-123", "abc 123" and "abc123" are then one identity.
    FOLDED_ALPHANUMERIC = "folded_alphanumeric"


@dataclass(frozen=True)
class Garage:
    """One garage. One billing day, one timezone, one currency, one grace period.

    ``id`` is opaque to this module. It is compared, never parsed, and nothing
    here assumes it came from any particular system -- a module that assumed our
    platform exists would have stopped being standalone.
    """

    id: str
    timezone: str
    currency: str
    billing_day: BillingDay
    payment_grace_days: int
    identity_rule: IdentityRule
    #: Only meaningful when ``billing_day`` is ``NTH_DAY_OF_MONTH``.
    billing_day_of_month: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("a garage id must be a non-empty string.")

        # Refused, not defaulted. Each of these three has its own refusal code
        # and its own sentence, because "something is missing" is not actionable
        # and "the billing day is missing" is.
        if not isinstance(self.billing_day, BillingDay):
            raise Refused(
                REFUSAL_NO_BILLING_DAY,
                f"garage {self.id!r} has billing_day={self.billing_day!r}, which is "
                f"not one of {[o.value for o in BillingDay]}.",
            )
        if not isinstance(self.identity_rule, IdentityRule):
            raise Refused(
                REFUSAL_NO_IDENTITY_RULE,
                f"garage {self.id!r} has identity_rule={self.identity_rule!r}, which "
                f"is not one of {[o.value for o in IdentityRule]}.",
            )
        if isinstance(self.payment_grace_days, bool) or not isinstance(
            self.payment_grace_days, int
        ):
            # bool first: it is an int subclass, and True would read as one day.
            raise Refused(
                REFUSAL_NO_PAYMENT_GRACE,
                f"garage {self.id!r} has payment_grace_days="
                f"{self.payment_grace_days!r}, which is not a whole number of days.",
            )
        if self.payment_grace_days < 0:
            raise Refused(
                REFUSAL_NO_PAYMENT_GRACE,
                f"garage {self.id!r} has a negative grace period "
                f"({self.payment_grace_days}). Zero is a valid choice and means an "
                "invoice stops covering the day after it falls due.",
            )

        validate_currency(self.currency, f"garage[{self.id}].currency")
        zone(self.timezone)  # refuses here rather than at the first billing run

        if self.billing_day is BillingDay.NTH_DAY_OF_MONTH:
            n = self.billing_day_of_month
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 28:
                raise Refused(
                    REFUSAL_NO_BILLING_DAY,
                    f"garage {self.id!r} bills on the nth day of the month and states "
                    f"n={n!r}. It must be a whole number from 1 to 28. 29, 30 and 31 "
                    "are refused because they do not exist in every month; a garage "
                    "that means the end of the month states LAST_DAY_OF_MONTH, which "
                    "always exists and is the 28th in February.",
                )
        elif self.billing_day_of_month is not None:
            raise Refused(
                REFUSAL_NO_BILLING_DAY,
                f"garage {self.id!r} states billing_day_of_month="
                f"{self.billing_day_of_month!r} while its billing day is "
                f"{self.billing_day.value}. One of the two is not what was meant, and "
                "guessing which would put the charge on a date nobody chose.",
            )

    # ---------------------------------------------------------------- billing day

    def billing_day_in(self, year: int, month: int) -> date:
        """The date this garage bills on, in that month.

        This is where month-end stops being a special case: ``LAST_DAY_OF_MONTH``
        asks the calendar rather than carrying a number, so February needs no
        branch here and none anywhere downstream.
        """
        if self.billing_day is BillingDay.FIRST_DAY_OF_MONTH:
            return date(year, month, 1)
        if self.billing_day is BillingDay.LAST_DAY_OF_MONTH:
            return last_day_of_month(year, month)
        assert self.billing_day_of_month is not None  # enforced in __post_init__
        return date(year, month, self.billing_day_of_month)

    def next_billing_day_on_or_after(self, day: date) -> date:
        """The first billing date falling on or after ``day``."""
        candidate = self.billing_day_in(day.year, day.month)
        if candidate >= day:
            return candidate
        following = add_months(
            date(day.year, day.month, 1),
            1,
            clamp_to_month_end=False,
        )
        return self.billing_day_in(following.year, following.month)

    # ---------------------------------------------------------------- identity

    def normalise_identity(self, identity: str) -> str:
        """Put a vehicle identity into the form this garage compares in.

        Refuses an identity that normalises to nothing. "---" under the folding
        rule is the empty string, and an empty identity would match every other
        empty one -- so one malformed enrolment would cover every car whose
        identity was equally malformed. Loud is the only safe direction here.
        """
        if not isinstance(identity, str):
            raise ValueError(
                f"a vehicle identity is a string; this one is {type(identity).__name__}."
            )
        if self.identity_rule is IdentityRule.EXACT:
            normalised = identity
        else:
            normalised = "".join(c for c in identity.lower() if c.isalnum())
        if not normalised.strip():
            raise ValueError(
                f"the identity {identity!r} normalises to nothing under this garage's "
                f"{self.identity_rule.value} rule. It is refused rather than stored: "
                "an empty identity would match every other empty one, so a single "
                "malformed enrolment would cover somebody else's car."
            )
        return normalised

    def identities_match(self, written: str, presented: str) -> bool:
        return self.normalise_identity(written) == self.normalise_identity(presented)
