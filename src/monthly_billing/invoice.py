"""What a payer owes, itemised, for one garage and one period.

**THE PAYER IS A SEPARATE PARTY FROM THE PARKER, FROM THE FIRST MIGRATION.** A
company pays one invoice for many parkers, so an invoice is issued to a PAYER and
itemises each agreement under it. Where the payer is the parker it is the same
mechanism with one agreement, never a second path -- two paths would mean two sets
of arithmetic and the seldom-used one would be the one that was wrong.

**AN INVOICE COVERS ONE GARAGE, ONE PERIOD AND ONE CURRENCY**, and the reason is
arithmetic rather than policy. The billing day is a property of the garage, so two
garages bill on different dates and their periods are not the same period. The
currency is a property of the garage too, and two currencies do not sum -- this
module will not convert them, because a conversion needs a rate, a date and a
spread, none of which any document here carries. A payer holding agreements at two
garages therefore receives two invoices. Recorded because the specification said
"an invoice is issued to the payer and itemises each agreement under it" without
settling what happens when those agreements are not billed on the same day.

**THE FIRST CHARGE IS TWO LINES AND IS NEVER ONE SUMMED FIGURE.** The period
containing the start date plus the one after it, always as separate lines, so a
customer can see which part is which.

**THE LINE COUNT DOES NOT DEPEND ON THE START DATE, and that is the promise.** An
agreement starting ON the billing day gets the whole of the first period, so its
first line is a full month rather than a part one -- it is still its own line.
This was written the other way round first, as "the partial period is zero days
when the start date is the billing day", and that was simply wrong: the period
CONTAINING the billing day starts on it, so every day of it is billable. The
sentence was corrected rather than the arithmetic.

A first line really can be zero -- an agreement whose whole first period is
paused -- and it is emitted at zero saying so, rather than dropped. A caller that
reads the second line for the following period gets the same answer whatever the
start date, which is what makes this a guarantee rather than a habit.

**AND A PAID PERIOD IS PAID.** A price change never touches a period already
paid; it takes effect at the next cycle. Enforced by construction rather than by
care: an invoice records the agreement VERSION each line was computed from, and
recomputing a paid invoice against a newer version is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .agreement import Agreement
from .currency import validate_currency
from .cycle import BillingPeriod, billable_days, next_period_after, period_containing, prorate
from .findings import REFUSAL_CURRENCY_MISMATCH, REFUSAL_GARAGE_MISMATCH, Refused
from .garage import Garage
from .money import format_minor


class LineKind(Enum):
    """What a line is FOR. The kinds are separate because the invoice promises
    they are: a partial period and a full one are never one summed figure."""

    PARTIAL_PERIOD = "partial_period"
    FULL_PERIOD = "full_period"
    RECURRING_FEE = "recurring_fee"
    ONE_TIME_FEE = "one_time_fee"
    #: An owner's exception that changed an amount. Always its own line, always
    #: with the exception's id on it, so that a figure nobody can account for
    #: cannot appear on an invoice.
    EXCEPTION_ADJUSTMENT = "exception_adjustment"


@dataclass(frozen=True)
class InvoiceLine:
    kind: LineKind
    label: str
    amount_minor: int
    agreement_id: str
    agreement_version: int
    period_start_day: date
    period_end_day: date
    #: Populated on an EXCEPTION_ADJUSTMENT line and on nothing else.
    exception_id: str | None = None

    def rendered(self, currency: str) -> str:
        return f"{self.label}: {format_minor(self.amount_minor, currency)}"


@dataclass(frozen=True)
class Invoice:
    payer_id: str
    garage_id: str
    currency: str
    issued_for_period_start: date
    lines: tuple[InvoiceLine, ...] = field(default_factory=tuple)

    @property
    def total_minor(self) -> int:
        return sum(line.amount_minor for line in self.lines)

    def rendered(self) -> str:
        body = "\n".join(f"  {line.rendered(self.currency)}" for line in self.lines)
        return (
            f"Invoice to payer {self.payer_id} for garage {self.garage_id}\n"
            f"{body}\n"
            f"  TOTAL: {format_minor(self.total_minor, self.currency)}"
        )


def _fee_lines(
    agreement: Agreement,
    period: BillingPeriod,
    *,
    prorated: bool,
) -> list[InvoiceLine]:
    """The additional fee lines falling in ``period``.

    **A RECURRING FEE IS PRORATED EXACTLY AS THE MONTHLY PRICE IS.** It is part of
    what the account pays every month, so a half month of it is half of it. A
    ONE-TIME fee is not prorated -- it is a thing that happened once, on a day --
    and it lands on the invoice for the period containing its effective date.
    Neither of these was settled by the specification; both are stated here
    because either answer is defensible and only one can be the code's.
    """
    lines: list[InvoiceLine] = []
    days = billable_days(agreement, period)

    for fee in agreement.recurring_fees():
        if fee.effective_from >= period.end_day:
            continue
        amount = (
            prorate(fee.amount_minor, days, period.days) if prorated else fee.amount_minor
        )
        lines.append(
            InvoiceLine(
                kind=LineKind.RECURRING_FEE,
                label=f"{fee.label} ({'part month' if prorated else 'monthly'})",
                amount_minor=amount,
                agreement_id=agreement.id,
                agreement_version=agreement.version,
                period_start_day=period.start_day,
                period_end_day=period.end_day,
            )
        )

    for fee in agreement.one_time_fees():
        if period.contains(fee.effective_from):
            lines.append(
                InvoiceLine(
                    kind=LineKind.ONE_TIME_FEE,
                    label=f"{fee.label} (one-time)",
                    amount_minor=fee.amount_minor,
                    agreement_id=agreement.id,
                    agreement_version=agreement.version,
                    period_start_day=period.start_day,
                    period_end_day=period.end_day,
                )
            )
    return lines


def first_charge(garage: Garage, agreement: Agreement) -> Invoice:
    """The partial period and the next full one, as separate lines.

    No signup cutoff: whenever the agreement starts, it is charged for the rest
    of that period plus the whole of the next one. Simple was the requirement,
    and a cutoff is the thing that makes a billing system need a paragraph of
    explanation at the till.
    """
    _same_garage(garage, agreement)

    partial = period_containing(garage, agreement.start_day)
    following = next_period_after(garage, partial)

    partial_days = billable_days(agreement, partial)
    partial_amount = prorate(agreement.monthly_price_minor, partial_days, partial.days)

    lines: list[InvoiceLine] = [
        InvoiceLine(
            kind=LineKind.PARTIAL_PERIOD,
            label=(
                f"Part period {partial.start_day} to {partial.end_day} "
                f"({partial_days} of {partial.days} days)"
                if partial_days
                else (
                    f"Part period {partial.start_day} to {partial.end_day} "
                    f"(0 of {partial.days} days — nothing is billable in this "
                    "period, so this line is zero rather than absent)"
                )
            ),
            amount_minor=partial_amount,
            agreement_id=agreement.id,
            agreement_version=agreement.version,
            period_start_day=partial.start_day,
            period_end_day=partial.end_day,
        )
    ]
    lines.extend(_fee_lines(agreement, partial, prorated=True))

    following_days = billable_days(agreement, following)
    lines.append(
        InvoiceLine(
            kind=LineKind.FULL_PERIOD,
            label=f"Period {following.start_day} to {following.end_day}",
            amount_minor=prorate(
                agreement.monthly_price_minor, following_days, following.days
            ),
            agreement_id=agreement.id,
            agreement_version=agreement.version,
            period_start_day=following.start_day,
            period_end_day=following.end_day,
        )
    )
    lines.extend(_fee_lines(agreement, following, prorated=following_days != following.days))

    return Invoice(
        payer_id=agreement.payer_id,
        garage_id=garage.id,
        currency=garage.currency,
        issued_for_period_start=partial.start_day,
        lines=tuple(lines),
    )


def invoice_for_period(
    garage: Garage,
    agreements: tuple[Agreement, ...],
    period: BillingPeriod,
    payer_id: str,
) -> Invoice:
    """One payer's invoice for one garage and one period, itemising each agreement.

    Every agreement under the payer contributes its own lines, labelled with its
    own id, so a company paying for twelve parkers can see which twelve.
    """
    validate_currency(garage.currency, f"garage[{garage.id}].currency")

    lines: list[InvoiceLine] = []
    for agreement in sorted(agreements, key=lambda a: a.id):
        if agreement.payer_id != payer_id:
            continue
        _same_garage(garage, agreement)

        days = billable_days(agreement, period)
        if days == 0:
            continue  # paused, cancelled or not yet started for the whole period
        lines.append(
            InvoiceLine(
                kind=(
                    LineKind.FULL_PERIOD if days == period.days else LineKind.PARTIAL_PERIOD
                ),
                label=(
                    f"{agreement.id}: period {period.start_day} to {period.end_day}"
                    + ("" if days == period.days else f" ({days} of {period.days} days)")
                ),
                amount_minor=prorate(agreement.monthly_price_minor, days, period.days),
                agreement_id=agreement.id,
                agreement_version=agreement.version,
                period_start_day=period.start_day,
                period_end_day=period.end_day,
            )
        )
        lines.extend(_fee_lines(agreement, period, prorated=days != period.days))

    return Invoice(
        payer_id=payer_id,
        garage_id=garage.id,
        currency=garage.currency,
        issued_for_period_start=period.start_day,
        lines=tuple(lines),
    )


def _same_garage(garage: Garage, agreement: Agreement) -> None:
    if agreement.garage_id != garage.id:
        raise Refused(
            REFUSAL_GARAGE_MISMATCH,
            f"agreement {agreement.id!r} belongs to garage "
            f"{agreement.garage_id!r} and was billed against {garage.id!r}.",
        )


def refuse_mixed_currency(garages: tuple[Garage, ...]) -> None:
    """Refuse an invoice that would span two currencies, naming both.

    Exposed rather than inlined because the caller assembling a payer's invoices
    is the one that can see more than one garage, and this module refuses in one
    voice wherever the situation arises.
    """
    currencies = {g.currency for g in garages}
    if len(currencies) > 1:
        raise Refused(
            REFUSAL_CURRENCY_MISMATCH,
            f"these agreements are denominated in {sorted(currencies)}.",
        )
