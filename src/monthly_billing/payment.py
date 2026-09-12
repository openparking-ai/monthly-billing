"""The payment interface, stubbed — and the two fields that exist before it does.

M1 defines the interface and ships a stub. **No processor, no card, no ACH, no
live key.** Collection is its own module and its own round.

⛔ **NO CARD NUMBER, NO BANK ACCOUNT NUMBER, AND NO TOKEN THAT COULD SUBSTITUTE
FOR ONE, IN THIS MODULE'S DATABASE OR ITS LOGS — EVER.**

That is the PCI decision and it is the most important sentence in this module. It
is built as a GUARANTEE WITH A FAIL CONTROL, not as a promise in a docstring: a
card-shaped value planted into the payment path must make the guard go red. See
``sensitive.py`` for the detector and
``tests/test_g9_no_instrument_survives.py`` for the control.

**WHY A MANDATE IS REQUIRED TO CHARGE AND NOT TO INVOICE.** The specification says
an agreement with no mandate record "cannot be billed, and refuses by name". Read
as blocking the INVOICE, that would stop a garage invoicing anybody who pays by
cheque -- and the same specification asks for owner exceptions covering bounced
cheques, so cheque payers plainly exist. So the mandate gates the CHARGE: an
invoice is issued and sent to anyone, and ``charge`` refuses by name without one.
A payer who pays by cheque simply never has one charged.

**THE RETRY RESET IS AN INPUT, BECAUSE IT HAS TO BE.** Three attempts maximum,
reset when a new payment method is added. This module holds no payment method --
that is the rule above -- so it cannot observe one being added, and a count that
reset itself on a fact it could not see would be a count that never reset.
``record_payment_method_changed`` is how the caller tells it. Stated because the
specification asked for a behaviour that its own PCI rule makes unobservable, and
an input is the only honest way to have both.

**A CHARGE IS FOR THE BALANCE, AND NOTHING OWED IS REFUSED BY NAME.** ``charge_invoice``
takes the amount to charge; the store-backed caller passes the outstanding balance
(total minus unreversed payments) and a caller with no store passes nothing and
gets the total. An amount of zero or less is ``REFUSAL_NOTHING_OWED`` before the
processor is called: a paid invoice is never charged again, a part-paid one is
charged its remainder, and a fully waived one is not charged at all.

**A PROCESSOR'S RESULT IS NEVER DISCARDED SILENTLY.** The processor call is the
one thing here this module does not control. If it raises, the outcome is ERROR
with the exception named in the detail; if what it returned could not even be
received -- a result carrying an instrument-shaped reference is refused by the
guard inside the processor's own return -- the outcome is ERROR and the detail
says UNKNOWN, because money may have moved and this module cannot know. A row is
written for every one of those by the store-backed caller; a message that would
itself trip the guard is replaced by the fixed word ``withheld`` rather than
losing the row to its own text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from .agreement import Agreement
from .findings import (
    REFUSAL_NO_MANDATE,
    REFUSAL_NOTHING_OWED,
    REFUSAL_RETRIES_EXHAUSTED,
    Refused,
)
from .invoice import Invoice
from .sensitive import InstrumentLike, refuse_instrument_like

#: His rule. Three, and the fourth needs a new payment method first.
MAX_ATTEMPTS = 3


class Outcome(Enum):
    SUCCESS = "success"
    DECLINE = "decline"
    ERROR = "error"


@dataclass(frozen=True)
class ChargeRequest:
    """Charge this payer this amount in this currency for this invoice.

    There is no instrument on this request, and there is no field one could be
    put in. Whatever can actually move money is the collection module's, held
    with the processor; this module names the payer and the amount and nothing
    that could be used without them.
    """

    payer_id: str
    amount_minor: int
    currency: str
    invoice_reference: str


@dataclass(frozen=True)
class ChargeResult:
    outcome: Outcome
    #: Why, for a person. Never contains an instrument -- checked, not trusted.
    detail: str
    #: The processor's own reference for a SUCCESS, if it gave one. It is what
    #: the card payment row records as `processor_reference`, so a payment can
    #: be traced back to the processor's side. Never an instrument -- checked.
    reference: str | None = None

    def __post_init__(self) -> None:
        refuse_instrument_like(self.detail, "charge_result.detail")
        if self.reference is not None:
            refuse_instrument_like(self.reference, "charge_result.reference")


@dataclass(frozen=True)
class RetryState:
    """How many attempts an invoice has had, and why the last one failed.

    M1 records; the collection module performs the attempts. Both halves are
    here because the count is what decides whether a fourth attempt is allowed,
    and that decision belongs with the agreement rather than with whichever
    process happens to be retrying.
    """

    invoice_reference: str
    attempts: int = 0
    last_outcome: Outcome | None = None
    last_detail: str = ""

    def __post_init__(self) -> None:
        refuse_instrument_like(self.last_detail, "retry_state.last_detail")

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_ATTEMPTS

    def after_attempt(self, result: ChargeResult) -> RetryState:
        if result.outcome is Outcome.SUCCESS:
            return replace(self, attempts=0, last_outcome=result.outcome, last_detail="")
        return replace(
            self,
            attempts=self.attempts + 1,
            last_outcome=result.outcome,
            last_detail=result.detail,
        )

    def record_payment_method_changed(self) -> RetryState:
        """Reset the count. The caller reports this; this module cannot see it."""
        return replace(self, attempts=0, last_outcome=None, last_detail="")


class PaymentProcessor(Protocol):
    """What a processor has to do. One method, and it says what happened.

    A Protocol rather than a base class: an integrator's processor is their own
    object and should not have to inherit anything of ours to be usable here.
    """

    def charge(self, request: ChargeRequest) -> ChargeResult: ...


class StubProcessor:
    """The M1 processor. It moves no money and says so.

    Deliberately not a fake that pretends to succeed. A stub that returned
    SUCCESS would let every test above it pass while nothing was ever collected,
    and the day a real processor arrives is the day that becomes visible. This
    one records the request and returns ERROR with a sentence naming itself, so
    anything depending on a real charge fails loudly today.
    """

    def __init__(self) -> None:
        self.requests: list[ChargeRequest] = []

    def charge(self, request: ChargeRequest) -> ChargeResult:
        self.requests.append(request)
        return ChargeResult(
            outcome=Outcome.ERROR,
            detail=(
                "no payment processor is connected. This module defines the interface "
                "and ships a stub; collection is its own module and its own round."
            ),
        )


#: The detail an attempt carries when the processor's result could not be received.
#: Fixed text, because the thing it describes is exactly the case where nothing the
#: processor said may be repeated.
RESULT_UNKNOWN_DETAIL = (
    "processor result refused by the instrument guard; outcome unknown -- reconcile "
    "with the processor"
)

#: The detail an attempt carries when the processor raised and its message would
#: itself trip the guard. The row is never lost to its own text.
DETAIL_WITHHELD = "withheld"


def result_of(processor: PaymentProcessor, request: ChargeRequest) -> ChargeResult:
    """Call the processor and ALWAYS come back with a result.

    A raise becomes ERROR naming the exception; a result the guard refused inside
    the processor's own return becomes ERROR saying unknown. See the module
    docstring: a reported result is never discarded silently.
    """
    try:
        return processor.charge(request)
    except InstrumentLike:
        return ChargeResult(outcome=Outcome.ERROR, detail=RESULT_UNKNOWN_DETAIL)
    except Exception as exc:  # noqa: BLE001 -- the processor is the one thing not ours
        detail = f"{type(exc).__name__}: {exc}"
        try:
            return ChargeResult(outcome=Outcome.ERROR, detail=detail)
        except InstrumentLike:
            return ChargeResult(outcome=Outcome.ERROR, detail=DETAIL_WITHHELD)


def charge_invoice(
    processor: PaymentProcessor,
    agreement: Agreement,
    invoice: Invoice,
    retry: RetryState,
    *,
    amount_minor: int | None = None,
) -> tuple[ChargeResult, RetryState]:
    """Attempt one charge, or refuse by name.

    Three refusals, all before anything is attempted: no mandate, retries
    exhausted, and nothing owed. Refusing before the attempt matters -- a
    processor that has already been called cannot be un-called, and "we tried it
    anyway and then complained" is how a customer gets charged on an agreement
    nobody agreed to, or twice for one month.

    ``amount_minor`` is what is charged: the caller's balance, or the invoice
    total when none is given. The request carries that amount and never the total
    by default of somebody forgetting to subtract.
    """
    if agreement.mandate is None:
        raise Refused(
            REFUSAL_NO_MANDATE,
            f"agreement {agreement.id!r} version {agreement.version} has no mandate "
            f"record. Its invoice for {invoice.issued_for_period_start} may still be "
            "issued and sent; it is the charge that is refused.",
        )
    if retry.exhausted:
        raise Refused(
            REFUSAL_RETRIES_EXHAUSTED,
            f"invoice {retry.invoice_reference!r} has had {retry.attempts} attempts, "
            f"and the maximum is {MAX_ATTEMPTS}.",
        )

    amount = invoice.total_minor if amount_minor is None else amount_minor
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise Refused(
            REFUSAL_NOTHING_OWED,
            f"invoice {retry.invoice_reference!r} has {amount!r} minor units to charge.",
        )

    result = result_of(
        processor,
        ChargeRequest(
            payer_id=invoice.payer_id,
            amount_minor=amount,
            currency=invoice.currency,
            invoice_reference=retry.invoice_reference,
        ),
    )
    return result, retry.after_attempt(result)
