"""The billing run: issue one period's invoices to every payer at a garage, once.

**IDEMPOTENT BY CONSTRAINT, NOT BY CONVENTION.** The run does not check whether
it has already issued a period and then decide not to; it tries to issue, and
the database refuses a second invoice for the same tenant, garage, payer and
period (migration 0002). The run catches that refusal and reports it per payer.
A run that remembered what it had done would be right until the day two ran at
once, and then it would issue twice and remember once. The refusal is read BY
CONSTRAINT NAME: only the period lock means "already issued"; any other unique
violation is reported as what it is, because "this period was already invoiced"
said about a reference collision is a false sentence in a report somebody acts on.

**ONE TRANSACTION PER PAYER.** A payer's invoice and its lines land together or
not at all, and one payer's refusal -- a currency the module will not price, an
agreement at the wrong garage -- does not stop the payer after it. Every payer
gets a line in the report and the command exits non-zero if any was refused.

**A PERIOD IS OWNED BY EXACTLY ONE OF ``first_charge`` AND THIS RUN.** The first
charge, taken on the binding day, covers the period containing the agreement's
start day AND the one after it -- two lines, or five with fees, but two periods.
The run must not price either of those periods for that agreement again, so
``owned_by_first_charge`` decides, by PERIOD and never by line count, which path
owns what. An agreement whose first charge owns the period contributes nothing
to the run's invoice for it; an agreement starting later than that contributes
its full period. Both halves are proven by a control that plants the rule the
other way round.

**`due_at` IS THE PERIOD'S START INSTANT.** The billing day, garage-local, at the
start of the period being paid for -- monthly parking is paid in advance, and
the grace period counts from there. ``BillingPeriod`` already carries the instant
resolved in the garage's zone, so a spring-forward billing day is due at the
right moment rather than 24 fixed hours after the previous one.

**THE REFERENCE IS DERIVED, SO THE SAME INVOICE ALWAYS GETS THE SAME ONE.**
Garage, period and payer -- the same three things the UNIQUE constraint holds --
so ``UNIQUE (tenant_id, reference)`` from migration 0001 is a second lock on the
same fact rather than a different one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .agreement import Agreement, InvalidAgreement
from .currency import UnpriceableCurrency
from .cycle import BillingPeriod, next_period_after, period_containing
from .findings import Refused
from .garage import Garage
from .invoice import Invoice, invoice_for_period
from .money import NotMinorUnits
from .store.postgres import tenant
from .store.records import (
    GarageNotFound,
    load_agreements_at_garage,
    load_garage,
    load_payers_at_garage,
)
from .store.writes import as_uuid, guarded_insert


class RunOutcome(Enum):
    """What the run did for one payer. Every payer gets exactly one."""

    ISSUED = "issued"
    ALREADY_ISSUED = "already_issued"
    NOTHING_BILLABLE = "nothing_billable"
    REFUSED = "refused"
    CONSTRAINT_VIOLATED = "constraint_violated"


#: The one constraint whose refusal MEANS "already issued". Any other unique
#: violation under the insert is a different fact and is reported as one, by
#: name -- a reference collision is not a period that was invoiced.
PERIOD_LOCK = "invoices_one_per_payer_per_period"

#: The outcomes that make the command exit non-zero: the payer was not issued and
#: it was not because nothing was owed or because it already had been.
FAILED_OUTCOMES: frozenset[RunOutcome] = frozenset(
    {RunOutcome.REFUSED, RunOutcome.CONSTRAINT_VIOLATED}
)


#: What each outcome means to the operator reading the report. The contract
#: document is generated from this, so an outcome added without a sentence is
#: caught by the test that walks the enum.
RUN_OUTCOME_MEANS: dict[RunOutcome, str] = {
    RunOutcome.ISSUED: "The invoice and its lines were written, in one transaction.",
    RunOutcome.ALREADY_ISSUED: (
        "This period was already invoiced for this payer. Nothing was issued and "
        "nothing was re-priced; the database refused the duplicate and the run reports it."
    ),
    RunOutcome.NOTHING_BILLABLE: (
        "No agreement of this payer's has a billable day in the period -- paused, "
        "cancelled, not started, or owned by the first charge -- so no invoice exists."
    ),
    RunOutcome.REFUSED: (
        "The module could not price this payer and says why. The payers after it "
        "were still processed; the command exits non-zero."
    ),
    RunOutcome.CONSTRAINT_VIOLATED: (
        "The database refused this payer's invoice on a constraint OTHER than the "
        "one-invoice-per-period lock, and the line names it. Nothing was issued for "
        "this payer and nothing is claimed about the period; the payers after it "
        "were still processed; the command exits non-zero."
    ),
}


@dataclass(frozen=True)
class RunLine:
    payer_id: str
    outcome: RunOutcome
    detail: str
    reference: str | None = None
    total_minor: int | None = None


@dataclass(frozen=True)
class RunReport:
    garage_id: str
    period: BillingPeriod
    lines: tuple[RunLine, ...]

    @property
    def refused(self) -> bool:
        """Whether any payer failed -- refused, or stopped by a constraint that is
        not the period lock. The command's non-zero exit reads this."""
        return any(line.outcome in FAILED_OUTCOMES for line in self.lines)

    def rendered(self) -> str:
        head = (
            f"Billing run for garage {self.garage_id}, period "
            f"{self.period.start_day} to {self.period.end_day}"
        )
        body = "\n".join(
            f"  {line.payer_id}: {line.outcome.value.upper()} — {line.detail}"
            for line in self.lines
        )
        return f"{head}\n{body}" if body else f"{head}\n  no payer has an agreement here"


