"""Payments received, reversals, and what "paid" means.

**PAID IS DERIVED, NEVER SET BY HAND.** An invoice is paid when the sum of its
UNREVERSED payments reaches its total, and ``paid_at`` is the instant of the
event that made that true. Nothing here takes a "mark as paid" instruction;
there is no such function, so there is no such button. ``rederive_paid_at`` is
called after every one of the THREE events that can change the answer:

1. a payment is recorded -- the sum goes up;
2. a reversal is recorded -- the sum goes down;
3. an owner exception with an amount lands an adjustment line -- the TOTAL
   moves, which changes the comparison without touching the payments.

The third is the one that is easy to forget, and forgetting it leaves a payer
who was credited down to what they had already paid still reading as unpaid at
the barrier. A control plants that omission and requires red.

**A REVERSAL REOPENS THE INVOICE FROM ITS ORIGINAL DUE DATE.** A cheque that
bounced was never money. The invoice was unpaid all along; the payer's grace
period ran from the billing day, not from the day the bank said no. So a
reversal clears ``paid_at`` and leaves ``due_at`` exactly where it was, and the
lane reads the ORIGINAL due date as the start of the unpaid period. A control
plants the other reading -- due from the reversal -- and requires red.

**A PARTIAL PAYMENT LEAVES THE INVOICE UNPAID, AND THE MODULE DECIDES NOTHING
ABOUT IT.** The owner records an exception with an amount, or does not. Money
received is a payment; a decision is an exception; they are different rows and
never one.

**CARD PAYMENTS ENTER ONLY THROUGH ``charging.attempt_charge``.** A card payment
is the record of a processor saying SUCCESS to a charge this module made against
a mandate, and that path is the one that writes it. ``record_payment`` refuses
``card`` so that a hand-typed card payment -- an operator "recording" a card
charge nobody made -- has nowhere to land.

⛔ **NO INSTRUMENT, EVER.** Every row here goes through ``guarded_insert``, which
scans every column. ``processor_reference`` is the processor's reference, a
cheque number or an ACH trace, and it is scanned like any other text. A card
brand and the last four digits may be stored on a card payment; the migration's
CHECK stops anything longer, and the guard stops anything card-shaped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from .store.postgres import tenant
from .store.writes import as_uuid, guarded_insert, guarded_update


class PaymentMethod(Enum):
    CARD = "card"
    CHEQUE = "cheque"
    ACH = "ach"


#: The methods an operator records by hand. Card is not one: a card payment is
#: what the charge path writes when a processor says SUCCESS, and nothing else.
#: ``record_payment`` refuses anything outside this set, and the contract
#: document derives its list from it.
OPERATOR_RECORDED: frozenset[PaymentMethod] = frozenset(
    {PaymentMethod.CHEQUE, PaymentMethod.ACH}
)


class ReversalReason(Enum):
    BOUNCED_CHEQUE = "bounced_cheque"
    ACH_RETURNED = "ach_returned"
    CHARGEBACK = "chargeback"
    PROCESSOR_REVERSED = "processor_reversed"


class InvoiceNotFound(LookupError):
    """The reference names no invoice in the store."""


class PaymentNotFound(LookupError):
    """The id names no payment in the store."""


class CardPaymentsEnterThroughTheCharge(ValueError):
    """``record_payment`` was asked to record a card payment by hand."""


@dataclass(frozen=True)
class PaidState:
    """What the store now says about one invoice."""

    invoice_id: UUID
    total_minor: int
    paid_minor: int
    paid_at: datetime | None

    @property
    def paid(self) -> bool:
        return self.paid_at is not None


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------


def paid_state(cursor: Any, invoice_uuid: Any) -> PaidState:
    """The total, the unreversed sum, and ``paid_at`` as they stand. Read only."""
    invoice_uuid = as_uuid(invoice_uuid)
    cursor.execute(
        "SELECT coalesce(sum(amount_minor), 0) FROM invoice_lines WHERE invoice_id = %s",
        (invoice_uuid,),
    )
    (total,) = cursor.fetchone()
    cursor.execute(
        """
        SELECT coalesce(sum(p.amount_minor), 0)
        FROM payments p
        WHERE p.invoice_id = %s
          AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
        """,
        (invoice_uuid,),
    )
    (paid,) = cursor.fetchone()
    cursor.execute("SELECT paid_at FROM invoices WHERE id = %s", (invoice_uuid,))
    row = cursor.fetchone()
    if row is None:
        raise InvoiceNotFound(f"no invoice with id {invoice_uuid!r}.")
    return PaidState(invoice_id=invoice_uuid, total_minor=int(total), paid_minor=int(paid),
                     paid_at=row[0])


def rederive_paid_at(cursor: Any, invoice_uuid: Any, event_at: datetime) -> PaidState:
    """Recompute ``paid_at`` after an event, and write it only if it changed.

    Paid iff unreversed payments >= the current total. Becoming paid stamps the
    event's instant; ceasing to be paid clears it. An invoice that was already
    paid and stays paid keeps the instant it became paid -- the event that made
    it true, not the latest one.
    """
    invoice_uuid = as_uuid(invoice_uuid)
    state = paid_state(cursor, invoice_uuid)
    covered = state.paid_minor >= state.total_minor
    if covered and state.paid_at is None:
        guarded_update(cursor, "invoices", {"paid_at": event_at}, {"id": invoice_uuid})
        return PaidState(state.invoice_id, state.total_minor, state.paid_minor, event_at)
    if not covered and state.paid_at is not None:
        guarded_update(cursor, "invoices", {"paid_at": None}, {"id": invoice_uuid})
        return PaidState(state.invoice_id, state.total_minor, state.paid_minor, None)
    return state


# ---------------------------------------------------------------------------
# The three events
# ---------------------------------------------------------------------------


def invoice_uuid_for(cursor: Any, reference: str) -> UUID:
    cursor.execute("SELECT id FROM invoices WHERE reference = %s", (reference,))
    row = cursor.fetchone()
    if row is None:
        raise InvoiceNotFound(f"no invoice with reference {reference!r}.")
    return as_uuid(row[0])


def record_payment(
    connection: Any,
    tenant_id: Any,
    invoice_reference: str,
    method: PaymentMethod,
    amount_minor: int,
    received_at: datetime,
    *,
    recorded_by: str,
    processor_reference: str | None = None,
    card_brand: str | None = None,
    card_last4: str | None = None,
) -> tuple[UUID, PaidState]:
    """Cheque or ACH, recorded by the operator. Card is refused here by design.

    Returns the payment's id and the invoice's paid state afterwards. One
    transaction: the payment and the derivation land together.
    """
    if method not in OPERATOR_RECORDED:
        raise CardPaymentsEnterThroughTheCharge(
            "a card payment is the record of a processor saying SUCCESS to a charge "
            "this module made. It is written by charging.attempt_charge and by nothing "
            "else; there is no hand-typed card payment."
        )
    return _record_payment(
        connection, tenant_id, invoice_reference, method, amount_minor, received_at,
        recorded_by=recorded_by, processor_reference=processor_reference,
        card_brand=card_brand, card_last4=card_last4,
    )


def _record_payment(
    connection: Any,
    tenant_id: Any,
    invoice_reference: str,
    method: PaymentMethod,
    amount_minor: int,
    received_at: datetime,
    *,
    recorded_by: str,
    processor_reference: str | None,
    card_brand: str | None,
    card_last4: str | None,
) -> tuple[UUID, PaidState]:
    tenant_id = as_uuid(tenant_id)
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise ValueError(
            f"a payment is a positive integer of minor units, not {amount_minor!r}. A "
            "payment of nothing is not a payment, and a negative one is a reversal "
            "wearing the wrong row."
        )
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        cursor.execute("SELECT currency FROM invoices WHERE id = %s", (invoice_uuid,))
        (currency,) = cursor.fetchone()
        guarded_insert(
            cursor,
            "payments",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "method": method.value,
                "amount_minor": amount_minor,
                "currency": currency,
                "received_at": received_at,
                "processor_reference": processor_reference,
                "card_brand": card_brand,
                "card_last4": card_last4,
                "recorded_by": recorded_by,
            },
        )
        (payment_uuid,) = cursor.fetchone()
        state = rederive_paid_at(cursor, invoice_uuid, received_at)
    connection.commit()
    return as_uuid(payment_uuid), state


def record_reversal(
    connection: Any,
    tenant_id: Any,
    payment_id: Any,
    reason: ReversalReason,
    reversed_at: datetime,
    *,
    recorded_by: str,
    note: str = "",
) -> PaidState:
    """The payment did not stand. A second row; the first is never touched.

    What happens next is the owner's decision and is an owner exception. This
    records the fact and re-derives the invoice: if the sum is now below the
    total, ``paid_at`` is cleared and the invoice is unpaid from its ORIGINAL
    ``due_at`` -- which this function does not read, because it does not change.
    """
    tenant_id, payment_id = as_uuid(tenant_id), as_uuid(payment_id)
    with tenant(connection, tenant_id) as cursor:
        cursor.execute("SELECT invoice_id FROM payments WHERE id = %s", (payment_id,))
        row = cursor.fetchone()
        if row is None:
            raise PaymentNotFound(f"no payment with id {payment_id!r}.")
        invoice_uuid = as_uuid(row[0])
        guarded_insert(
            cursor,
            "payment_reversals",
            {
                "tenant_id": tenant_id,
                "payment_id": payment_id,
                "reason": reason.value,
                "reversed_at": reversed_at,
                "recorded_by": recorded_by,
                "note": note,
            },
        )
        cursor.fetchone()
        state = rederive_paid_at(cursor, invoice_uuid, reversed_at)
    connection.commit()
    return state
