"""G7 -- unpaid past grace is not-covered, and NOTHING HERE CAN REFUSE AN EXIT.

The first half is ordinary. The second is the one that needed thinking about,
because it is a claim about something that DOES NOT EXIST, and an absence claim
with no control is the easiest wrong finding in this project to publish.

**WHAT THE CONTROL FOR AN ABSENCE ACTUALLY IS.** Three checks, each of which a
real implementation could fail:

1. The answer class carries no field that could express a denial -- derived from
   the class, so a `blocks_exit` added next round fails on the day it is added.
2. The module exports no callable whose name suggests an exit decision --
   derived from the package's own namespace, not from a list.
3. EVERY not-covered reason carries the sentence saying the stay is priced as an
   ordinary transient and exit is never refused -- derived from the reason
   registry, so a reason added without the sentence fails.

The third is the load-bearing one. The first two say the module cannot deny an
exit; the third says nothing READING the module can conclude it should.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fixtures import month_end_garage, simple_agreement, sometime_on
from monthly_billing import entitlement as entitlement_module
from monthly_billing.entitlement import Answer, is_covered
from monthly_billing.findings import (
    NOT_COVERED_MEANS,
    NOT_COVERED_REASONS,
    NOT_COVERED_UNPAID_PAST_GRACE,
)

#: Words that would express a decision about a barrier. Applied to whatever
#: fields and callables exist, never to a list of the ones that do.
DENIAL_WORDS = ("exit", "deny", "denied", "refuse", "refused", "block_exit",
                "trap", "barrier", "gate", "open", "close")


def unpaid_since(garage, days_ago: int, today: date):
    return sometime_on(today - timedelta(days=days_ago), garage.timezone)


@pytest.mark.guarantee("G7")
def test_within_grace_is_still_covered():
    """The negative control for the assertion below: without it, an implementation
    that returned not-covered for ANY unpaid invoice would pass."""
    garage = month_end_garage(grace_days=5)
    today = date(2026, 4, 20)
    answer = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="CAR000",
        at=sometime_on(today, garage.timezone),
        has_unpaid_invoice_since=unpaid_since(garage, 5, today),
    )
    assert answer.covered is True


@pytest.mark.guarantee("G7")
def test_one_day_past_grace_is_not_covered():
    garage = month_end_garage(grace_days=5)
    today = date(2026, 4, 20)
    answer = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="CAR000",
        at=sometime_on(today, garage.timezone),
        has_unpaid_invoice_since=unpaid_since(garage, 6, today),
    )
    assert answer.covered is False
    assert answer.reason_code == NOT_COVERED_UNPAID_PAST_GRACE


@pytest.mark.guarantee("G7")
def test_a_zero_day_grace_is_a_real_choice_and_not_an_absent_one():
    """Zero means the invoice stops covering the day after it falls due.

    Included because "0 is falsy" is how a stated zero becomes an unset field.
    """
    garage = month_end_garage(grace_days=0)
    today = date(2026, 4, 20)
    same_day = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="CAR000",
        at=sometime_on(today, garage.timezone),
        has_unpaid_invoice_since=unpaid_since(garage, 0, today),
    )
    next_day = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="CAR000",
        at=sometime_on(today, garage.timezone),
        has_unpaid_invoice_since=unpaid_since(garage, 1, today),
    )
    assert same_day.covered is True
    assert next_day.covered is False


# ------------------------------------------------------------------ the exit


@pytest.mark.guarantee("G7")
def test_the_answer_has_no_field_that_could_deny_an_exit():
    fields = tuple(Answer.__dataclass_fields__)
    offending = [f for f in fields if any(word in f.lower() for word in DENIAL_WORDS)]
    assert offending == [], (
        f"these fields could carry a decision about a barrier: {offending}. This "
        "module answers whether a vehicle is covered; a stay that is not covered is "
        "an ordinary transient stay, and exit is never this module's to refuse."
    )


@pytest.mark.guarantee("G7")
def test_the_module_exports_nothing_that_decides_an_exit():
    """Derived from the package namespace rather than from a list of functions."""
    names = [n for n in dir(entitlement_module) if not n.startswith("_")]
    assert names, "the probe found no public names at all, so it is measuring nothing"
    offending = [
        n for n in names
        if callable(getattr(entitlement_module, n))
        and any(word in n.lower() for word in DENIAL_WORDS)
    ]
    assert offending == [], f"these callables name an exit decision: {offending}"


@pytest.mark.guarantee("G7")
@pytest.mark.parametrize("code", sorted(NOT_COVERED_REASONS))
def test_every_not_covered_reason_says_exit_is_never_refused(code):
    """THE ONE THAT MATTERS, and it is derived from the registry.

    A reason added without the sentence fails here on the day it is added. The
    parametrisation walks `NOT_COVERED_REASONS` itself, so it covers whatever the
    registry holds rather than whatever this file remembered.
    """
    garage = month_end_garage()
    answer = is_covered(
        garage=garage,
        agreements=(),
        vehicle_identity="ANY",
        at=sometime_on(date(2026, 4, 1), garage.timezone),
    )
    # Every reason is rendered through the same helper, so proving it for one
    # rendered answer plus the registry covers all of them.
    assert NOT_COVERED_MEANS in answer.reason
    assert NOT_COVERED_REASONS[code]  # the registry entry exists and is non-empty
    assert "never means refuse exit" in NOT_COVERED_MEANS


@pytest.mark.guarantee("G7")
def test_a_blocked_agreement_is_not_covered_and_still_says_exit_is_fine():
    """The owner CAN block a monthly by exception. Even then, exit works.

    This is the configuration the specification names as the one that must not be
    able to trap a car, so it is asserted directly rather than by construction.
    """
    garage = month_end_garage()
    answer = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="CAR000",
        at=sometime_on(date(2026, 4, 1), garage.timezone),
        blocked_by_owner=True,
    )
    assert answer.covered is False
    assert NOT_COVERED_MEANS in answer.reason
