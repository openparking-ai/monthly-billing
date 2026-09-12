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

So a charge is two transactions with a PERSISTED RESERVATION between them,
and every transaction that touches the invoice's money runs inside
``store.postgres.locked_invoice`` -- the invoice row lock, with a rollback on
any exception:

* **T1, under the lock:** refuse by name if an attempt on this invoice is still
  pending with a request possibly in flight; rebuild the retry state from the
  outcome rows; recompute the balance; refuse no-mandate, retries-exhausted and
  nothing-owed exactly as before; then write the ``attempt`` row -- a fresh
  ``attempt_id``, the amount and currency RESERVED -- and commit. The processor
  has not been called.
* **The processor, outside any lock,** handed the attempt id as its idempotency
  key.
* **T2, under the lock:** write the ``outcome`` row naming the attempt, and on
  success the card payment for the amount RESERVED -- in the same transaction --
  and re-derive ``paid_at``. There is no instant at which a success is recorded
  and its money is not.

**WHAT THE MODULE DOES NOT KNOW IS NEVER WRITTEN AS AN OUTCOME.** The second
outside round ran the shape above with a processor that charged and then
raised, and with one that answered SUCCESS with a reference the instrument
guard refused: both were written as a resolved ``error`` outcome, the attempt
stopped being pending, and the next charge reserved a FRESH key and asked again
-- twice charged. So there are three things the module can know after calling
the processor: a result (success, decline, error), or NOTHING -- the processor
raised, before or after the request left, or its answer could not be received.
The second kind is an ``UnknownAnswer`` (``payment.ask_processor``): never an
``outcome`` row, never a ``ChargeResult``, never folded into the retry count.
It is written as an ``unknown`` row saying what the module saw, and the attempt
stays PENDING.

**A PENDING ATTEMPT IS RESUMED, NEVER CHARGED PAST.** Pending is an ``attempt``
row with no ``outcome`` row. In T1, under the lock:

* pending and its last row is ``attempt`` or ``ask`` -- a request may be in
  flight, or a worker died with it: refuse by name, ``REFUSAL_ATTEMPT_UNRESOLVED``,
  naming the attempt id. The operator's ``resolve_attempt`` is the way out.
* pending and its last row is ``unknown`` -- the module already said it does
  not know: write an ``ask`` row, commit, and ask the processor again with the
  SAME attempt id as the idempotency key and the RESERVED amount and currency.
  No new reservation, no fresh key, the three refusals not re-run -- this is
  not a new charge; the request already left once, and a method change since
  the reservation does not alter what is outstanding under that key. A real
  processor honours the key, so a repeated request charges once. There is no
  cap on re-asks: a processor that never answers grows the ``unknown`` log for
  as long as the scheduler calls, and one key is one charge at most.
* no pending attempt -- reserve as above.

A worker resuming and an operator resolving at once: the first to write the
outcome row under the lock wins; the second is ``REFUSAL_ATTEMPT_ALREADY_RESOLVED``
-- from the check under the lock, and from the database's one-outcome-per-attempt
index caught by name as the backstop. Per attempt the log reads ``attempt,
[unknown, ask, unknown, ask ...], outcome``; an unknown that lands after the
outcome -- a worker's re-ask returning after the operator resolved -- is
recorded as the fact it is, and the outcome stands.

**A CHEQUE BETWEEN T1 AND T2 DOES NOT DROP THE CARD PAYMENT.** The processor
moved the money the reservation asked for; T2 records it for the reserved
amount, and the invoice reads OVERPAID (``PaidState.overpaid_minor``). Never
silently, never dropped -- and the operator's refund is the owner's exception.

**``charge_attempts`` IS THE TRUTH FOR RETRIES, IN THE ORDER IT WAS RECORDED.**
The retry state is the fold of the OUTCOME rows through M1's own
``after_attempt``, walked in the log's recorded order -- the ``sequence`` the
database assigned under the lock, never the caller's ``occurred_at`` -- and a
non-success outcome counts toward the method that was current when its
RESERVATION was made: it counts iff its ``attempt`` row is after the last
``payment_method_changed`` row. A pending attempt counts as neither, an
unknown counts as nothing, and a success counts toward nothing and resets
nothing -- the reset is the caller's report of a new payment method, which is
allowed while an attempt is pending because it moves no money, and is written
under the lock so that its place in the log is its place in time.

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
    UnknownAnswer,
    ask_processor,
    refuse_unless_chargeable,
)
from .payments import (
    PaidState,
    PaymentMethod,
    insert_payment,
    invoice_uuid_for,
    paid_state,
    rederive_paid_at,
)
from .store.postgres import locked_invoice, tenant
from .store.records import load_agreements_at_garage
from .store.writes import as_uuid, guarded_insert

