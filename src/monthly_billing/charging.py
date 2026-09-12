"""Charging an invoice against its mandate: a RESERVATION, the processor, an OUTCOME.

**THE ATTEMPT ROW IS WRITTEN BEFORE THE PROCESSOR IS CALLED, AND THE OUTCOME
LANDS WITH ITS PAYMENT.** The outside pass ran the old shape and showed two
crash windows and a race, all from the same cause -- the row was written after
the processor had acted:

* a crash after the processor said yes and before any row left nothing, and a
  restart charged again;
* a crash between a committed SUCCESS row and its payment left a success that
  reset the retry count, and a restart charged again;
* two workers reading one balance both collected it.

So a charge is now two transactions with a PERSISTED RESERVATION between them,
and every transaction that touches the invoice's money takes the invoice row
lock first (``store.postgres.lock_invoice``):

* **T1, under the lock:** refuse by name if an attempt on this invoice is still
  pending; rebuild the retry state from the outcome rows; recompute the balance;
  refuse no-mandate, retries-exhausted and nothing-owed exactly as before; then
  write the ``attempt`` row -- a fresh ``attempt_id``, the amount and currency
  RESERVED -- and commit. The processor has not been called.
* **The processor, outside any lock,** handed the attempt id as its idempotency
  key.
* **T2, under the lock:** write the ``outcome`` row naming the attempt, and on
  success the card payment for the amount RESERVED -- in the same transaction --
  and re-derive ``paid_at``. There is no instant at which a success is recorded
  and its money is not.

**A PENDING ATTEMPT IS NEVER CHARGED PAST.** A reservation with no outcome row
-- the worker died after T1, or after the processor answered -- refuses the
next charge by name (``REFUSAL_ATTEMPT_UNRESOLVED``) until an operator records
what the processor says (``resolve_attempt``), which writes the outcome row
and, on success, the payment, under the same lock. Money may have moved; the
module says so rather than guessing either way.

**A CHEQUE BETWEEN T1 AND T2 DOES NOT DROP THE CARD PAYMENT.** The processor
moved the money the reservation asked for; T2 records it for the reserved
amount, and the invoice reads OVERPAID (``PaidState.overpaid_minor``). Never
silently, never dropped -- and the operator's refund is the owner's exception.

**``charge_attempts`` IS THE TRUTH FOR RETRIES.** The retry state is the fold
of the OUTCOME rows since the last ``payment_method_changed`` through M1's own
``after_attempt``; a pending attempt counts as neither, and a success counts
toward nothing and resets nothing -- the reset is the caller's report of a new
payment method, which is allowed while an attempt is pending because it moves
no money.

**A CHARGE IS FOR THE BALANCE, AND A PAID INVOICE IS NEVER CHARGED.** The
balance -- the invoice's total minus its unreversed payments, read through the
same ``paid_state`` the derivation uses -- is what is reserved and requested.
Nothing owed is ``REFUSAL_NOTHING_OWED`` before any row is written.

**THE MANDATE IS CHECKED FOR EVERY AGREEMENT ON THE INVOICE**, as before: if
any one of them has no mandate, nothing is charged, by name, before the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from .findings import (
    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
    REFUSAL_ATTEMPT_UNRESOLVED,
    Refused,
)
from .invoice import Invoice, InvoiceLine, LineKind
from .payment import (
    ChargeRequest,
    ChargeResult,
    Outcome,
    PaymentProcessor,
    RetryState,
    refuse_unless_chargeable,
    result_of,
)
from .payments import (
    PaidState,
    PaymentMethod,
    insert_payment,
    invoice_uuid_for,
    paid_state,
    rederive_paid_at,
)
from .store.postgres import lock_invoice, tenant
from .store.records import load_agreements_at_garage
from .store.writes import as_uuid, guarded_insert


@dataclass(frozen=True)
class ChargeOutcome:
    result: ChargeResult
    retry: RetryState
    attempt_id: UUID
    payment_id: UUID | None
    paid: PaidState | None


class AttemptNotFound(LookupError):
    """The id names no pending attempt in the store."""


def retry_state_from_rows(
    invoice_reference: str, rows: list[tuple[str, str | None, str]]
) -> RetryState:
    """Fold the log into M1's ``RetryState``. ``rows`` are (kind, outcome, detail)
    in occurrence order.

    Only ``outcome`` rows count. A ``payment_method_changed`` row resets --
    through the same method the caller used to call -- so everything before it
    stops counting; an ``attempt`` row is a reservation, not a result, and
    contributes nothing.
    """
    state = RetryState(invoice_reference=invoice_reference)
    for kind, outcome, detail in rows:
        if kind == "payment_method_changed":
            state = state.record_payment_method_changed()
        elif kind == "outcome":
            state = state.after_attempt(ChargeResult(outcome=Outcome(outcome), detail=detail))
    return state


def _load_attempt_rows(cursor: Any, invoice_uuid: Any) -> list[tuple[str, str | None, str]]:
    cursor.execute(
        "SELECT kind, outcome, detail FROM charge_attempts WHERE invoice_id = %s "
        "ORDER BY occurred_at, created_at",
        (invoice_uuid,),
    )
    return [(kind, outcome, detail) for kind, outcome, detail in cursor.fetchall()]


def _pending_attempt(cursor: Any, invoice_uuid: Any) -> UUID | None:
    """The attempt id of a reservation with no outcome row, if any."""
    cursor.execute(
        """
        SELECT a.attempt_id FROM charge_attempts a
        WHERE a.invoice_id = %s AND a.kind = 'attempt'
          AND NOT EXISTS (SELECT 1 FROM charge_attempts o
                          WHERE o.attempt_id = a.attempt_id AND o.kind = 'outcome')
        ORDER BY a.occurred_at, a.created_at LIMIT 1
        """,
        (invoice_uuid,),
    )
    row = cursor.fetchone()
    return None if row is None else as_uuid(row[0])


def _load_invoice(cursor: Any, invoice_uuid: Any) -> tuple[Invoice, Any]:
    """The invoice as the engine's value, and the garage uuid it belongs to."""
    cursor.execute(
        "SELECT p.external_id, g.external_id, g.id, i.currency, i.period_start_day "
        "FROM invoices i JOIN payers p ON p.id = i.payer_id JOIN garages g ON g.id = i.garage_id "
        "WHERE i.id = %s",
        (invoice_uuid,),
    )
    payer_external, garage_external, garage_uuid, currency, period_start = cursor.fetchone()
    cursor.execute(
        "SELECT l.kind, l.label, l.amount_minor, a.external_id, l.agreement_version, "
        "l.period_start_day, l.period_end_day, l.exception_id "
        "FROM invoice_lines l JOIN agreements a ON a.id = l.agreement_id "
        "WHERE l.invoice_id = %s ORDER BY l.created_at",
        (invoice_uuid,),
    )
    lines = tuple(
        InvoiceLine(
            kind=LineKind(kind),
            label=label,
            amount_minor=amount,
            agreement_id=agreement_external,
            agreement_version=version,
            period_start_day=start,
            period_end_day=end,
            exception_id=None if exception_id is None else str(exception_id),  # engine value
        )
        for kind, label, amount, agreement_external, version, start, end, exception_id
        in cursor.fetchall()
    )
    invoice = Invoice(
        payer_id=payer_external,
        garage_id=garage_external,
        currency=currency.strip(),
        issued_for_period_start=period_start,
        lines=lines,
    )
    return invoice, as_uuid(garage_uuid)


