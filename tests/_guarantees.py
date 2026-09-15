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
        "Every table this module's migrations create carries ENABLE and FORCE "
        "row-level security and an isolation policy, and every table but `tenants` "
        "carries a tenant column -- `tenants` IS the tenant and is isolated by "
        "`id = current_tenant_id()`. Read from the database catalogue, never from a "
        "list of table names."
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
        "The charge log is the truth for retries, in the order it was RECORDED: the "
        "retry state is rebuilt from the persisted OUTCOME rows walked by the sequence "
        "the database assigned under the invoice lock -- never by the instant a caller "
        "typed -- and an outcome counts toward the payment method that was current "
        "when its RESERVATION was made. Three recorded non-success outcomes refuse the "
        "fourth by name; a recorded method change allows it -- and is allowed while an "
        "attempt is pending, because it moves no money; a success counts toward nothing "
        "and resets nothing; an unknown counts as nothing; and a restart forgets nothing "
        "because nothing lives outside the rows."
    ),
    "G23": (
        "A charge is for the BALANCE -- the invoice's total minus its unreversed "
        "payments, read under the invoice lock through the same derivation that "
        "decides paid -- never the total; and a balance of nothing is refused by name "
        "BEFORE the processor is called and before any row is written, so a paid "
        "invoice is never charged again, a part-paid one is charged its remainder, "
        "and a fully waived one is not charged at all. A payment that lands between "
        "the reservation and the outcome does not cancel the card payment the "
        "processor made: it is recorded for the amount reserved and the invoice reads "
        "OVERPAID, never silently."
    ),
    "G24": (
        "Every call to the processor is PRECEDED by its row -- the reservation for the "
        "first ask, an `ask` row for every re-ask, each written and committed before the "
        "processor is asked, so a call the processor received always has a row on our "
        "side -- and FOLLOWED by a row recorded as it came: what the processor said as an "
        "`outcome` row -- or, when the operator resolved the attempt while that ask was "
        "at the processor, as a `late` row beside the operator's resolution, carrying what "
        "the processor said, never dropped -- and what the module did not receive -- a "
        "raise, before or after the request left, or a return the instrument guard "
        "refused -- as an `unknown` row saying what the module saw, never as an outcome. "
        "A row is never lost to its own message."
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
        "error for a state the module can name. Two reversals of one payment AT ONCE "
        "are one recorded and one refused by name: the check runs under the invoice "
        "lock, and the one-reversal-per-payment constraint is caught by its name as "
        "the backstop."
    ),
    "G30": (
        "Every event that changes an invoice's money -- a payment, a reversal, an "
        "owner's adjustment, both halves of a charge, the resolution of a pending "
        "attempt -- runs inside a transaction that first takes the invoice's row "
        "lock, so two events on one invoice at once are serialised and the second "
        "derives paid_at from the first's COMMITTED rows. Which events take the lock "
        "is read from the source by the contract, never typed -- and the control that "
        "proves the serialisation removes the lock itself, not one caller's."
    ),
    "G31": (
        "A charge is a PERSISTED RESERVATION, then the processor, then an OUTCOME "
        "with its payment: the attempt row is committed BEFORE the processor is "
        "called, and the outcome row and the card payment land in ONE transaction. "
        "What the module does not know is never written as an outcome: an answer that "
        "did not arrive -- a raise, or a return the instrument guard refused -- is an "
        "`unknown` row and the attempt stays PENDING. A pending attempt is never "
        "charged past: while its request may be in flight the next charge is refused "
        "by name, naming the attempt id, and `pending-attempts` lists it; when the "
        "module has said it does not know, the next charge asks the processor again "
        "under the SAME idempotency key for the amount RESERVED, with no cap on "
        "re-asks -- one key is one charge at most. An operator records what the "
        "processor said once (`resolve-attempt`): a second resolution is refused by name, "
        "from the check under the lock and from the one-outcome-per-attempt index "
        "caught by its name. The processor's own answer to an ask that was at the "
        "processor when the operator resolved is not a second resolution: it is a `late` "
        "row, and the processor's word on money outranks the operator's typed one -- a "
        "late SUCCESS the operator did not record writes the card payment for the amount "
        "RESERVED in the same transaction, so the invoice is paid and no fresh key is "
        "minted; a late SUCCESS on a recorded success writes no second payment; a late "
        "decline or error is the row only, and the operator's SUCCESS and its payment "
        "stand for the operator's reversal."
    ),
    "G32": (
        "ONE CAR, ONE AGREEMENT PER GARAGE. A vehicle identity is registered to one "
        "agreement at a garage; a second agreement listing it is refused by name, "
        "naming the holder and, if the holder is cancelled, the day it frees the "
        "vehicle -- on which day the registration passes to the new agreement. A "
        "REFUSAL WRITES NOTHING: every listed identity is checked before the release "
        "and before any row changes, so the caller's transaction is as it was. The "
        "database's UNIQUE is the backstop for a raw insert and for two registrations "
        "racing. The coverage call picks nothing: handed two agreement identities for "
        "one vehicle, it refuses by name."
    ),
    "G33": (
        "An invoice total never goes below zero: an owner's adjustment that would "
        "take it there is refused by name, computed under the invoice lock from the "
        "committed lines; exactly zero is a fully waived invoice and is allowed. "
        "There is no database backstop for a floor across rows -- the lock is the "
        "whole guard, and the contract says so."
    ),
    "G34": (
        "A block the owner records against an INVOICE is lifted by an unblock the "
        "owner records, never by a payment: invoice-attached blocks and unblocks are "
        "read whatever the invoice's paid state, and a grace extension on an invoice "
        "is read only while that invoice is unpaid."
    ),
    "G35": (
        "Overpayment is a RECORDED FACT and never acted on: the paid state carries "
        "by how much the unreversed payments exceed the total, the command line prints "
        "it, and no cap is put on what an operator records -- a cheque is what it is; "
        "a refund is the owner's exception and the money's return is collection's."
    ),
    "G36": (
        "The store-backed coverage call answers the AGREEMENT axes at the instant "
        "asked and the PAYMENT state as of now, and the command line SAYS SO whenever "
        "the instant asked is in the past -- one line, present for a past instant "
        "and absent otherwise."
    ),
    "G37": (
        "Every garage reference is half of a COMPOSITE TENANT KEY: every column named "
        "garage_id in the schema carries a foreign key (tenant_id, garage_id) at "
        "garages (tenant_id, id), so a row cannot name another tenant's garage even by "
        "a raw INSERT that the policy would let past -- a foreign-key check runs past "
        "row-level security and the key does not. Read from the catalogue, never from "
        "a list of table names."
    ),
    "G38": (
        "No exception leaves the invoice lock held: every money event takes the lock "
        "through ONE context manager that rolls the transaction back on any raise -- a "
        "refusal, the instrument guard, a driver error -- so the caller's connection is "
        "idle and unlocked when the exception reaches it, and a money event on another "
        "connection is recorded at once. The lock is taken in exactly one place, read "
        "from the source."
    ),
    "G39": (
        "An agreement is billed at ONE HOME GARAGE and COVERS the garages the owner "
        "lists, the home among them -- stated by listing, never by an 'everywhere' flag "
        "or a default, and a document that omits the set is refused by name. The "
        "coverage door is MEMBERSHIP of that set; the asking garage still compares the "
        "plate under its own rule. The unpaid-invoice read, the owner's exceptions and "
        "the grace period -- days AND clock -- follow the agreement's HOME, never the "
        "asking garage, and two agreements of one payer homed at two garages are judged "
        "independently. Registrations fan out one row per covered garage, each under "
        "that garage's own identity rule, all or none: a collision at ANY covered "
        "garage refuses the whole registration by name and writes nothing. The "
        "entitlement is across the covered set, not per garage. THE HOME NEVER MOVES: "
        "a version billed at a different garage from the versions already stored is "
        "refused by name and writes nothing, because a move has no stated answer for "
        "the month already invoiced, for an unpaid invoice or a block at the old home, "
        "or for whose billing day governs next -- so the home-keyed reads are correct "
        "for every state the module can store. There is no cross-row database backstop "
        "for this; a row written past the module with another home is read at that "
        "home only."
    ),
    "G40": (
        "MONEY IS NOT MULTI-GARAGE. The billing day, the currency, the timezone of the "
        "period boundaries, proration, the invoice, the run and the charge are keyed on "
        "the agreement's HOME garage only: a run at a garage the agreement merely covers "
        "issues nothing for it, the first charge and the period invoice refuse another "
        "garage by name whether or not the agreement covers it, and the store refuses "
        "to file an agreement under a garage that is not its home. The billing run reads "
        "the agreements BILLED at a garage through a loader that never widens to the "
        "covered set and that chooses each agreement's LATEST version before it asks "
        "where that version is homed; the coverage call reads a second loader. A "
        "version that would MOVE the home is refused by name and never stored, so the "
        "ordering matters only for a row written past the module -- and for that row "
        "the run bills the newest version's home only. The charge reads the agreements "
        "the invoice's own lines name, each at its latest version, and the garage does "
        "not enter that read: an invoice whose agreement's newest row was written past "
        "the module at another home is still charged, and an invoice naming no "
        "loadable agreement is refused by name."
    ),
    "G41": (
        "Migration 0004 gives every EXISTING agreement version a covered set of exactly "
        "its home garage, read from agreements.garage_id, and asserts the placed rows "
        "against the pre-migration count rather than a literal. A version whose home "
        "cannot be placed fails the migration BY NAME -- external id and version -- "
        "before a row is written, and the whole migration rolls back."
    ),
    "G42": (
        "ONE AGREEMENT, ONE REGISTRAR. An agreement STATES who writes its "
        "registrations -- this module, the default, or an OUTSIDE registrar -- on "
        "its document and on its row, never inferred. For an outside registrar's "
        "agreement the module writes NO registration of its own: storing a version "
        "registers nothing and releases nothing, the document lists no vehicles "
        "(a list it does not own is refused by name) and loads back that way, and "
        "the outside registrar registers and releases ONE vehicle identity at a "
        "time through the registration door -- which fans out over the covered set "
        "under each garage's own rule, refuses at every covered garage before it "
        "writes anywhere, takes over a cancelled holder's row on its day, and "
        "answers with the identity as stored per garage. Both halves of the door "
        "refuse by name an agreement whose registrations this module writes. The "
        "door writes the garage's holder claim (vehicle_registrations), never a "
        "version's own list (agreement_vehicles)."
    ),
    "G43": (
        "Migration 0005 gives every EXISTING agreement version the registrar it "
        "has today -- this module -- and asserts the stated rows against the "
        "pre-migration count rather than a literal: a default that left any "
        "version stating otherwise fails the migration and the whole file rolls "
        "back. No version is left unstated."
    ),
}


def guarantee_ids() -> tuple[str, ...]:
    """Sorted numerically, not lexically -- G10 follows G9, not G1."""
    return tuple(sorted(GUARANTEES, key=lambda g: int(g[1:])))


#: Naming an id here lets the suite finish with that guarantee unproven. It is a
#: DECISION somebody writes down, never a default -- CI names nothing, and a
#: guarantee that did not run and pass fails the run.
ALLOW_ENV = "MONTHLY_BILLING_ALLOW_UNRUN"
