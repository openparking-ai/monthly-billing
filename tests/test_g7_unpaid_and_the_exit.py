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

from datetime import date, time, timedelta

import pytest

from fixtures import month_end_garage, simple_agreement, sometime_on
from monthly_billing import entitlement as entitlement_module
from monthly_billing.agreement import AccessHours, Pause, Status
from monthly_billing.entitlement import Answer, is_covered
from monthly_billing.findings import (
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


def _answer_for(code: str):
    """A REAL `is_covered` call that comes back with `code`. One per reason.

    **THIS TABLE IS WHY THE TEST BELOW MEANS ANYTHING.** The version this
    replaced was parametrised over all seven codes and rendered exactly ONE of
    them -- `NO_AGREEMENT`, from `agreements=()` -- so the parameter never
    reached the module. A review dropped the sentence from the `PAUSED` path and
    ruff, 129 tests, every fail-control and the contract check all stayed green.
    A control that plants the fault in one code and renders another is not a
    control.

    Each entry drives the module down a DIFFERENT branch. Missing a code here is
    caught by `test_every_registered_reason_has_a_scenario`, which compares this
    table against the registry rather than against a list.
    """
    garage = month_end_garage(grace_days=5)
    today = date(2026, 4, 20)
    at = sometime_on(today, garage.timezone)
    live = dict(garage=garage, vehicle_identity="CAR000", at=at)

    if code == NOT_COVERED_NO_AGREEMENT:
        return is_covered(garage=garage, agreements=(), vehicle_identity="ANY", at=at)
    if code == NOT_COVERED_NOT_STARTED:
        return is_covered(agreements=(simple_agreement(start_day=date(2026, 6, 1)),), **live)
    if code == NOT_COVERED_CANCELLED:
        return is_covered(
            agreements=(
                simple_agreement(
                    start_day=date(2026, 3, 1),
                    status=Status.CANCELLED,
                    cancelled_effective_day=date(2026, 4, 1),
                ),
            ),
            **live,
        )
    if code == NOT_COVERED_PAUSED:
        return is_covered(
            agreements=(
                simple_agreement(
                    start_day=date(2026, 3, 1),
                    pauses=(Pause(from_day=date(2026, 4, 1), until_day=date(2026, 5, 1)),),
                ),
            ),
            **live,
        )
    if code == NOT_COVERED_BLOCKED_BY_OWNER:
        return is_covered(
            agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
            blocked_by_owner=True,
            **live,
        )
    if code == NOT_COVERED_UNPAID_PAST_GRACE:
        return is_covered(
            agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
            has_unpaid_invoice_since=unpaid_since(garage, 6, today),
            **live,
        )
    if code == NOT_COVERED_OUTSIDE_ACCESS_HOURS:
        early = sometime_on(today, garage.timezone).replace(hour=4, minute=0)
        return is_covered(
            garage=garage,
            agreements=(
                simple_agreement(
                    start_day=date(2026, 3, 1),
                    access_hours=AccessHours(entry_from=time(6, 0), exit_by=time(20, 0)),
                ),
            ),
            vehicle_identity="CAR000",
            at=early,
        )
    raise AssertionError(
        f"{code!r} is in the registry and has no scenario here, so nothing renders it"
    )


@pytest.mark.guarantee("G7")
def test_every_registered_reason_has_a_scenario():
    """The control on the table: it is compared against the REGISTRY, not a list.

    A reason added to `findings.py` with no scenario here fails on the day it is
    added, rather than quietly not being rendered by the test below.
    """
    for code in sorted(NOT_COVERED_REASONS):
        answer = _answer_for(code)
        assert answer.covered is False, f"the scenario for {code} came back covered"
        assert answer.reason_code == code, (
            f"the scenario for {code} actually produced {answer.reason_code} -- so "
            f"{code} is not being rendered by anything"
        )


@pytest.mark.guarantee("G7")
@pytest.mark.parametrize("code", sorted(NOT_COVERED_REASONS))
def test_every_not_covered_reason_says_exit_is_never_refused(code):
    """THE ONE THAT MATTERS. Every reason RENDERED SEPARATELY and asserted.

    `code` reaches the module: `_answer_for` drives a different branch for each
    one, and `test_every_registered_reason_has_a_scenario` proves the branch
    taken is the branch named. So dropping the sentence from any single path
    fails here, which is what the previous version could not do.
    """
    answer = _answer_for(code)
    assert answer.reason_code == code
    assert NOT_COVERED_MEANS in answer.reason, (
        f"the {code} answer reaches a barrier without the sentence saying the stay "
        f"is priced as an ordinary transient: {answer.reason!r}"
    )
    assert "never means refuse exit" in answer.reason


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