@dataclass(frozen=True)
class _Reservation:
    attempt_id: UUID
    invoice_uuid: UUID
    invoice: Invoice
    amount_minor: int
    retry: RetryState


def _reserve(
    connection: Any, tenant_id: UUID, invoice_reference: str, *, recorded_by: str, now: datetime
) -> _Reservation:
    """T1. Under the lock: refuse by name, or write the reservation and commit."""
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        lock_invoice(cursor, invoice_uuid)
        pending = _pending_attempt(cursor, invoice_uuid)
        if pending is not None:
            connection.rollback()
            raise Refused(
                REFUSAL_ATTEMPT_UNRESOLVED,
                f"invoice {invoice_reference!r} has a pending attempt; resolve it first.",
            )
        invoice, garage_uuid = _load_invoice(cursor, invoice_uuid)
        on_invoice = {line.agreement_id for line in invoice.lines}
        agreements = [
            item.agreement
            for item in load_agreements_at_garage(cursor, garage_uuid)
            if item.agreement.id in on_invoice
        ]
        retry = retry_state_from_rows(invoice_reference, _load_attempt_rows(cursor, invoice_uuid))
        owed = paid_state(cursor, invoice_uuid)
        # The mandate rule, for every agreement the invoice itemises: the first
        # one without a mandate is the one the refusal names.
        gate = next((a for a in agreements if a.mandate is None), agreements[0])
        balance = owed.total_minor - owed.paid_minor
        try:
            refuse_unless_chargeable(gate, invoice, retry, balance)
        except Refused:
            connection.rollback()
            raise
        attempt_id = uuid4()
        guarded_insert(
            cursor,
            "charge_attempts",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": "attempt",
                "attempt_id": attempt_id,
                "amount_minor": balance,
                "currency": invoice.currency,
                "outcome": None,
                "detail": "",
                "occurred_at": now,
                "recorded_by": recorded_by,
            },
        )
        cursor.fetchone()
    connection.commit()
    return _Reservation(attempt_id, invoice_uuid, invoice, balance, retry)