#: The index whose violation MEANS "this attempt already has an outcome"
#: (migration 0003). The module refuses by name under the lock before it
#: fires; this is the backstop for a raw insert or a check bypassed in-process.
ONE_OUTCOME_PER_ATTEMPT = "charge_attempts_one_row_per_kind_per_attempt"

#: The two words a pending attempt's state can be, on the command line.
IN_FLIGHT = "in flight"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChargeOutcome:
    """What one call to ``attempt_charge`` (or ``resolve_attempt``) did.

    ``result`` is the processor's ``ChargeResult`` -- or an ``UnknownAnswer``,
    a different type, when the module does not know what the processor did:
    then ``payment_id`` and ``paid`` are None, the attempt is still PENDING,
    and ``retry`` is the state as it stood, unchanged by the ask.
    """

    result: ChargeResult | UnknownAnswer
    retry: RetryState
    attempt_id: UUID
    payment_id: UUID | None
    paid: PaidState | None

    @property
    def unknown(self) -> bool:
        """True when the processor's answer did not arrive and the attempt is
        left pending for the next call to ask again under the same key."""
        return isinstance(self.result, UnknownAnswer)


@dataclass(frozen=True)
class PendingAttempt:
    """A reservation with no outcome row, as the operator reads it."""

    attempt_id: UUID
    amount_minor: int
    currency: str
    #: ``IN_FLIGHT`` when the last row is the reservation or an ask -- a request
    #: may be in flight, or a worker died with it -- and ``UNKNOWN`` when the
    #: last row says the module does not know.
    state: str
    #: What the module saw last: the last ``unknown`` row's detail, or empty.
    last_detail: str
    #: How many times the processor has been asked again under this key.
    asks: int


class AttemptNotFound(LookupError):
    """The id names no pending attempt in the store."""


def retry_state_from_rows(
    invoice_reference: str,
    rows: list[tuple[str, str | None, str]] | list[tuple[str, str | None, str, Any]],
) -> RetryState:
    """Fold the log into M1's ``RetryState``. ``rows`` are in RECORDED order.

    Two row shapes, and the shape says how an outcome is dated:

    * ``(kind, outcome, detail)`` -- an outcome counts WHERE IT SITS: after the
      last ``payment_method_changed`` row it counts, before it it does not.
    * ``(kind, outcome, detail, attempt_id)`` -- the store's own shape: an
      outcome counts iff its ATTEMPT row -- its reservation -- sits after the
      last ``payment_method_changed`` row. A decline that was reserved under the
      old card and resolved after the operator changed it counts against the
      old card, whenever the operator typed its instant.

    Only ``outcome`` rows count. A ``payment_method_changed`` row resets --
    through the same method the caller used to call. ``attempt``, ``unknown``
    and ``ask`` rows are reservations and asks, not results, and contribute
    nothing.
    """
    state = RetryState(invoice_reference=invoice_reference)
    reserved_since_change: set[Any] | None = None
    for row in rows:
        kind, outcome, detail = row[0], row[1], row[2]
        attempt_id = row[3] if len(row) > 3 else None
        if kind == "payment_method_changed":
            state = state.record_payment_method_changed()
            reserved_since_change = set()
        elif kind == "attempt" and attempt_id is not None:
            if reserved_since_change is None:
                reserved_since_change = set()
            reserved_since_change.add(attempt_id)
        elif kind == "outcome":
            if attempt_id is not None and reserved_since_change is not None:
                if attempt_id not in reserved_since_change:
                    continue  # reserved under the method the change retired
            state = state.after_attempt(ChargeResult(outcome=Outcome(outcome), detail=detail))
    return state


def _load_attempt_rows(cursor: Any, invoice_uuid: Any) -> list[tuple[str, str | None, str, Any]]:
    """The log in RECORDED order, by the sequence the database assigned under
    the lock -- never by ``occurred_at``, which is whatever the caller typed."""
    cursor.execute(
        "SELECT kind, outcome, detail, attempt_id FROM charge_attempts WHERE invoice_id = %s "
        "ORDER BY sequence",
        (invoice_uuid,),
    )
    return [
        (kind, outcome, detail, None if attempt_id is None else as_uuid(attempt_id))
        for kind, outcome, detail, attempt_id in cursor.fetchall()
    ]


_PENDING_SQL = """
    SELECT a.attempt_id, a.amount_minor, a.currency,
           last.kind, last.detail,
           (SELECT count(*) FROM charge_attempts k
             WHERE k.attempt_id = a.attempt_id AND k.kind = 'ask')
    FROM charge_attempts a
    JOIN LATERAL (SELECT r.kind, r.detail FROM charge_attempts r
                   WHERE r.attempt_id = a.attempt_id
                   ORDER BY r.sequence DESC LIMIT 1) AS last ON true
    WHERE a.invoice_id = %s AND a.kind = 'attempt'
      AND NOT EXISTS (SELECT 1 FROM charge_attempts o
                      WHERE o.attempt_id = a.attempt_id AND o.kind = 'outcome')
    ORDER BY a.sequence
"""


