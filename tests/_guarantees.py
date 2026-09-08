"""The canonical registry of what this module guarantees.

**ONE SOURCE, THREE CONSUMERS.** ``conftest.py`` requires every registered id to
have RUN; ``scripts/fail_controls.py`` requires every registered id to have a
control PROVEN TO FAIL and refuses to run if one has none; ``docs/CONTRACT.md``
is generated from this file. A guarantee therefore cannot be quietly dropped from
any of the three, and a number of them cannot go stale in a comment, because
nothing types the number anywhere.

**A GUARANTEE IS A SENTENCE SOMEBODY COULD ACT ON.** Not "the loader validates
input" -- that is a description of a mechanism. "Money is an integer of minor
units at every leaf of an agreement, including leaves this version does not
read" is a claim with a failing case, and the failing case is what the control
plants.
"""

from __future__ import annotations

GUARANTEES: dict[str, str] = {
    "G1": (
        "Money is an integer of minor units with an explicit currency. A float, a "
        "bool or a Decimal is refused at load at EVERY LEAF of an agreement -- "
        "including leaves this version does not read -- not only at the fields the "
        "module happens to consult."
    ),
    "G2": (
        "Proration is by the actual days of the period being prorated, and a "
        "billing period's boundaries are INSTANTS in the garage's own timezone. A "
        "spring-forward period really is an hour shorter and a fall-back period an "
        "hour longer, while both still contain a whole number of calendar days."
    ),
    "G3": (
        "A month-end billing day resolves to the last day the month actually has, "
        "so February is not a special case: the 28th, or the 29th in a leap year."
    ),
    "G4": (
        "The first charge emits the period containing the start date and the "
        "following one as SEPARATE LINES, never one summed figure. The line count "
        "does not depend on the start date: an agreement starting ON the billing "
        "day is charged two full periods, and one starting the day before the next "
        "billing day is charged one day plus a full period, and both are two lines."
    ),
    "G5": (
        "A price change never alters a period already invoiced. Every invoice line "
        "records the agreement VERSION that priced it, and that version is what a "
        "recomputation is checked against."
    ),
    "G6": (
        "The entitlement answer carries NO MONETARY FIELD. Not a fee, not an "
        "amount, not a balance -- the field set is derived from the answer class "
        "itself, so a monetary field added tomorrow is caught the day it exists."
    ),
    "G7": (
        "An unpaid invoice past the garage's grace period returns not-covered, and "
        "NO CONFIGURATION OF THIS MODULE CAN REFUSE AN EXIT. There is no exit call, "
        "no field on the answer that could deny one, and every not-covered reason "
        "carries the sentence saying the stay is priced as an ordinary transient."
    ),
    "G8": (
        "An agreement with no mandate record refuses to be CHARGED, by name, before "
        "any processor is called. Its invoice may still be issued -- a payer who "
        "pays by cheque has no mandate and is still billed."
    ),
    "G9": (
        "No value shaped like a payment card or a bank account survives into the "
        "store or into a log line. The guard scans every column of every row from "
        "the record's own keys, and it holds no example of what it looks for."
    ),
    "G10": (
        "An owner's exception records who made it and when, and a NOTE CAN NEVER "
        "CHANGE AN AMOUNT. The note reaches no arithmetic anywhere in the module; "
        "the amount is a separate, typed field, and a monetary exception without "
        "one is refused by name."
    ),
    "G11": (
        "The vehicle list is INDEPENDENT of the spots bought. Twenty vehicles "
        "against ten spots is a legal agreement, and the module states the "
        "entitlement rather than counting what is inside."
    ),
    "G12": (
        "Every table this module's migrations create carries a tenant column, "
        "ENABLE and FORCE row-level security and an isolation policy -- read from "
        "the database catalogue, never from a list of table names."
    ),
    "G13": (
        "A pause covering days in a period that has already been paid is REFUSED by "
        "name rather than credited or ignored. This module does not decide refunds; "
        "the owner records an exception with an amount."
    ),
}


def guarantee_ids() -> tuple[str, ...]:
    """Sorted numerically, not lexically -- G10 follows G9, not G1."""
    return tuple(sorted(GUARANTEES, key=lambda g: int(g[1:])))


#: Naming an id here lets the suite finish with that guarantee unproven. It is a
#: DECISION somebody writes down, never a default -- CI names nothing, and a
#: guarantee that did not run and pass fails the run.
ALLOW_ENV = "MONTHLY_BILLING_ALLOW_UNRUN"