def _settle(
    connection: Any,
    tenant_id: UUID,
    invoice_uuid: UUID,
    attempt_id: UUID,
    result: ChargeResult,
    *,
    recorded_by: str,
    now: datetime,
) -> tuple[RetryState, UUID | None, PaidState | None]:
    """T2. Under the lock: the outcome row, and on success the payment for the
    RESERVED amount, in one transaction. Shared by the charge and by the
    operator's resolution of a pending attempt."""
    with tenant(connection, tenant_id) as cursor:
        lock_invoice(cursor, invoice_uuid)
        cursor.execute(
            "SELECT amount_minor, invoice_id FROM charge_attempts "
            "WHERE attempt_id = %s AND kind = 'attempt'",
            (attempt_id,),
        )
        row = cursor.fetchone()
        if row is None:
            connection.rollback()
            raise AttemptNotFound(f"no attempt with id {attempt_id!r}.")
        reserved, reserved_invoice = int(row[0]), as_uuid(row[1])
        if reserved_invoice != invoice_uuid:
            connection.rollback()
            raise AttemptNotFound(f"attempt {attempt_id!r} is not on invoice {invoice_uuid!r}.")
        cursor.execute(
            "SELECT 1 FROM charge_attempts WHERE attempt_id = %s AND kind = 'outcome'",
            (attempt_id,),
        )
        if cursor.fetchone() is not None:
            connection.rollback()
            raise Refused(
                REFUSAL_ATTEMPT_ALREADY_RESOLVED,
                f"attempt {attempt_id!r} already has an outcome recorded.",
            )
        guarded_insert(
            cursor,
            "charge_attempts",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": "outcome",
                "attempt_id": attempt_id,
                "outcome": result.outcome.value,
                "detail": result.detail,
                "occurred_at": now,
                "recorded_by": recorded_by,
            },
        )
        cursor.fetchone()
        payment_id = paid = None
        if result.outcome is Outcome.SUCCESS:
            payment_id = insert_payment(
                cursor, tenant_id, invoice_uuid, PaymentMethod.CARD, reserved, now,
                recorded_by=recorded_by, processor_reference=result.reference,
                card_brand=None, card_last4=None,
            )
            paid = rederive_paid_at(cursor, invoice_uuid, now)
        retry = retry_state_from_rows(
            _reference_of(cursor, invoice_uuid), _load_attempt_rows(cursor, invoice_uuid)
        )
    connection.commit()
    return retry, payment_id, paid


def _reference_of(cursor: Any, invoice_uuid: Any) -> str:
    cursor.execute("SELECT reference FROM invoices WHERE id = %s", (invoice_uuid,))
    return cursor.fetchone()[0]


