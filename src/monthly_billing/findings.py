"""Every refusal this module can make, as a code with a plain-English sentence.

**The registry is the single source.** ``docs/CONTRACT.md`` is generated from it,
the refusal carries the code, and a test derives its list from here rather than
walking a hand-written one -- so a code added without a sentence, or a sentence
published for a code nothing raises, each fail. A check that walks a hard-coded
list cannot notice anything added to what it is supposed to cover.

**A refusal is not an error.** The module refuses when the agreement does not
determine the answer, and that is a first-class result: the owner is told which
field is missing and decides. Inventing a figure instead is the one behaviour
this module may never have, because a figure somebody pays looks the same whether
it was determined or guessed.

Two separate vocabularies live here and they are deliberately not one:

* **REFUSALS** -- the module cannot answer, and names what is missing.
* **NOT_COVERED_REASONS** -- the module CAN answer, and the answer is no. These
  reach a parking lane in plain English and carry no money, ever.
"""

from __future__ import annotations


class Refused(Exception):
    """The module will not answer, and says which field would let it.

    Carries the code so a caller can branch on it without parsing prose, and the
    sentence so a person reading a log does not have to look the code up.
    """

    def __init__(self, code: str, detail: str) -> None:
        if code not in REFUSALS:
            raise KeyError(
                f"{code!r} is not a registered refusal. Add it to findings.REFUSALS "
                "with the sentence an operator should read; the contract document "
                "and its test are generated from that registry, so a code invented "
                "at the raise site would be published nowhere and tested by nothing."
            )
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {REFUSALS[code]} — {detail}")


# --------------------------------------------------------------------------
# Refusals. The module cannot answer.
# --------------------------------------------------------------------------

REFUSAL_NO_MANDATE = "REFUSAL_NO_MANDATE"
REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD = "REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD"
REFUSAL_NO_BILLING_DAY = "REFUSAL_NO_BILLING_DAY"
REFUSAL_NO_PAYMENT_GRACE = "REFUSAL_NO_PAYMENT_GRACE"
REFUSAL_NO_IDENTITY_RULE = "REFUSAL_NO_IDENTITY_RULE"
REFUSAL_CURRENCY_MISMATCH = "REFUSAL_CURRENCY_MISMATCH"
REFUSAL_GARAGE_MISMATCH = "REFUSAL_GARAGE_MISMATCH"
REFUSAL_EXCEPTION_HAS_NO_AMOUNT = "REFUSAL_EXCEPTION_HAS_NO_AMOUNT"
REFUSAL_RETRIES_EXHAUSTED = "REFUSAL_RETRIES_EXHAUSTED"
REFUSAL_NOTHING_OWED = "REFUSAL_NOTHING_OWED"
REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE = "REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE"
REFUSAL_ALREADY_REVERSED = "REFUSAL_ALREADY_REVERSED"
REFUSAL_REVERSAL_REASON_MISMATCH = "REFUSAL_REVERSAL_REASON_MISMATCH"
REFUSAL_CARD_FIELDS_WITHOUT_A_CARD = "REFUSAL_CARD_FIELDS_WITHOUT_A_CARD"
REFUSAL_ATTEMPT_UNRESOLVED = "REFUSAL_ATTEMPT_UNRESOLVED"
REFUSAL_ATTEMPT_ALREADY_RESOLVED = "REFUSAL_ATTEMPT_ALREADY_RESOLVED"
REFUSAL_VEHICLE_ALREADY_REGISTERED = "REFUSAL_VEHICLE_ALREADY_REGISTERED"
REFUSAL_VEHICLE_ON_TWO_AGREEMENTS = "REFUSAL_VEHICLE_ON_TWO_AGREEMENTS"
REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL = "REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL"
REFUSAL_HOME_GARAGE_NOT_GIVEN = "REFUSAL_HOME_GARAGE_NOT_GIVEN"
REFUSAL_INVOICE_NAMES_NO_AGREEMENT = "REFUSAL_INVOICE_NAMES_NO_AGREEMENT"
REFUSAL_AGREEMENT_HOME_MOVED = "REFUSAL_AGREEMENT_HOME_MOVED"
REFUSAL_REGISTRAR_IS_THIS_MODULE = "REFUSAL_REGISTRAR_IS_THIS_MODULE"
REFUSAL_REGISTRAR_IS_OUTSIDE = "REFUSAL_REGISTRAR_IS_OUTSIDE"
REFUSAL_REGISTRAR_CHANGED = "REFUSAL_REGISTRAR_CHANGED"
REFUSAL_REGISTRATIONS_NOT_GIVEN = "REFUSAL_REGISTRATIONS_NOT_GIVEN"
REFUSAL_VEHICLE_NOT_REGISTERED = "REFUSAL_VEHICLE_NOT_REGISTERED"

