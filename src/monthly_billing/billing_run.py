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
And a payer the run cannot price again TODAY -- no tax set in force at the due
instant, as for an invoice issued before migration 0006, or no engine -- is not
refused for a period already invoiced: the same fact is read back after the
refusal, and a period with its row is ALREADY_ISSUED. A re-run must give the
first run's answer, and "refused" about an invoiced period is not that answer.

**ONE TRANSACTION PER PAYER.** A payer's invoice and its lines land together or
not at all, and one payer's refusal -- a currency the module will not price, an
agreement at the wrong garage, a row the instrument guard refuses -- does not
stop the payer after it. Every payer gets a line in the report and the command
exits non-zero if any was refused.

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

**EVERY INVOICE IS TAXED, AT ITS `due_at`, BY THE ENGINE, IN THE PAYER'S OWN
TRANSACTION.** The garage's tax sets are read INSIDE the transaction that writes
the payer's invoice -- one transaction per payer is this module's unit of
consistency -- so a set stated while the run is going cannot price two payers of
one run differently without ``tax_sets_seen`` on their rows saying so. The
moment is ``due_at``, never the clock: a re-run must give the first run's answer,
and a figure that depended on when the command was typed would not. A garage
with no set in force at ``due_at`` -- including one that has stated none -- is
REFUSED for that payer, by name, in the engine's words; it is never taxed at
zero. The invoice row records the pre-tax subtotal, the set that priced it and
how many sets the transaction saw, and the database holds the subtotal to the
issued lines at commit (migration 0006).

**THE REFERENCE IS DERIVED, SO THE SAME INVOICE ALWAYS GETS THE SAME ONE.**
Garage, period and payer -- the same three things the UNIQUE constraint holds --
so ``UNIQUE (tenant_id, reference)`` from migration 0001 is a second lock on the
same fact rather than a different one. The instrument guard scans it like every
column and is not loosened for it: a garage id ending in nine digits that pass
the routing checksum makes every reference at that garage read as a routing
number followed by an account number, so the guard refuses each of its payers'
invoices, and each is that payer's REFUSED line -- not a stop of the run.