def invoice_reference(garage: Garage, period: BillingPeriod, payer_id: str) -> str:
    """Deterministic. The same garage, period and payer always get this one."""
    return f"{garage.id}/{period.start_day.isoformat()}/{payer_id}"


def owned_by_first_charge(garage: Garage, agreement: Agreement, period: BillingPeriod) -> bool:
    """Whether ``period`` is one of the two the first charge already covered.

    The first charge takes the period containing the start day and the one after
    it. This compares PERIODS -- by their start day, which identifies a period at
    a garage -- and never counts lines, because a first charge with fees emits
    five lines and one without emits two, and both cover exactly two periods.
    """
    partial = period_containing(garage, agreement.start_day)
    following = next_period_after(garage, partial)
    return period.start_day in (partial.start_day, following.start_day)


def run_billing(
    connection: Any,
    tenant_id: Any,
    garage_id: str,
    period_containing_day: date,
    *,
    now: datetime,
) -> RunReport:
    """Issue the period's invoices to every payer at the garage. See the module
    docstring for the four rules this enforces.

    ``connection`` is a driver connection as the application role. Each payer is
    committed or rolled back on it separately, and the tenant context is set
    again for every transaction, because a commit ends it.
    """
    import psycopg  # the store extra; the engine never imports this module

    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        stored = load_garage(cursor, garage_id)
        if stored is None:
            raise GarageNotFound(f"no garage with id {garage_id!r} in the store.")
        garage = stored.garage
        period = period_containing(garage, period_containing_day)
        payers = load_payers_at_garage(cursor, stored.uuid)
    connection.rollback()  # a read; the write transactions below each set their own context

    lines: list[RunLine] = []
    for payer_uuid, payer_id in payers:
        # Everything that can refuse for THIS payer -- a stored agreement the
        # engine's constructor will not accept, a currency it will not price --
        # is inside this boundary, so the payer after it still gets its turn.
        try:
            with tenant(connection, tenant_id) as cursor:
                items = load_agreements_at_garage(cursor, stored.uuid, payer_uuid)
            connection.rollback()
            priced = tuple(
                item.agreement
                for item in items
                if not owned_by_first_charge(garage, item.agreement, period)
            )
            invoice = invoice_for_period(garage, priced, period, payer_id)
        except (Refused, InvalidAgreement, NotMinorUnits, UnpriceableCurrency) as refusal:
            connection.rollback()
            lines.append(RunLine(payer_id, RunOutcome.REFUSED, str(refusal)))
            continue

        if not invoice.lines:
            skipped = len(items) - len(priced)
            lines.append(
                RunLine(
                    payer_id,
                    RunOutcome.NOTHING_BILLABLE,
                    (
                        f"{len(items)} agreement(s), no billable day in this period"
                        + (f"; {skipped} owned by the first charge" if skipped else "")
                    ),
                )
            )
            continue

        reference = invoice_reference(garage, period, payer_id)
        try:
            with tenant(connection, tenant_id) as cursor:
                _persist(
                    cursor,
                    tenant_id,
                    invoice,
                    reference=reference,
                    garage_uuid=stored.uuid,
                    payer_uuid=payer_uuid,
                    agreement_uuids={item.agreement.id: item.uuid for item in items},
                    due_at=period.start_instant,
                    now=now,
                )
            connection.commit()
        except psycopg.errors.UniqueViolation as violation:
            connection.rollback()
            constraint = violation.diag.constraint_name
            # A genuine duplicate violates BOTH locks, and the database names
            # whichever index it checked first -- the 0001 reference lock, as
            # measured. So the name alone does not settle it: the period lock's
            # own fact is read back. Already issued means a row for this garage,
            # payer and period EXISTS; anything else is the other constraint.
            with tenant(connection, tenant_id) as cursor:
                issued = _period_is_issued(cursor, stored.uuid, payer_uuid, period.start_day)
            connection.rollback()
            if constraint == PERIOD_LOCK or issued:
                lines.append(
                    RunLine(
                        payer_id,
                        RunOutcome.ALREADY_ISSUED,
                        "this period was already invoiced; nothing issued and nothing re-priced",
                        reference=reference,
                    )
                )
            else:
                lines.append(
                    RunLine(
                        payer_id,
                        RunOutcome.CONSTRAINT_VIOLATED,
                        f"the database refused the invoice on {constraint!r}; nothing issued, "
                        "and this period is NOT known to be invoiced",
                        reference=reference,
                    )
                )
            continue

        lines.append(
            RunLine(
                payer_id,
                RunOutcome.ISSUED,
                f"{len(invoice.lines)} line(s), total {invoice.total_minor} "
                f"{invoice.currency} minor",
                reference=reference,
                total_minor=invoice.total_minor,
            )
        )

    return RunReport(garage_id=garage.id, period=period, lines=tuple(lines))