REFUSALS: dict[str, str] = {
    REFUSAL_NO_MANDATE: (
        "This agreement has no mandate record, so nothing may be charged against "
        "it. The mandate records who agreed, when, and to what: the recurring "
        "charge, its timing and frequency, how the amount is determined, and how "
        "it is cancelled. An invoice may still be issued and sent -- it is the "
        "CHARGE that is refused, not the billing."
    ),
    REFUSAL_PAUSE_OVERLAPS_A_PAID_PERIOD: (
        "The pause covers days in a period that has already been paid. A paid "
        "period is paid, and this module does not decide refunds or credits. The "
        "owner records an exception with an amount, or moves the pause."
    ),
    REFUSAL_NO_BILLING_DAY: (
        "The garage has not stated its billing day. It is one of the offered "
        "options and there is no default, because the answer differs by garage "
        "and a guessed billing day charges somebody on the wrong date."
    ),
    REFUSAL_NO_PAYMENT_GRACE: (
        "The garage has not stated how many days past an unpaid invoice an "
        "agreement stays covered. There is no default: a guessed grace period "
        "either strands a paying customer at a barrier or covers an unpaid one "
        "indefinitely."
    ),
    REFUSAL_NO_IDENTITY_RULE: (
        "The garage has not stated how a vehicle identity is compared. There is "
        "no default, because the answer decides whether a monthly parker is "
        "recognised at all -- and an unrecognised monthly parker is charged as a "
        "transient."
    ),
    REFUSAL_CURRENCY_MISMATCH: (
        "An invoice covers one garage and one currency. Two agreements in "
        "different currencies do not sum, and this module will not convert them: "
        "a conversion needs a rate, a date and a spread, none of which an "
        "agreement carries."
    ),
    REFUSAL_GARAGE_MISMATCH: (
        "An agreement is BILLED at one home garage, and this money question named "
        "another. The billing day, the currency, the timezone, the grace period and "
        "the invoice are the home garage's; an agreement may be good at other "
        "garages the owner lists, but pricing it against one of them would pick "
        "that garage's options over the home's with no rule saying which governs."
    ),
    REFUSAL_EXCEPTION_HAS_NO_AMOUNT: (
        "This exception changes money and carries no amount. A note explains; an "
        "amount changes what somebody pays. They are different fields and the "
        "amount is typed, so that no free-text note can ever price anything."
    ),
    REFUSAL_RETRIES_EXHAUSTED: (
        "This invoice has reached its maximum charge attempts. The count resets "
        "when the caller reports that the payer changed payment method -- this "
        "module holds no payment method and cannot observe that for itself."
    ),
    REFUSAL_NOTHING_OWED: (
        "Nothing is owed on this invoice: its unreversed payments already reach "
        "its total, so there is no balance to charge. The processor is not called "
        "and no attempt is recorded, because nothing was attempted. A charge is "
        "always for the balance, never for the total."
    ),
    REFUSAL_EXCEPTION_AMOUNT_NOT_POSITIVE: (
        "This exception changes money and its amount is not a positive number of "
        "minor units. A waived fee, a credit and a refund each name how much; the "
        "direction is the kind's, never the sign's, so a negative amount is refused "
        "rather than read as the opposite kind."
    ),
    REFUSAL_ALREADY_REVERSED: (
        "This payment already has a reversal recorded. A payment that did not "
        "stand cannot un-stand twice; the first reversal is the record."
    ),
    REFUSAL_REVERSAL_REASON_MISMATCH: (
        "The reversal's reason does not fit the payment's method: a cheque bounces, "
        "an ACH debit is returned, a card payment is charged back or reversed by "
        "the processor. A reason from the wrong column is a record somebody "
        "assembled wrong, and it is refused rather than stored."
    ),
    REFUSAL_CARD_FIELDS_WITHOUT_A_CARD: (
        "A card brand or last four digits were given on a payment that is not a "
        "card payment. Those fields describe the card a processor charged, and a "
        "cheque or an ACH debit has none."
    ),
    REFUSAL_ATTEMPT_UNRESOLVED: (
        "A charge attempt on this invoice is still PENDING and its request may be "
        "IN FLIGHT: its reservation was written and the processor was asked, and "
        "neither an outcome nor an answer has been recorded -- the worker died with "
        "the request, or is still waiting. Money may have moved. Nothing is charged "
        "past a pending attempt, and nothing asks again while a request may be in "
        "flight: the refusal names the attempt id, pending-attempts lists it, and an "
        "operator records what the processor says (resolve-attempt). A pending "
        "attempt whose last word is UNKNOWN is different: the next charge asks the "
        "processor again under the same key, and is not refused."
    ),
    REFUSAL_ATTEMPT_ALREADY_RESOLVED: (
        "This attempt already has an outcome recorded. What the processor said is "
        "written once; a second answer for the same attempt is a record somebody "
        "assembled wrong, and it is refused rather than stored beside the first."
    ),
    REFUSAL_VEHICLE_ALREADY_REGISTERED: (
        "This vehicle identity is registered to ANOTHER agreement at this garage. "
        "One car, one agreement per garage: the registration names the other "
        "agreement and, if that agreement is cancelled, the day it frees the "
        "vehicle. Until then the vehicle is not added here."
    ),
    REFUSAL_VEHICLE_ON_TWO_AGREEMENTS: (
        "The agreements handed to this call list the same vehicle under two "
        "different agreement identities. The store can no longer produce that "
        "state; a library caller handed it in. The module refuses rather than "
        "picking one, because the two may disagree about coverage and a guess at "
        "a barrier is a wrong answer given confidently."
    ),
    REFUSAL_HOME_GARAGE_NOT_GIVEN: (
        "The coverage question was asked at a garage that is not the agreement's "
        "home, with an unpaid invoice to weigh, and the home garage was not "
        "supplied. Grace is a property of the invoice -- how long after it fell due "
        "the account stays covered -- and the invoice lives at the home garage, so "
        "the home's grace and clock decide it at every door. Reading the asking "
        "garage's grace instead would make one unpaid invoice expire on two "
        "different days depending on which door the car is at; refused rather "
        "than defaulted."
    ),
    REFUSAL_INVOICE_NAMES_NO_AGREEMENT: (
        "This invoice's lines name no agreement the store can load, so there is no "
        "mandate to charge it against. The charge gate reads the agreements the "
        "lines name, each at its latest version, wherever it is homed now; an "
        "invoice that reaches this state was written past the module, and a charge "
        "with no gate is refused rather than attempted."
    ),
    REFUSAL_AGREEMENT_HOME_MOVED: (
        "This version is billed at a different home garage from the versions the "
        "store already holds for the same agreement, and MOVING AN AGREEMENT'S HOME "
        "IS AN OPERATION NOBODY DESIGNED. Nothing states what a move does to the month "
        "already invoiced at the old home, to an unpaid invoice or an owner's block "
        "sitting there, or to which garage's billing day and currency govern next -- "
        "so the module refuses rather than invents. An agreement keeps the home it "
        "was first stored with for its whole life; a different home is a different "
        "agreement. There is no cross-row database backstop for this: a row written "
        "past the module with another home is read at that home only."
    ),
    REFUSAL_REGISTRAR_IS_THIS_MODULE: (
        "This agreement's registrations are written by this module, from the "
        "vehicle list on its own document, and the registration door is for an "
        "agreement whose registrar is OUTSIDE. One agreement, one registrar: a "
        "second writer of the same rows would race the first, so the door refuses "
        "by name instead. To move a car onto this agreement, store a version that "
        "lists it; to hand the register to an outside registrar, store a version "
        "that says so."
    ),
    REFUSAL_REGISTRAR_IS_OUTSIDE: (
        "This agreement's registrations are written by an OUTSIDE registrar, one "
        "vehicle at a time through the registration door, and the version path "
        "writes registrations only for an agreement whose registrar is this "
        "module. One agreement, one registrar: writing this agreement's rows from a "
        "version's list would take out every car the outside registrar put on, so "
        "the writer refuses by name instead. Storing a version of such an agreement "
        "is ordinary and writes none of them; only a direct call to the writer "
        "lands here."
    ),
    REFUSAL_REGISTRAR_CHANGED: (
        "This version names a different registrar from the versions the store "
        "already holds for the same agreement. Handing the register from this "
        "module to an outside registrar, or back, is an operation nobody designed: "
        "nothing says what becomes of the registrations already written by the "
        "other one. It is refused by name rather than stored -- as a version that "
        "would move the home is -- and a different registrar is a different "
        "agreement."
    ),
    REFUSAL_REGISTRATIONS_NOT_GIVEN: (
        "The coverage question was asked about an agreement whose registrar is "
        "OUTSIDE, and the store's registrations were not supplied. Such an "
        "agreement lists no vehicles on its document by rule; its register is the "
        "registration rows the outside registrar wrote through the door, and a "
        "call without them cannot tell a registered car from one nobody "
        "registered. Refused by name rather than answered 'no agreement' for "
        "every car; the store-backed call supplies them."
    ),
    REFUSAL_VEHICLE_NOT_REGISTERED: (
        "This vehicle identity is registered to this agreement at no garage the "
        "agreement covers, so there is nothing to release. For a register kept by "
        "one writer a release that finds no row means the two registers have "
        "diverged, which is the one thing the single-writer rule exists to "
        "surface -- so it is refused by name rather than reported as done."
    ),
    REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL: (
        "This adjustment would take the invoice total below zero. A waived fee or "
        "a credit may reduce what is owed to exactly nothing -- a fully waived "
        "invoice -- and no further: money owed BY the garage is not a negative "
        "invoice, it is a refund the owner records as its own decision. Computed "
        "under the invoice lock from the committed lines; there is no database "
        "backstop for a floor across rows, so the lock is the whole guard."
    ),
}