**A ZERO TOTAL IS PAID AT ISSUE.** Paid is derived (payments.py), and the issue
is where it is first true for an invoice that owes nothing: ``_persist`` derives
``paid_at`` in the transaction that writes the invoice, so a comped agreement's
invoice is paid from its issue instant rather than unpaid for ever -- a payment
of nothing and a waiver of nothing are both refused, so nothing else could pay
it. Any other total is unpaid at issue and the derivation writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from .agreement import Agreement, InvalidAgreement
from .currency import UnpriceableCurrency
from .cycle import BillingPeriod, next_period_after, period_containing
from .findings import (
    REFUSAL_FIRST_CHARGE_DAY_ISSUED,
    REFUSAL_FIRST_CHARGE_PAST_ITS_PERIODS,
    Refused,
)
from .garage import Garage
from .invoice import Invoice, invoice_for_period
from .localday import day_of, zone
from .money import NotMinorUnits
from .payments import rederive_paid_at
from .sensitive import InstrumentLike
from .store.postgres import tenant
from .store.records import (
    GarageNotFound,
    load_agreements_at_garage,
    load_garage,
    load_payers_at_garage,
)
from .store.tax_sets import load_tax_set_documents
from .store.writes import as_uuid, guarded_insert
from .tax import TaxPosition, tax_an_invoice


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
        "nothing was re-priced; the database refused the duplicate, or -- when the "
        "period could not be priced again today, as with no tax set in force at its "
        "due instant -- the run read the period's row and reports it, not a refusal."
    ),
    RunOutcome.NOTHING_BILLABLE: (
        "No agreement of this payer's has a billable day in the period -- paused, "
        "cancelled, not started, or owned by the first charge -- so no invoice exists."
    ),
    RunOutcome.REFUSED: (
        "The module could not price or store this payer's invoice and says why -- "
        "among the reasons, no tax set in force at the invoice's due instant, which "
        "is never taxed at zero, and a row the instrument guard refuses. The payers "
        "after it were still processed; the command exits non-zero."
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


def owned_by_first_charge(
    garage: Garage,
    agreement: Agreement,
    period: BillingPeriod,
    stored: tuple[date, date] | None = None,
) -> bool:
    """Whether ``period`` is one of the two the first charge already covered.

    The first charge takes the period containing the start day and the one after
    it. This compares PERIODS -- by their start day, which identifies a period at
    a garage -- and never counts lines, because a first charge with fees emits
    five lines and one without emits two, and both cover exactly two periods.

    ``stored`` is the two period start days a STORED first charge owns
    (``first_charges``, migration 0010). When given they decide, never the
    latest version's start day: a version that moved the start day must not
    move the periods a first charge was issued for (OP-7-3).
    """
    if stored is not None:
        return period.start_day in stored
    partial = period_containing(garage, agreement.start_day)
    following = next_period_after(garage, partial)
    return period.start_day in (partial.start_day, following.start_day)


def stored_first_charges(cursor: Any, garage_uuid: Any) -> dict[str, tuple[date, date]]:
    """The two owned period start days of every first charge stored at a home,
    by agreement id."""
    cursor.execute(
        "SELECT agreement_external_id, first_period_start_day, second_period_start_day "
        "FROM first_charges WHERE garage_id = %s",
        (as_uuid(garage_uuid),),
    )
    return {agreement: (first, second) for agreement, first, second in cursor.fetchall()}


def _one_time_fees_due(
    cursor: Any,
    garage: Garage,
    agreement: Agreement,
    period: BillingPeriod,
    stored: tuple[date, date] | None,
) -> tuple:
    """THE ONE-TIME FEES THIS PERIOD'S INVOICE CARRIES (OP-7-11): every one
    whose day is before the period ends and that no invoice line of the
    agreement already bills -- so a fee added after its period was invoiced,
    or in a period with no billable day, lands on the first invoice issued on
    or after its day, once.

    Billed means a ``one_time_fee`` line of the agreement's identity under the
    fee's label and day (``one_time_fee_label``), or -- written before that
    label existed -- under its bare label on a line whose period holds its day.
    A fee whose day is inside the first charge's two periods is the first
    charge's alone, stored there or not: the run never carries it."""
    from .invoice import one_time_fee_label

    first = (
        period_containing(garage, stored[1])
        if stored is not None
        else next_period_after(garage, period_containing(garage, agreement.start_day))
    )
    candidates = [
        fee for fee in agreement.one_time_fees()
        if first.end_day <= fee.effective_from < period.end_day
    ]
    if not candidates:
        return ()
    cursor.execute(
        "SELECT l.label, l.period_start_day, l.period_end_day FROM invoice_lines l "
        "JOIN agreements a ON a.id = l.agreement_id "
        "WHERE a.external_id = %s AND l.kind = 'one_time_fee'",
        (agreement.id,),
    )
    billed = cursor.fetchall()

    def already(fee) -> bool:
        return any(
            label == one_time_fee_label(fee)
            or (label == f"{fee.label} (one-time)" and start <= fee.effective_from < end)
            for label, start, end in billed
        )

    return tuple(fee for fee in candidates if not already(fee))


def _agreements_with_invoice_lines(
    cursor: Any, agreement_ids: list[str], *, first_charge: bool
) -> set[str]:
    """The agreement ids, by identity across versions, that a line of a first-
    charge invoice (``first_charge``) or of any other invoice names."""
    cursor.execute(
        "SELECT DISTINCT a.external_id FROM invoice_lines l "
        "JOIN invoices i ON i.id = l.invoice_id "
        "JOIN agreements a ON a.id = l.agreement_id "
        "WHERE a.external_id = ANY(%s) AND i.first_charge = %s",
        (agreement_ids, first_charge),
    )
    return {agreement for (agreement,) in cursor.fetchall()}


def issue_first_charge(
    connection: Any,
    tenant_id: Any,
    agreement_id: str,
    *,
    now: datetime,
) -> RunLine:
    """THE FIRST CHARGE, STORED (OP-7-2): written as an invoice through the
    run's own path -- taxed once at ``first_charge_due_at`` by the engine,
    under the invoice's one-per-payer-per-day lock, ``paid_at`` derived -- and,
    in the same transaction, one ``first_charges`` row per agreement on it,
    naming the invoice and the two periods that agreement owns.

    **ONE INVOICE PER PAYER AND SIGN-UP DAY, FOR EVERY SPOT.** The owner's
    rule: the first charge is paid on the sign-up day and collects for every
    spot signed up. So the invoice carries every agreement of this payer at
    this home that starts on the same day and has no first charge yet --
    ``first_charge``'s two lines for each, kept separate -- and one
    ``first_charges`` row for each. Refused by name
    (``REFUSAL_FIRST_CHARGE_PAST_ITS_PERIODS``) once ``now`` is on or after the
    end of the second period or an invoice already bills the agreement; a
    same-day spot an invoice already bills is never swept into the batch. The
    invoice is keyed on (payer, sign-up
    day, first charge) -- its own key, so it never meets the payer's period
    invoice or a first charge of another day (migration 0010). Asked again for
    any of them it is ALREADY_ISSUED. A spot stored for the same payer and day AFTER that
    invoice was issued is refused by name (``REFUSAL_FIRST_CHARGE_DAY_ISSUED``),
    writing nothing: an issued invoice is never added to.
    """
    import psycopg  # the store extra; the engine never imports this module

    from .invoice import first_charge, first_charge_due_at
    from .store.records import latest_version_home, load_garage_by_uuid

    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        home_uuid = latest_version_home(cursor, tenant_id, agreement_id)
        stored = load_garage_by_uuid(cursor, home_uuid)
        garage = stored.garage
        at_home = load_agreements_at_garage(cursor, home_uuid)
        already = stored_first_charges(cursor, home_uuid)
        ids = [i.agreement.id for i in at_home]
        billed = _agreements_with_invoice_lines(cursor, ids, first_charge=False)
        # Read after ``already``: a same-day issue committed in between shows
        # here, and its spots are already issued, not billed elsewhere.
        already = {**already, **dict.fromkeys(
            _agreements_with_invoice_lines(cursor, ids, first_charge=True), ())}
    connection.rollback()
    asked = [item for item in at_home if item.agreement.id == agreement_id]
    if not asked:
        raise GarageNotFound(f"agreement {agreement_id!r} is not billed at its home garage.")
    (item,) = asked
    payer_id = item.agreement.payer_id
    if agreement_id in already:
        return RunLine(payer_id, RunOutcome.ALREADY_ISSUED,
                       "the first charge of this agreement was already issued")
    start_day = item.agreement.start_day
    partial = period_containing(garage, start_day)
    following = next_period_after(garage, partial)
    # NEVER FOR PERIODS ALREADY OVER (the independent review): a first charge
    # pays its two periods in advance. Issued on or after the second one ends,
    # or for an agreement an invoice already bills, it would bill periods this
    # store never billed and that were settled outside it.
    if day_of(now, zone(garage.timezone)) >= following.end_day or agreement_id in billed:
        refusal = Refused(
            REFUSAL_FIRST_CHARGE_PAST_ITS_PERIODS,
            f"agreement {agreement_id!r} starts {start_day}; its second period ended "
            f"{following.end_day}"
            + ("; an invoice already bills it." if agreement_id in billed else "."),
        )
        return RunLine(payer_id, RunOutcome.REFUSED, str(refusal))
    # Every spot of this payer signed up on this day, not yet first-charged and
    # billed by nothing.
    batch = sorted(
        (
            other for other in at_home
            if other.payer_uuid == item.payer_uuid
            and other.agreement.start_day == start_day
            and other.agreement.id not in already
            and other.agreement.id not in billed
        ),
        key=lambda other: other.agreement.id,
    )
    day = start_day  # the first charge's own key: (payer, sign-up day, first_charge)
    invoice = replace(
        first_charge(garage, item.agreement),
        issued_for_period_start=day,
        lines=tuple(
            line for other in batch for line in first_charge(garage, other.agreement).lines
        ),
    )
    reference = f"{garage.id}/first-charge/{payer_id}/{start_day.isoformat()}"
    due_at = first_charge_due_at(garage, item.agreement)
    try:
        with tenant(connection, tenant_id) as cursor:
            taxed = tax_an_invoice(
                invoice, sets=load_tax_set_documents(cursor, home_uuid), at=due_at,
            )
            invoice_uuid = _persist(
                cursor, tenant_id, taxed.invoice, position=taxed.position,
                reference=reference, garage_uuid=home_uuid, payer_uuid=item.payer_uuid,
                agreement_uuids={other.agreement.id: other.uuid for other in batch},
                due_at=due_at, now=now, first_charge=True,
            )
            for other in batch:
                guarded_insert(
                    cursor,
                    "first_charges",
                    {
                        "tenant_id": tenant_id,
                        "garage_id": home_uuid,
                        "agreement_external_id": other.agreement.id,
                        "agreement_id": other.uuid,
                        "invoice_id": invoice_uuid,
                        "first_period_start_day": partial.start_day,
                        "second_period_start_day": following.start_day,
                        "second_period_end_day": following.end_day,
                    },
                )
                cursor.fetchone()
        connection.commit()
    except (Refused, UnpriceableCurrency, InstrumentLike) as refusal:
        connection.rollback()
        return RunLine(payer_id, RunOutcome.REFUSED, str(refusal), reference=reference)
    except psycopg.errors.UniqueViolation as violation:
        connection.rollback()
        name = violation.diag.constraint_name
        with tenant(connection, tenant_id) as cursor:
            # A same-day issue that ran at once with this one may already
            # hold this spot: then the answer is that, not a refusal.
            mine_now = agreement_id in stored_first_charges(cursor, home_uuid)
        connection.rollback()
        if name == "first_charges_one_per_agreement" or mine_now:
            return RunLine(payer_id, RunOutcome.ALREADY_ISSUED,
                           "the first charge of this agreement was already issued",
                           reference=reference)
        with tenant(connection, tenant_id) as cursor:
            cursor.execute(
                "SELECT 1 FROM invoices i "
                "WHERE i.garage_id = %s AND i.payer_id = %s AND i.period_start_day = %s "
                "AND i.first_charge",
                (home_uuid, item.payer_uuid, day),
            )
            day_taken_by_a_first_charge = cursor.fetchone() is not None
        connection.rollback()
        if day_taken_by_a_first_charge:
            refusal = Refused(
                REFUSAL_FIRST_CHARGE_DAY_ISSUED,
                f"agreement {agreement_id!r} starts {start_day}; payer {payer_id!r} already "
                f"has its first-charge invoice keyed on {day}.",
            )
            return RunLine(payer_id, RunOutcome.REFUSED, str(refusal), reference=reference)
        return RunLine(
            payer_id, RunOutcome.CONSTRAINT_VIOLATED,
            f"the database refused the first charge on {name!r}: the payer already has "
            f"an invoice keyed on {day}; nothing issued",
            reference=reference,
        )
    issued = taxed.invoice
    return RunLine(
        payer_id, RunOutcome.ISSUED,
        f"first charge for {len(batch)} agreement(s) "
        f"({', '.join(other.agreement.id for other in batch)}): {len(issued.lines)} line(s), "
        f"subtotal {issued.subtotal_minor}, tax {issued.total_minor - issued.subtotal_minor}, "
        f"total {issued.total_minor} {issued.currency} minor",
        reference=reference, total_minor=issued.total_minor,
    )


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
                first_charges = stored_first_charges(cursor, stored.uuid)
                priced = tuple(
                    item.agreement
                    for item in items
                    if not owned_by_first_charge(
                        garage, item.agreement, period, first_charges.get(item.agreement.id)
                    )
                )
                due = {
                    agreement.id: _one_time_fees_due(
                        cursor, garage, agreement, period, first_charges.get(agreement.id)
                    )
                    for agreement in priced
                }
            connection.rollback()
            invoice = invoice_for_period(garage, priced, period, payer_id, one_time_due=due)
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
                # The sets this payer's transaction sees, and the tax at due_at.
                taxed = tax_an_invoice(
                    invoice,
                    sets=load_tax_set_documents(cursor, stored.uuid),
                    at=period.start_instant,
                )
                _persist(
                    cursor,
                    tenant_id,
                    taxed.invoice,
                    position=taxed.position,
                    reference=reference,
                    garage_uuid=stored.uuid,
                    payer_uuid=payer_uuid,
                    agreement_uuids={item.agreement.id: item.uuid for item in items},
                    due_at=period.start_instant,
                    now=now,
                )
            connection.commit()
        except (Refused, UnpriceableCurrency, InstrumentLike) as refusal:
            connection.rollback()
            # Nothing was written. A period already invoiced is not refused
            # because it cannot be priced again today: nothing was to be issued
            # and nothing re-priced. The period lock's own fact, read back as
            # the unique-violation path below reads it.
            with tenant(connection, tenant_id) as cursor:
                invoiced = _period_is_issued(cursor, stored.uuid, payer_uuid, period.start_day)
            connection.rollback()
            if invoiced:
                lines.append(
                    RunLine(
                        payer_id,
                        RunOutcome.ALREADY_ISSUED,
                        "this period was already invoiced; nothing issued and nothing re-priced",
                        reference=reference,
                    )
                )
            else:
                lines.append(RunLine(payer_id, RunOutcome.REFUSED, str(refusal)))
            continue
        except psycopg.errors.CheckViolation as violation:
            # A constraint checked at COMMIT (migration 0006's subtotal trigger)
            # or at the statement. Named, never read as "already issued".
            connection.rollback()
            lines.append(
                RunLine(
                    payer_id,
                    RunOutcome.CONSTRAINT_VIOLATED,
                    f"the database refused the invoice on {violation.diag.constraint_name!r}; "
                    "nothing issued, and this period is NOT known to be invoiced",
                    reference=reference,
                )
            )
            continue
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

        issued = taxed.invoice
        lines.append(
            RunLine(
                payer_id,
                RunOutcome.ISSUED,
                f"{len(issued.lines)} line(s), subtotal {issued.subtotal_minor}, tax "
                f"{issued.total_minor - issued.subtotal_minor}, total {issued.total_minor} "
                f"{issued.currency} minor",
                reference=reference,
                total_minor=issued.total_minor,
            )
        )

    return RunReport(garage_id=garage.id, period=period, lines=tuple(lines))


def _period_is_issued(cursor: Any, garage_uuid: Any, payer_uuid: Any, start_day: date) -> bool:
    """The fact the period lock guards, read directly: a PERIOD invoice, never a
    first charge, which has its own key (migration 0010)."""
    cursor.execute(
        "SELECT 1 FROM invoices WHERE garage_id = %s AND payer_id = %s AND period_start_day = %s "
        "AND NOT first_charge",
        (garage_uuid, payer_uuid, start_day),
    )
    return cursor.fetchone() is not None


def _persist(
    cursor: Any,
    tenant_id: Any,
    invoice: Invoice,
    *,
    position: TaxPosition,
    reference: str,
    garage_uuid: Any,
    payer_uuid: Any,
    agreement_uuids: dict[str, Any],
    due_at: datetime,
    now: datetime,
    first_charge: bool = False,
) -> Any:
    """The invoice row -- with the tax position that priced it -- and its lines,
    through the chokepoint, in the caller's transaction. A tax line names no
    agreement; every other line names the one that priced it. Then ``paid_at``,
    derived: a zero total is paid at issue (see the module docstring)."""
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
            "subtotal_minor": position.subtotal_minor,
            "tax_set_effective_from": position.tax_set_effective_from,
            "tax_sets_seen": position.tax_sets_seen,
            "first_charge": first_charge,
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
                "agreement_id": (
                    None if line.agreement_id is None else agreement_uuids[line.agreement_id]
                ),
                "agreement_version": line.agreement_version,
                "period_start_day": line.period_start_day,
                "period_end_day": line.period_end_day,
                "exception_id": line.exception_id,
                "tax_rule_id": line.tax_rule_id,
            },
        )
    rederive_paid_at(cursor, invoice_uuid, now)
    return invoice_uuid
