"""Charging an invoice against its mandate, with the attempts kept as a LOG.

**``charge_attempts`` IS THE TRUTH FOR RETRIES.** M1's ``RetryState`` was a value
the caller carried; on restart it was whatever the caller remembered, which is
nothing. It is now REBUILT from the rows every time: one row per attempt with its
outcome, one row per ``payment_method_changed``, and the state is the fold of the
rows since the last method change through M1's own ``after_attempt`` and
``record_payment_method_changed`` -- the same arithmetic, fed from the store.
Three persisted non-success attempts since the last method change and the next
charge is refused by name; a persisted method change and the fourth is allowed.
A control plants a rebuild that ignores the method-change rows and requires red.

**EVERY ATTEMPT IS PERSISTED, SUCCESS OR NOT, BEFORE ITS RESULT IS ACTED ON --
AND WHATEVER THE PROCESSOR DID.** The row is the record that a processor was
called. A processor that raised is an ``error`` row naming the exception; a result
the guard refused inside the processor's own return is an ``error`` row whose
detail says the outcome is UNKNOWN (see ``payment.result_of``). A success
additionally records the card payment -- the ONLY path that writes a ``card``
payment -- and re-derives ``paid_at``. The table is append-only by grant, so
nothing here overwrites what happened.

**A CHARGE IS FOR THE BALANCE, AND A PAID INVOICE IS NEVER CHARGED.** The
outstanding balance -- the invoice's total minus its unreversed payments, read
through the same ``paid_state`` the derivation uses, so there is one reading of
"owed" and not two -- is what the request carries. A balance of nothing is
``REFUSAL_NOTHING_OWED`` before the processor is called and before any row is
written, because nothing was attempted. A control plants the total back in place
of the balance and requires red.

**THE MANDATE IS CHECKED FOR EVERY AGREEMENT ON THE INVOICE.** A payer's invoice
itemises several agreements; if any one of them has no mandate, nothing is
charged, by name, before the processor is called -- the M1 rule, applied to the
whole invoice rather than to one document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .invoice import Invoice, InvoiceLine, LineKind
from .payment import ChargeResult, Outcome, PaymentProcessor, RetryState, charge_invoice
from .payments import (
    PaidState,
    PaymentMethod,
    _record_payment,
    invoice_uuid_for,
    paid_state,
)
from .store.postgres import tenant
from .store.records import load_agreements_at_garage
from .store.writes import as_uuid, guarded_insert


@dataclass(frozen=True)
class ChargeOutcome:
    result: ChargeResult
    retry: RetryState
    payment_id: Any | None
    paid: PaidState | None


def retry_state_from_rows(
    invoice_reference: str, rows: list[tuple[str, str | None, str]]
) -> RetryState:
    """Fold the log into M1's ``RetryState``. ``rows`` are (kind, outcome, detail)
    in occurrence order.

    A ``payment_method_changed`` row resets -- through the same method the
    caller used to call -- so everything before it stops counting.
    """
    state = RetryState(invoice_reference=invoice_reference)
    for kind, outcome, detail in rows:
        if kind == "payment_method_changed":
            state = state.record_payment_method_changed()
        else:
            state = state.after_attempt(ChargeResult(outcome=Outcome(outcome), detail=detail))
    return state


def _load_attempt_rows(cursor: Any, invoice_uuid: Any) -> list[tuple[str, str | None, str]]:
    cursor.execute(
        "SELECT kind, outcome, detail FROM charge_attempts WHERE invoice_id = %s "
        "ORDER BY occurred_at, created_at",
        (invoice_uuid,),
    )
    return [(kind, outcome, detail) for kind, outcome, detail in cursor.fetchall()]


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


def attempt_charge(
    connection: Any,
    tenant_id: Any,
    processor: PaymentProcessor,
    invoice_reference: str,
    *,
    recorded_by: str,
    now: datetime,
) -> ChargeOutcome:
    """One attempt, or a refusal by name. The attempt row is written whatever
    the processor said; a success also records the card payment."""
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        invoice, garage_uuid = _load_invoice(cursor, invoice_uuid)
        on_invoice = {line.agreement_id for line in invoice.lines}
        agreements = [
            item.agreement
            for item in load_agreements_at_garage(cursor, garage_uuid)
            if item.agreement.id in on_invoice
        ]
        retry = retry_state_from_rows(invoice_reference, _load_attempt_rows(cursor, invoice_uuid))
        owed = paid_state(cursor, invoice_uuid)
    connection.rollback()

    # The mandate rule, for every agreement the invoice itemises: the first one
    # without a mandate is the one the refusal names.
    gate = next((a for a in agreements if a.mandate is None), agreements[0])
    balance = owed.total_minor - owed.paid_minor
    result, retry_after = charge_invoice(processor, gate, invoice, retry, amount_minor=balance)

    with tenant(connection, tenant_id) as cursor:
        guarded_insert(
            cursor,
            "charge_attempts",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": "attempt",
                "outcome": result.outcome.value,
                "detail": result.detail,
                "occurred_at": now,
                "recorded_by": recorded_by,
            },
        )
        cursor.fetchone()
    connection.commit()

    if result.outcome is not Outcome.SUCCESS:
        return ChargeOutcome(result=result, retry=retry_after, payment_id=None, paid=None)

    payment_id, paid = _record_payment(
        connection, tenant_id, invoice_reference, PaymentMethod.CARD, balance, now,
        recorded_by=recorded_by, processor_reference=result.reference,
        card_brand=None, card_last4=None,
    )
    return ChargeOutcome(result=result, retry=retry_after, payment_id=payment_id, paid=paid)


def record_payment_method_changed(
    connection: Any,
    tenant_id: Any,
    invoice_reference: str,
    *,
    recorded_by: str,
    now: datetime,
) -> RetryState:
    """The caller reports a new payment method. Persisted as a row, so the reset
    survives a restart; the returned state is rebuilt from the rows."""
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
    "ChargeOutcome",
    "attempt_charge",
    "record_payment_method_changed",
    "retry_state_from_rows",
]