# --------------------------------------------------------------------------
# Not-covered reasons. The module answers, and the answer is no.
#
# THESE CARRY NO MONEY. A stay that is not covered is an ordinary transient stay
# and something else prices it. See entitlement.py.
# --------------------------------------------------------------------------

NOT_COVERED_NO_AGREEMENT = "NO_AGREEMENT"
NOT_COVERED_NOT_STARTED = "NOT_STARTED"
NOT_COVERED_PAUSED = "PAUSED"
NOT_COVERED_OUTSIDE_ACCESS_HOURS = "OUTSIDE_ACCESS_HOURS"
NOT_COVERED_UNPAID_PAST_GRACE = "UNPAID_PAST_GRACE"
NOT_COVERED_CANCELLED = "CANCELLED"
NOT_COVERED_BLOCKED_BY_OWNER = "BLOCKED_BY_OWNER"

NOT_COVERED_REASONS: dict[str, str] = {
    NOT_COVERED_NO_AGREEMENT: (
        "No agreement at this garage lists this vehicle."
    ),
    NOT_COVERED_NOT_STARTED: (
        "This vehicle's agreement has not started yet."
    ),
    NOT_COVERED_PAUSED: (
        "This agreement is paused. Nothing is billed during a pause and nothing "
        "is covered by it."
    ),
    NOT_COVERED_OUTSIDE_ACCESS_HOURS: (
        "This agreement buys entry and exit within stated hours, and this is "
        "outside them."
    ),
    NOT_COVERED_UNPAID_PAST_GRACE: (
        "This agreement has an unpaid invoice past its home garage's grace period."
    ),
    NOT_COVERED_CANCELLED: (
        "This agreement was cancelled."
    ),
    NOT_COVERED_BLOCKED_BY_OWNER: (
        "The garage owner has blocked this agreement by exception."
    ),
}

#: THE SENTENCE THAT TRAVELS WITH EVERY ONE OF THEM.
#:
#: It is stored once, here, and appended by the entitlement answer rather than
#: written into seven strings that would drift. A parking lane reads this and it
#: is the only thing it needs to know about money, which is that there isn't any
#: in this answer.
#: The unpaid state is the PAYER'S at a home garage, and it always was; after
#: 0004 it shows at every garage the payer's agreements homed there cover.
UNPAID_IS_THE_PAYERS: str = (
    "The unpaid invoice is the PAYER'S: an unpaid invoice of the payer at a home "
    "garage makes every agreement of that payer homed there not-covered once its "
    "grace has run, at every garage each of them covers. An agreement of the same "
    "payer homed at another garage is untouched by it -- each home's invoices judge "
    "only the agreements billed there."
)

NOT_COVERED_MEANS: str = (
    "Not covered means this stay is an ordinary transient stay and is priced "
    "like any other. It does not mean refuse entry, and it never means refuse "
    "exit."
)
