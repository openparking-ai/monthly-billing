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
        "An agreement belongs to one garage and was asked about another. One "
        "garage per account is the stated shape; answering across garages would "
        "require rules for an entitlement this agreement does not describe."
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
        "This agreement has an unpaid invoice past the garage's grace period."
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
NOT_COVERED_MEANS: str = (
    "Not covered means this stay is an ordinary transient stay and is priced "
    "like any other. It does not mean refuse entry, and it never means refuse "
    "exit."
)