def _pending_rows(cursor: Any, invoice_uuid: Any) -> list[PendingAttempt]:
    cursor.execute(_PENDING_SQL, (invoice_uuid,))
    return [
        PendingAttempt(
            attempt_id=as_uuid(attempt_id),
            amount_minor=int(amount),
            currency=currency.strip(),
            state=UNKNOWN if last_kind == "unknown" else IN_FLIGHT,
            last_detail=last_detail if last_kind == "unknown" else "",
            asks=int(asks),
        )
        for attempt_id, amount, currency, last_kind, last_detail, asks in cursor.fetchall()
    ]


def _pending_attempt(cursor: Any, invoice_uuid: Any) -> PendingAttempt | None:
    """The oldest reservation with no outcome row, if any, with its state."""
    pending = _pending_rows(cursor, invoice_uuid)
    return pending[0] if pending else None


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
    currency: str
    retry: RetryState
    #: True when T1 found a pending attempt whose last word was "unknown" and
    #: wrote an ``ask`` row instead of a new reservation.
    resumed: bool


def _log_row(
    cursor: Any,
    tenant_id: UUID,
    invoice_uuid: UUID,
    kind: str,
    attempt_id: UUID,
    detail: str,
    *,
    recorded_by: str,
    now: datetime,
) -> None:
    """One ``unknown`` or ``ask`` row: names its attempt, carries no money and
    no outcome."""
    guarded_insert(
        cursor,
        "charge_attempts",
        {
            "tenant_id": tenant_id,
            "invoice_id": invoice_uuid,
            "kind": kind,
            "attempt_id": attempt_id,
            "outcome": None,
            "detail": detail,
            "occurred_at": now,
            "recorded_by": recorded_by,
        },
    )
    cursor.fetchone()


def _reserve(
    connection: Any, tenant_id: UUID, invoice_reference: str, *, recorded_by: str, now: datetime
) -> _Reservation:
    """T1. Under the lock: refuse by name, resume a pending attempt the module
    already said it does not know about, or write a fresh reservation. Commit."""
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        with locked_invoice(connection, cursor, invoice_uuid):
            invoice, garage_uuid = _load_invoice(cursor, invoice_uuid)
            retry = retry_state_from_rows(
                invoice_reference, _load_attempt_rows(cursor, invoice_uuid)
            )
            pending = _pending_attempt(cursor, invoice_uuid)
            if pending is not None and pending.state != UNKNOWN:
                raise Refused(
                    REFUSAL_ATTEMPT_UNRESOLVED,
                    f"invoice {invoice_reference!r} has a pending attempt "
                    f"{pending.attempt_id} whose request may be in flight; "
                    "resolve it first (resolve-attempt), or list it (pending-attempts).",
                )
            if pending is not None:
                # The module already said it does not know: ask again under the
                # SAME key for what was RESERVED. Not a new charge, so the three
                # refusals are not re-run and nothing new is reserved.
                _log_row(
                    cursor, tenant_id, invoice_uuid, "ask", pending.attempt_id,
                    f"re-ask {pending.asks + 1} under the same key",
                    recorded_by=recorded_by, now=now,
                )
                reservation = _Reservation(
                    pending.attempt_id, invoice_uuid, invoice, pending.amount_minor,
                    pending.currency, retry, resumed=True,
                )
            else:
                on_invoice = {line.agreement_id for line in invoice.lines}
                agreements = [
                    item.agreement
                    for item in load_agreements_at_garage(cursor, garage_uuid)
                    if item.agreement.id in on_invoice
                ]
                owed = paid_state(cursor, invoice_uuid)
                # The mandate rule, for every agreement the invoice itemises: the
                # first one without a mandate is the one the refusal names.
                gate = next((a for a in agreements if a.mandate is None), agreements[0])
                balance = owed.total_minor - owed.paid_minor
                refuse_unless_chargeable(gate, invoice, retry, balance)
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
                reservation = _Reservation(
                    attempt_id, invoice_uuid, invoice, balance, invoice.currency, retry,
                    resumed=False,
                )
    connection.commit()
    return reservation


