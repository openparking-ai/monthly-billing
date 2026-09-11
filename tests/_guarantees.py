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
        "store or into a log line, and NO TRACKED FILE IN THE REPOSITORY CONTAINS "
        "ONE. The guard scans every column of every row from the record's own "
        "keys; the repository sweep derives its file set from git and exempts "
        "nothing, tests and fixtures included."
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
    "G14": (
        "Every fixture carries its own control asserting it holds the property it "
        "claims to represent, and those controls run as tests. A DST fixture whose "
        "zone does not shift, or two identity rules that agree, read as coverage "
        "while sampling one point on the axis that decides."
    ),
    "G15": (
        "docs/CONTRACT.md is GENERATED from this registry and from the enums and "
        "refusal codes that implement it, and its prose is derived rather than "
        "fixed: a value that contradicts a published sentence changes the sentence, "
        "because generation is not verification."
    ),
    "G16": (
        "Every test module contributes at least one registered guarantee, derived "
        "from the filesystem and read from the AST -- so a module cannot be added, "
        "skipped or deleted without a guard noticing. A module that PLANTS a defect "
        "may not be excused at all."
    ),
    "G13": (
        "A pause covering days in a period that has already been paid is REFUSED by "
        "name rather than credited or ignored. This module does not decide refunds; "
        "the owner records an exception with an amount."
    ),
    "G17": (
        "A billing run is idempotent BY CONSTRAINT: the database holds one invoice "
        "per tenant, garage, payer and period, so a second run of the same period "
        "issues nothing, re-prices nothing, and says so per payer rather than "
        "erroring or duplicating."
    ),
    "G18": (
        "A period is owned by exactly one of the first charge and the billing run, "
        "decided by PERIOD and never by line count: the run does not re-issue the "
        "two periods the first charge covered, and it does issue every period after "
        "them."
    ),
    "G19": (
        "An invoice is paid only by unreversed payments summing to its total, and "
        "paid_at is DERIVED after each of the three events that can change that -- "
        "a payment, a reversal, an owner adjustment that moves the total. A "
        "reversal reopens the invoice from its ORIGINAL due date, never from the "
        "reversal."
    ),
    "G20": (
        "Payments, reversals and charge attempts are append-only BY GRANT: the "
        "application role has no UPDATE and no DELETE on them, so money history is "
        "never edited, only added to -- and the store's instrument guard scans every "
        "row of theirs on the way in, as it does everywhere. The invoice's own lines "
        "and the owner's recorded decisions are history the same way -- no UPDATE, no "
        "DELETE -- and an invoice cannot be deleted; it keeps UPDATE for the one "
        "derived column, paid_at."
    ),
    "G21": (
        "The store-backed entitlement call returns the pure function's answer and "
        "nothing else -- the same field set, no money -- with 'unpaid since' derived "
        "as the earliest unpaid due date, and it HONOURS the owner's grace extensions "
        "and blocks rather than reading the garage's base figure alone."
    ),
    "G22": (
        "The charge log is the truth for retries: the retry state is rebuilt from the "
        "persisted attempts since the last persisted payment-method change, so three "
        "recorded non-success attempts refuse the fourth by name, a recorded method "
        "change allows it, and a restart forgets nothing."
    ),
    "G23": (
        "A charge is for the BALANCE -- the invoice's total minus its unreversed "
        "payments, read through the same derivation that decides paid -- never the "
        "total; and a balance of nothing is refused by name BEFORE the processor is "
        "called and before any attempt is recorded, so a paid invoice is never charged "
        "again, a part-paid one is charged its remainder, and a fully waived one is not "
        "charged at all."
    ),
    "G24": (
        "Every call to the processor leaves an attempt row, whatever the processor "
        "did: a result is recorded as it came, a raise is recorded as an error naming "
        "the exception, and a result the instrument guard refused inside the "
        "processor's own return is recorded as an error whose detail says the outcome "
        "is unknown -- a reported result is never discarded silently, and a row is "
        "never lost to its own message."
    ),
    "G25": (
        "An owner's exception is read by the agreement's IDENTITY across every version "
        "of it: a block or a grace extension recorded against one version still "
        "reaches the barrier after a price change has stored the next."
    ),
    "G26": (
        "An owner exception's amount is a POSITIVE number of minor units, refused by "
        "name by the module and by CHECK in the database, so no kind can be turned "
        "into its opposite by a sign -- and a refund records the decision and its "
        "amount and moves NO total: a paid period is paid."
    ),
    "G27": (
        "ALREADY_ISSUED means exactly that a row for this garage, payer and period "
        "exists: a unique violation on any other constraint is reported as its own "
        "outcome, naming the constraint, and makes the run exit non-zero -- the run "
        "never says a period was invoiced when it was not."
    ),
    "G28": (
        "Every money-history row points into its OWN tenant by composite key -- a "
        "payment at its invoice, a reversal at its payment, an attempt at its invoice "
        "-- so a row cannot reference another tenant's even by a raw INSERT that the "
        "policy would let past, because a foreign-key check runs past row-level "
        "security and the key does not."
    ),
    "G29": (
        "A payment is reversed at most once, for a reason that fits its method, and "
        "card fields sit only on a card payment -- each refused BY NAME by the module "
        "before the database has to, so an operator is never handed a raw driver "
        "error for a state the module can name."
    ),
}


def guarantee_ids() -> tuple[str, ...]:
    """Sorted numerically, not lexically -- G10 follows G9, not G1."""
    return tuple(sorted(GUARANTEES, key=lambda g: int(g[1:])))


#: Naming an id here lets the suite finish with that guarantee unproven. It is a
#: DECISION somebody writes down, never a default -- CI names nothing, and a
#: guarantee that did not run and pass fails the run.
ALLOW_ENV = "MONTHLY_BILLING_ALLOW_UNRUN"