def attempt_charge(
    connection: Any,
    tenant_id: Any,
    processor: PaymentProcessor,
    invoice_reference: str,
    *,
    recorded_by: str,
    now: datetime,
) -> ChargeOutcome:
    """One attempt, or a refusal by name. The reservation is written before the
    processor is called; the outcome and its payment land together."""
    tenant_id = as_uuid(tenant_id)
    reservation = _reserve(
        connection, tenant_id, invoice_reference, recorded_by=recorded_by, now=now
    )

    result = result_of(
        processor,
        ChargeRequest(
            payer_id=reservation.invoice.payer_id,
            amount_minor=reservation.amount_minor,
            currency=reservation.invoice.currency,
            invoice_reference=invoice_reference,
            idempotency_key=reservation.attempt_id,
        ),
    )

    retry, payment_id, paid = _settle(
        connection, tenant_id, reservation.invoice_uuid, reservation.attempt_id, result,
        recorded_by=recorded_by, now=now,
    )
    return ChargeOutcome(
        result=result, retry=retry, attempt_id=reservation.attempt_id,
        payment_id=payment_id, paid=paid,
    )


def resolve_attempt(
    connection: Any,
    tenant_id: Any,
    attempt_id: Any,
    result: ChargeResult,
    *,
    recorded_by: str,
    now: datetime,
) -> ChargeOutcome:
    """The operator records what the processor said about a PENDING attempt.

    The same T2 the charge path runs: the outcome row and, on success, the card
    payment for the amount reserved, under the lock. An attempt that already
    has an outcome is refused by name; an id that names no reservation is not
    found.
    """
    tenant_id, attempt_id = as_uuid(tenant_id), as_uuid(attempt_id)
    with tenant(connection, tenant_id) as cursor:
        cursor.execute(
            "SELECT invoice_id FROM charge_attempts WHERE attempt_id = %s AND kind = 'attempt'",
            (attempt_id,),
        )
        row = cursor.fetchone()
    connection.rollback()
    if row is None:
        raise AttemptNotFound(f"no attempt with id {attempt_id!r}.")
    invoice_uuid = as_uuid(row[0])
    retry, payment_id, paid = _settle(
        connection, tenant_id, invoice_uuid, attempt_id, result,
        recorded_by=recorded_by, now=now,
    )
    return ChargeOutcome(
        result=result, retry=retry, attempt_id=attempt_id, payment_id=payment_id, paid=paid
    )


def pending_attempts(connection: Any, tenant_id: Any, invoice_reference: str) -> tuple[UUID, ...]:
    """The reservations on an invoice with no outcome row, oldest first."""
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        cursor.execute(
            """
            SELECT a.attempt_id FROM charge_attempts a
            WHERE a.invoice_id = %s AND a.kind = 'attempt'
              AND NOT EXISTS (SELECT 1 FROM charge_attempts o
                              WHERE o.attempt_id = a.attempt_id AND o.kind = 'outcome')
            ORDER BY a.occurred_at, a.created_at
            """,
            (invoice_uuid,),
        )
        ids = tuple(as_uuid(r[0]) for r in cursor.fetchall())
    connection.rollback()
    return ids


def record_payment_method_changed(
    connection: Any,
    tenant_id: Any,
    invoice_reference: str,
    *,
    recorded_by: str,
    now: datetime,
) -> RetryState:
    """The caller reports a new payment method. Persisted as a row, so the reset
    survives a restart; the returned state is rebuilt from the rows.

    Allowed while an attempt is pending: it moves no money, and an operator
    changing a card while collection is stuck must not be refused.
    """
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        guarded_insert(
            cursor,
            "charge_attempts",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": "payment_method_changed",
                "outcome": None,
                "detail": "",
                "occurred_at": now,
                "recorded_by": recorded_by,
            },
        )
        cursor.fetchone()
        state = retry_state_from_rows(invoice_reference, _load_attempt_rows(cursor, invoice_uuid))
    connection.commit()
    return state


__all__ = [
    "AttemptNotFound",
    "ChargeOutcome",
    "attempt_charge",
    "pending_attempts",
    "record_payment_method_changed",
    "resolve_attempt",
    "retry_state_from_rows",
]