def _record_unknown(
    connection: Any,
    tenant_id: UUID,
    invoice_uuid: UUID,
    attempt_id: UUID,
    answer: UnknownAnswer,
    *,
    recorded_by: str,
    now: datetime,
) -> RetryState:
    """The processor's answer did not arrive: an ``unknown`` row, under the
    lock, and the attempt stays pending. No outcome, no payment, no count."""
    with tenant(connection, tenant_id) as cursor:
        with locked_invoice(connection, cursor, invoice_uuid):
            _log_row(
                cursor, tenant_id, invoice_uuid, "unknown", attempt_id, answer.detail,
                recorded_by=recorded_by, now=now,
            )
            retry = retry_state_from_rows(
                _reference_of(cursor, invoice_uuid), _load_attempt_rows(cursor, invoice_uuid)
            )
    connection.commit()
    return retry


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
    import psycopg  # the store extra; the engine never imports this module

    with tenant(connection, tenant_id) as cursor:
        with locked_invoice(connection, cursor, invoice_uuid):
            cursor.execute(
                "SELECT amount_minor, invoice_id FROM charge_attempts "
                "WHERE attempt_id = %s AND kind = 'attempt'",
                (attempt_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise AttemptNotFound(f"no attempt with id {attempt_id!r}.")
            reserved, reserved_invoice = int(row[0]), as_uuid(row[1])
            if reserved_invoice != invoice_uuid:
                raise AttemptNotFound(f"attempt {attempt_id!r} is not on invoice {invoice_uuid!r}.")
            cursor.execute(
                "SELECT 1 FROM charge_attempts WHERE attempt_id = %s AND kind = 'outcome'",
                (attempt_id,),
            )
            if cursor.fetchone() is not None:
                raise Refused(
                    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
                    f"attempt {attempt_id!r} already has an outcome recorded.",
                )
            try:
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
            except psycopg.errors.UniqueViolation as violation:
                # The backstop, read BY INDEX NAME: only the one-outcome-per-
                # attempt index means "already resolved". Anything else is
                # what it is.
                if violation.diag.constraint_name != ONE_OUTCOME_PER_ATTEMPT:
                    raise
                raise Refused(
                    REFUSAL_ATTEMPT_ALREADY_RESOLVED,
                    f"attempt {attempt_id!r} already has an outcome recorded.",
                ) from None
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
    processor is called; the outcome and its payment land together; an answer
    that does not arrive leaves the attempt pending and is asked again, under
    the same key, by the next call."""
    tenant_id = as_uuid(tenant_id)
    reservation = _reserve(
        connection, tenant_id, invoice_reference, recorded_by=recorded_by, now=now
    )

    answer = ask_processor(
        processor,
        ChargeRequest(
            payer_id=reservation.invoice.payer_id,
            amount_minor=reservation.amount_minor,
            currency=reservation.currency,
            invoice_reference=invoice_reference,
            idempotency_key=reservation.attempt_id,
        ),
    )

    if isinstance(answer, UnknownAnswer):
        retry = _record_unknown(
            connection, tenant_id, reservation.invoice_uuid, reservation.attempt_id, answer,
            recorded_by=recorded_by, now=now,
        )
        return ChargeOutcome(
            result=answer, retry=retry, attempt_id=reservation.attempt_id,
            payment_id=None, paid=None,
        )

    retry, payment_id, paid = _settle(
        connection, tenant_id, reservation.invoice_uuid, reservation.attempt_id, answer,
        recorded_by=recorded_by, now=now,
    )
    return ChargeOutcome(
        result=answer, retry=retry, attempt_id=reservation.attempt_id,
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
    """The ids of the reservations on an invoice with no outcome row, oldest
    first. ``list_pending_attempts`` is the same read with the operator's
    detail."""
    pending = list_pending_attempts(connection, tenant_id, invoice_reference)
    return tuple(p.attempt_id for p in pending)


def list_pending_attempts(
    connection: Any, tenant_id: Any, invoice_reference: str
) -> tuple[PendingAttempt, ...]:
    """Every pending attempt on an invoice as the operator needs to see it: the
    id ``resolve-attempt`` takes, what was reserved, whether a request may be in
    flight or the module has said it does not know, and what it saw last."""
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        pending = tuple(_pending_rows(cursor, invoice_uuid))
    connection.rollback()
    return pending


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
    changing a card while collection is stuck must not be refused. Written
    under the invoice lock all the same, so that its place in the log's
    recorded order is its place in time against the reservations around it --
    the fold counts an outcome toward the method current at its reservation.
    """
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        invoice_uuid = invoice_uuid_for(cursor, invoice_reference)
        with locked_invoice(connection, cursor, invoice_uuid):
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
            state = retry_state_from_rows(
                invoice_reference, _load_attempt_rows(cursor, invoice_uuid)
            )
    connection.commit()
    return state


__all__ = [
    "IN_FLIGHT",
    "ONE_OUTCOME_PER_ATTEMPT",
    "UNKNOWN",
    "AttemptNotFound",
    "ChargeOutcome",
    "PendingAttempt",
    "attempt_charge",
    "list_pending_attempts",
    "pending_attempts",
    "record_payment_method_changed",
    "resolve_attempt",
    "retry_state_from_rows",
]
