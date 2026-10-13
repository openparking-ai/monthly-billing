"""The owner's exceptions, written to the store.

Two attachment points, as M1 defined them: an exception sits on an AGREEMENT
(a grace extension, a block, an unblock) or on an INVOICE (a waived fee, a
credit, a refund -- and a grace extension can sit here too). A kind that changes
what is owed lands an ``exception_adjustment`` line and re-derives ``paid_at``,
because the line moves the total; see payments.py for the three events. A refund
is recorded and lands nothing; see ``record_invoice_exception``.

The note is stored and reaches no arithmetic. The amount is a typed column.

**A TOTAL NEVER GOES BELOW ZERO.** An adjustment that would take the invoice's
PRE-TAX total below zero is refused by name, computed under the invoice lock from
the committed lines -- on an untaxed invoice that is its total, and on a taxed
one the tax follows the base, so the total reaches zero exactly when the base
does; exactly zero is allowed -- that is a fully waived invoice, and
it reads as paid with no payment, which is what waiving means. There is NO
database backstop for this: a floor across rows is not a CHECK, so the lock is
the whole guard and a raw insert as the application role can still take a total
negative. Said here and in the contract rather than implied.

**ONE WRITER OF ADJUSTMENTS, AND IT RE-TAXES.** ``record_invoice_exception`` is
the only code that writes an ``exception_adjustment`` line to an issued
invoice. Tax is the last COMPUTATION on an invoice, not the last row: the
adjustment lands after the tax lines (lines are append-only), and in the SAME
transaction this writer appends, for every rule of the set the invoice
RECORDED -- never today's set -- the compensating tax line the engine's answer
implies (``tax.retax_after_adjustment``): a negative line where a waiver
lowered the base, zero where the rounded figure did not move. A waived fee
therefore stops being taxed the moment it is waived. Migration 0006 refuses at
commit an adjustment on an invoice whose set has rules that arrives without
its tax line; that the line is the RIGHT amount is a guarantee with a control,
because the schema does not compute the percentage.

The adjustment's agreement and period are copied from a NON-TAX line of the
invoice. A tax line names no agreement, and copying one would put a NULL
agreement on an adjustment.

**AN INVOICE EXCEPTION RUNS UNDER THE INVOICE LOCK**, monetary or not, so the
adjustment line and the derivation that follows it land against committed rows
and never beside a payment being recorded at the same instant -- through
``locked_invoice``, so the refusal above, or the instrument guard, hands the
lock back with the exception it raised.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .exceptions_by_owner import LANDS_A_LINE, ExceptionKind, OwnerException
from .findings import REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL, Refused
from .invoice import Invoice, InvoiceLine, LineKind
from .payments import PaidState, invoice_uuid_for, rederive_paid_at
from .store.postgres import locked_invoice, tenant
from .store.tax_sets import load_tax_set_documents
from .store.writes import as_uuid, guarded_insert
from .tax import retax_after_adjustment


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
        with locked_invoice(connection, cursor, invoice_uuid):
            moves_the_total = exception.kind in LANDS_A_LINE
            if moves_the_total:
                pre_tax = _pre_tax_total(cursor, invoice_uuid)
                amount = int(exception.amount_minor or 0)
                if pre_tax - amount < 0:
                    raise Refused(
                        REFUSAL_ADJUSTMENT_EXCEEDS_TOTAL,
                        f"exception {exception.id!r} would adjust "
                        f"{exception.invoice_reference!r} by {amount} against a pre-tax "
                        f"total of {pre_tax}.",
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
                _append_adjustment(
                    cursor, tenant_id, invoice_uuid, exception, as_uuid(exception_uuid)
                )
            state = rederive_paid_at(cursor, invoice_uuid, exception.recorded_at)
    connection.commit()
    return state


def _pre_tax_total(cursor: Any, invoice_uuid: Any) -> int:
    """The sum of the invoice's NON-TAX lines as committed: what a tax on it is
    a percentage of, and what an adjustment may take to zero and no further."""
    cursor.execute(
        "SELECT coalesce(sum(amount_minor), 0) FROM invoice_lines "
        "WHERE invoice_id = %s AND kind <> 'tax'",
        (invoice_uuid,),
    )
    (total,) = cursor.fetchone()
    return int(total)


def _append_adjustment(
    cursor: Any,
    tenant_id: UUID,
    invoice_uuid: UUID,
    exception: OwnerException,
    exception_uuid: UUID,
) -> None:
    """THE ONLY WRITER OF AN ADJUSTMENT LINE: the adjustment, and -- on an
    invoice priced with a tax set -- the compensating tax line per rule of THAT
    set, in the caller's transaction, under the caller's invoice lock.

    The agreement is copied from a NON-TAX line. Every line an issue writes
    shares one ``created_at`` (``now()`` is transaction-stable), so ``id``
    breaks the tie: the pick is deterministic, not the planner's choice. Which
    of several non-tax agreements it lands on is still arbitrary (A1.7). The
    filter is defence in depth -- what makes an adjustment carrying a tax
    line's null agreement impossible is the database's
    ``invoice_lines_tax_names_no_agreement``."""
    cursor.execute(
        "SELECT agreement_id, agreement_version, period_start_day, period_end_day "
        "FROM invoice_lines WHERE invoice_id = %s AND kind <> 'tax' "
        "ORDER BY created_at, id LIMIT 1",
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

    invoice, garage_uuid, recorded = _invoice_as_it_stands(cursor, invoice_uuid)
    if recorded is None:
        return  # issued before migration 0006, untaxed; there is no tax to move
    compensating = retax_after_adjustment(
        invoice,
        sets=load_tax_set_documents(cursor, garage_uuid),
        recorded_effective_from=recorded,
        exception_id=str(exception_uuid),
        exception_label=exception.id,
    )
    for line in compensating:
        guarded_insert(
            cursor,
            "invoice_lines",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": LineKind.TAX.value,
                "label": line.label,
                "amount_minor": line.amount_minor,
                "agreement_id": None,
                "agreement_version": None,
                "period_start_day": line.period_start_day,
                "period_end_day": line.period_end_day,
                "exception_id": exception_uuid,
                "tax_rule_id": line.tax_rule_id,
            },
        )
        cursor.fetchone()


def _invoice_as_it_stands(cursor: Any, invoice_uuid: UUID) -> tuple[Invoice, UUID, Any]:
    """Every line of the invoice as committed in this transaction, as an engine
    value; the garage it belongs to; and the tax set it recorded (``None`` on
    an invoice issued untaxed, before migration 0006)."""
    cursor.execute(
        "SELECT p.external_id, g.external_id, i.garage_id, i.currency, i.period_start_day, "
        "i.tax_set_effective_from "
        "FROM invoices i JOIN payers p ON p.id = i.payer_id JOIN garages g ON g.id = i.garage_id "
        "WHERE i.id = %s",
        (invoice_uuid,),
    )
    payer, garage, garage_uuid, currency, period_start, recorded = cursor.fetchone()
    cursor.execute(
        "SELECT kind, label, amount_minor, agreement_id, agreement_version, period_start_day, "
        "period_end_day, tax_rule_id FROM invoice_lines WHERE invoice_id = %s "
        "ORDER BY created_at",
        (invoice_uuid,),
    )
    lines = tuple(
        InvoiceLine(
            kind=LineKind(kind),
            label=label,
            amount_minor=int(amount),
            agreement_id=None if agreement is None else str(agreement),
            agreement_version=version,
            period_start_day=start,
            period_end_day=end,
            tax_rule_id=rule,
        )
        for kind, label, amount, agreement, version, start, end, rule in cursor.fetchall()
    )
    invoice = Invoice(
        payer_id=payer,
        garage_id=garage,
        currency=currency.strip(),
        issued_for_period_start=period_start,
        lines=lines,
    )
    return invoice, as_uuid(garage_uuid), recorded


def _adjustment_label(exception: OwnerException) -> str:
    words = {
        ExceptionKind.WAIVE_FEE: "Fee waived",
        ExceptionKind.CREDIT: "Credit",
    }
    return f"{words[exception.kind]} by {exception.recorded_by} (exception {exception.id})"
