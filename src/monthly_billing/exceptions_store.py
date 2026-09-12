"""The owner's exceptions, written to the store.

Two attachment points, as M1 defined them: an exception sits on an AGREEMENT
(a grace extension, a block, an unblock) or on an INVOICE (a waived fee, a
credit, a refund -- and a grace extension can sit here too). A kind that changes
what is owed lands an ``exception_adjustment`` line and re-derives ``paid_at``,
because the line moves the total; see payments.py for the three events. A refund
is recorded and lands nothing; see ``record_invoice_exception``.

The note is stored and reaches no arithmetic. The amount is a typed column.

**A TOTAL NEVER GOES BELOW ZERO.** An adjustment that would take the invoice
total below zero is refused by name, computed under the invoice lock from the
committed lines; exactly zero is allowed -- that is a fully waived invoice, and
it reads as paid with no payment, which is what waiving means. There is NO
database backstop for this: a floor across rows is not a CHECK, so the lock is
the whole guard and a raw insert as the application role can still take a total
negative. Said here and in the contract rather than implied.

**AN INVOICE EXCEPTION RUNS UNDER THE INVOICE LOCK**, monetary or not, so the
adjustment line and the derivation that follows it land against committed rows
and never beside a payment being recorded at the same instant.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .exceptions_by_owner import LANDS_A_LINE, ExceptionKind, OwnerException
from .findings import REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL, Refused
from .invoice import LineKind
from .payments import PaidState, invoice_uuid_for, paid_state, rederive_paid_at
from .store.postgres import lock_invoice, tenant
from .store.writes import as_uuid, guarded_insert


def record_agreement_exception(
    connection: Any,
    tenant_id: Any,
    agreement_uuid: Any,
    exception: OwnerException,
) -> UUID:
    """An exception against an agreement. Returns the row's id."""
    if exception.agreement_id is None:
        raise ValueError("record_agreement_exception takes an exception attached to an agreement.")
    tenant_id, agreement_uuid = as_uuid(tenant_id), as_uuid(agreement_uuid)
    with tenant(connection, tenant_id) as cursor:
        guarded_insert(
            cursor,
            "owner_exceptions",
            {
                "tenant_id": tenant_id,
                "agreement_id": agreement_uuid,
                "kind": exception.kind.value,
                "recorded_by": exception.recorded_by,
                "recorded_at": exception.recorded_at,
                "note": exception.note,
                "amount_minor": exception.amount_minor,
                "extra_grace_days": exception.extra_grace_days,
            },
        )
        (exception_uuid,) = cursor.fetchone()
    connection.commit()
    return as_uuid(exception_uuid)


def record_invoice_exception(
    connection: Any,
    tenant_id: Any,
    exception: OwnerException,
) -> PaidState:
    """An owner's exception against an invoice, and -- for a monetary kind --
    the adjustment line it lands, and the derivation that follows it.

    A waived fee and a credit REDUCE what the payer owes on this invoice, so the
    adjustment line carries the negative of the amount (``LANDS_A_LINE``). A
    refund lands NO line: the decision and its amount are recorded, the total and
    ``paid_at`` are untouched -- a paid period is paid -- and the money going back
    is a movement for collection's records, not a billing line. The line names the
    exception (the migration insists), so a figure nobody can account for cannot
    appear. The note is stored and reaches no arithmetic.
    """
    if exception.invoice_reference is None:
        raise ValueError("record_invoice_exception takes an exception attached to an invoice.")
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, exception.invoice_reference)
        lock_invoice(cursor, invoice_uuid)
        moves_the_total = exception.kind in LANDS_A_LINE
        if moves_the_total:
            owed = paid_state(cursor, invoice_uuid)
            amount = int(exception.amount_minor or 0)
            if owed.total_minor - amount < 0:
                connection.rollback()
                raise Refused(
                    REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL,
                    f"exception {exception.id!r} would adjust "
                    f"{exception.invoice_reference!r} by {amount} against a total of "
                    f"{owed.total_minor}.",
                )
        guarded_insert(
            cursor,
            "owner_exceptions",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": exception.kind.value,
                "recorded_by": exception.recorded_by,
                "recorded_at": exception.recorded_at,
                "note": exception.note,
                "amount_minor": exception.amount_minor,
                "extra_grace_days": exception.extra_grace_days,
            },
        )
        (exception_uuid,) = cursor.fetchone()
        if exception.kind in LANDS_A_LINE:
            cursor.execute(
                "SELECT agreement_id, agreement_version, period_start_day, period_end_day "
                "FROM invoice_lines WHERE invoice_id = %s ORDER BY created_at LIMIT 1",
                (invoice_uuid,),
            )
            agreement_uuid, version, start_day, end_day = cursor.fetchone()
            guarded_insert(
                cursor,
                "invoice_lines",
                {
                    "tenant_id": tenant_id,
                    "invoice_id": invoice_uuid,
                    "kind": LineKind.EXCEPTION_ADJUSTMENT.value,
                    "label": _adjustment_label(exception),
                    "amount_minor": -int(exception.amount_minor or 0),
                    "agreement_id": agreement_uuid,
                    "agreement_version": version,
                    "period_start_day": start_day,
                    "period_end_day": end_day,
                    "exception_id": exception_uuid,
                },
            )
            cursor.fetchone()
        state = rederive_paid_at(cursor, invoice_uuid, exception.recorded_at)
    connection.commit()
    return state


def _adjustment_label(exception: OwnerException) -> str:
    words = {
        ExceptionKind.WAIVE_FEE: "Fee waived",
        ExceptionKind.CREDIT: "Credit",
    }
    return f"{words[exception.kind]} by {exception.recorded_by} (exception {exception.id})"