def _period_is_issued(cursor: Any, garage_uuid: Any, payer_uuid: Any, start_day: date) -> bool:
    """The fact the period lock guards, read directly."""
    cursor.execute(
        "SELECT 1 FROM invoices WHERE garage_id = %s AND payer_id = %s AND period_start_day = %s",
        (garage_uuid, payer_uuid, start_day),
    )
    return cursor.fetchone() is not None


def _persist(
    cursor: Any,
    tenant_id: Any,
    invoice: Invoice,
    *,
    reference: str,
    garage_uuid: Any,
    payer_uuid: Any,
    agreement_uuids: dict[str, Any],
    due_at: datetime,
    now: datetime,
) -> Any:
    """The invoice row and its lines, through the chokepoint, in the caller's
    transaction."""
    guarded_insert(
        cursor,
        "invoices",
        {
            "tenant_id": tenant_id,
            "reference": reference,
            "payer_id": payer_uuid,
            "garage_id": garage_uuid,
            "currency": invoice.currency,
            "period_start_day": invoice.issued_for_period_start,
            "issued_at": now,
            "due_at": due_at,
        },
    )
    (invoice_uuid,) = cursor.fetchone()
    invoice_uuid = as_uuid(invoice_uuid)
    for line in invoice.lines:
        guarded_insert(
            cursor,
            "invoice_lines",
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_uuid,
                "kind": line.kind.value,
                "label": line.label,
                "amount_minor": line.amount_minor,
                "agreement_id": agreement_uuids[line.agreement_id],
                "agreement_version": line.agreement_version,
                "period_start_day": line.period_start_day,
                "period_end_day": line.period_end_day,
                "exception_id": line.exception_id,
            },
        )
    return invoice_uuid
