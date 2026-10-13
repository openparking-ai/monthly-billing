"""Tax on an invoice: `rate-engine`'s computation, and this module's seam to it.

**THIS MODULE COMPUTES NO PERCENTAGE.** `rate-engine`'s ``tax.py`` is the only tax
arithmetic in the estate -- the barrier's tax on a transient stay is the same
function -- and this file hands it a subtotal, a currency, an instant and the
garage's sets, and maps the lines it answers onto invoice lines. A second
implementation here would be one money decision in two places, free to disagree
with the one at the barrier, and the garage's tax would then reconcile against
nothing. There is no multiplication and no division in this file, and a
guarantee reads its source to say so.

**THE ENGINE IS IMPORTED HERE, LAZILY, AND NOWHERE ELSE.** It is the ``tax``
extra, not a dependency, exactly as the store's driver is the ``store`` extra:
``pip install`` of this package still installs nothing, and the entitlement
answer -- the call an integrator embeds in a lane -- imports nothing from this
file. Asked for tax without the engine installed, this refuses by name
(``REFUSAL_TAX_NEEDS_THE_ENGINE``) rather than computing one.

**VALIDATION IS THE ENGINE'S.** A garage's sets go to ``load_tax_sets`` before
any field of them is read here, and what it refuses is refused in its words
(``REFUSAL_TAX_SETS_INVALID``). This module judges no tax rule.

**ONE INVOICE, ONE TAX MOMENT, AND NO SET IN FORCE IS A REFUSAL.** The set is
chosen by the engine at the instant the caller gives -- the invoice's ``due_at``
for the billing run -- and an instant no set covers, or a garage that has stated
no set at all, is ``REFUSAL_NO_TAX_SET_IN_FORCE`` carrying the engine's sentence,
never a zero. A set with NO rules is how a garage states it charges none: the
invoice gets no tax lines and its subtotal is its total.

**TAX IS THE LAST COMPUTATION, NOT THE LAST ROW.** ``tax_an_invoice`` takes a
COMPLETE untaxed invoice and appends the tax lines -- the only producer of tax
at issue. An owner's later adjustment lands after them (lines are append-only),
and ``retax_after_adjustment`` gives the compensating line per rule of the set
the invoice RECORDED: the engine's tax for that rule on the new non-tax total,
minus what that rule's lines already carry. The engine still does the
arithmetic; the subtraction only says how far the lines already on the invoice
are from the engine's answer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .currency import UnpriceableCurrency
from .findings import (
    REFUSAL_INVOICE_ALREADY_TAXED,
    REFUSAL_NO_TAX_SET_IN_FORCE,
    REFUSAL_TAX_NEEDS_THE_ENGINE,
    REFUSAL_TAX_SETS_INVALID,
    Refused,
)
from .invoice import Invoice, InvoiceLine, LineKind
from .money import format_minor


@dataclass(frozen=True)
class TaxPosition:
    """What priced an invoice's tax, recorded on the invoice row and never changed.

    ``subtotal_minor`` is the pre-tax total AT ISSUE. The set is named by its
    instant -- the store's key for it -- and ``tax_sets_seen`` is how many sets
    the garage held when the invoice was priced, so a set stated later, even a
    backdated one, is visible as a difference.
    """

    subtotal_minor: int
    tax_set_effective_from: datetime
    tax_sets_seen: int


@dataclass(frozen=True)
class TaxedInvoice:
    """An invoice with its tax lines appended, and the position that priced them.

    The position travels BESIDE the invoice rather than on it: ``Invoice`` is the
    priced lines and nothing else, and a field on it that only some invoices
    carried would be state a reader had to know to ignore.
    """

    invoice: Invoice
    position: TaxPosition


def _engine() -> tuple[Any, type[Exception], type[Exception], Any]:
    """The engine's tax module, its two refusal types and its currency table --
    imported here, at call time, or refused by name."""
    try:
        from rate_engine import currency as engine_currency
        from rate_engine import tax as engine_tax
        from rate_engine.findings import Refused as EngineRefused
        from rate_engine.plan import InvalidPlan
    except ImportError:
        raise Refused(
            REFUSAL_TAX_NEEDS_THE_ENGINE,
            "`import rate_engine` failed; `pip install 'openparking-monthly-billing[tax]'`.",
        ) from None
    return engine_tax, EngineRefused, InvalidPlan, engine_currency


def load_sets(document: object, *, where: str = "tax_sets") -> tuple[Any, ...]:
    """A garage's sets, validated by the ENGINE, or ``REFUSAL_TAX_SETS_INVALID``
    in the engine's words. The returned values are the engine's ``TaxSet``s."""
    engine_tax, _, InvalidPlan, _ = _engine()
    try:
        return engine_tax.load_tax_sets(document, where)
    except InvalidPlan as refusal:
        raise Refused(REFUSAL_TAX_SETS_INVALID, str(refusal)) from None


def echoed(sets: tuple[Any, ...]) -> list[dict[str, Any]]:
    """The sets as the engine READ them, back in its document shape -- the
    instant as the engine parsed it, the rules in the garage's stated order.
    What the store keeps is this, never the caller's spelling."""
    return [
        {
            "effective_from": s.effective_from.isoformat(),
            "rules": [
                {
                    "id": r.id,
                    "label": r.label,
                    "percent_bp": r.percent_bp,
                    "rounding": r.rounding,
                    "sequence": r.sequence,
                }
                for r in sorted(s.rules, key=lambda r: r.sequence)
            ],
        }
        for s in sets
    ]


def _sets_or_no_position(document: object) -> tuple[Any, ...]:
    """The billing path's load: a garage with NO sets has stated no tax position,
    which is the no-set-in-force refusal -- in the engine's own words, because
    the engine refuses an empty list for exactly that reason."""
    if isinstance(document, list) and not document:
        engine_tax, _, InvalidPlan, _ = _engine()
        try:
            engine_tax.load_tax_sets(document)
        except InvalidPlan as refusal:
            raise Refused(REFUSAL_NO_TAX_SET_IN_FORCE, str(refusal)) from None
    return load_sets(document)


def _engine_lines(
    sets: tuple[Any, ...], *, subtotal_minor: int, currency: str, at: datetime
) -> tuple[Any, list[Any]]:
    """The set the engine chose at ``at`` and the lines it answered."""
    engine_tax, EngineRefused, InvalidPlan, engine_currency = _engine()
    if not engine_currency.is_known(currency):
        raise UnpriceableCurrency(
            f"{currency!r} is not a currency rate-engine renders, so it cannot tax an "
            "invoice in it."
        )
    try:
        chosen = engine_tax.select_tax_set(sets, at)
        lines = engine_tax.tax_lines(
            sets, subtotal_minor=subtotal_minor, currency=currency, at=at
        )
    except EngineRefused as refusal:
        words = " · ".join(f"{f.code}: {f.text}" for f in refusal.findings)
        raise Refused(REFUSAL_NO_TAX_SET_IN_FORCE, words) from None
    except InvalidPlan as refusal:
        raise Refused(REFUSAL_TAX_SETS_INVALID, str(refusal)) from None
    return chosen, lines


def _span(invoice: Invoice) -> tuple[Any, Any]:
    """The days the invoice's priced lines cover -- what a tax line's period is,
    since its base is all of them."""
    priced = [line for line in invoice.lines if line.kind is not LineKind.TAX]
    return (
        min(line.period_start_day for line in priced),
        max(line.period_end_day for line in priced),
    )


def tax_an_invoice(invoice: Invoice, *, sets: object, at: datetime) -> TaxedInvoice:
    """Append the engine's tax lines to a COMPLETE untaxed invoice.

    ``sets`` is the garage's WHOLE list of tax sets as documents -- the store's
    rows, or a document the command line was handed -- and ``at`` is the
    invoice's one tax moment. Returns a new invoice; the one given is unchanged.
    """
    if invoice.taxed:
        raise Refused(
            REFUSAL_INVOICE_ALREADY_TAXED,
            f"the invoice to payer {invoice.payer_id} for {invoice.issued_for_period_start} "
            "was handed to tax_an_invoice with tax lines on it.",
        )
    loaded = _sets_or_no_position(sets)
    subtotal = invoice.subtotal_minor
    chosen, answered = _engine_lines(
        loaded, subtotal_minor=subtotal, currency=invoice.currency, at=at
    )
    tax: list[InvoiceLine] = []
    if answered:
        start, end = _span(invoice)
        tax = [
            InvoiceLine(
                kind=LineKind.TAX,
                label=line.text,
                amount_minor=line.delta_minor,
                agreement_id=None,
                agreement_version=None,
                period_start_day=start,
                period_end_day=end,
                tax_rule_id=line.rule_id,
            )
            for line in answered
        ]
    return TaxedInvoice(
        invoice=replace(invoice, lines=(*invoice.lines, *tax)),
        position=TaxPosition(
            subtotal_minor=subtotal,
            tax_set_effective_from=chosen.effective_from,
            tax_sets_seen=len(loaded),
        ),
    )


def retax_after_adjustment(
    invoice: Invoice,
    *,
    sets: object,
    recorded_effective_from: datetime,
    exception_id: str,
    exception_label: str,
) -> tuple[InvoiceLine, ...]:
    """The compensating tax lines for an adjustment, one per rule of the set
    the invoice RECORDED -- never the set in force today.

    ``invoice`` is the invoice as it stands with the adjustment line on it.
    ``exception_id`` is what the lines carry; ``exception_label`` is how their
    text names it -- the owner's id for the exception, never a store uuid, whose
    text the instrument guard can mistake for a card number.
    For each rule: the engine's tax for that rule on the new non-tax total,
    minus the sum of that rule's tax lines already on the invoice. A rule whose
    figure did not move still gets its line, at zero, so every adjustment on a
    taxed invoice shows that its tax was recomputed -- and the store can require
    it. A recorded set with no rules returns nothing: there is no tax to move.
    """
    loaded = load_sets(sets)
    (recorded,) = [s for s in loaded if s.effective_from == recorded_effective_from] or [None]
    if recorded is None:
        raise LookupError(
            f"the set this invoice recorded, in force from "
            f"{recorded_effective_from.isoformat()}, is not among the garage's sets."
        )
    if not recorded.rules:
        return ()
    _, answered = _engine_lines(
        (recorded,),
        subtotal_minor=invoice.subtotal_minor,
        currency=invoice.currency,
        at=recorded.effective_from,
    )
    by_rule = {line.rule_id: line for line in answered}
    start, end = _span(invoice)
    compensating: list[InvoiceLine] = []
    for rule in sorted(recorded.rules, key=lambda r: r.sequence):
        now_owed = by_rule[rule.id].delta_minor if rule.id in by_rule else 0
        carried = sum(
            line.amount_minor
            for line in invoice.lines
            if line.kind is LineKind.TAX and line.tax_rule_id == rule.id
        )
        engine_says = (
            by_rule[rule.id].text
            if rule.id in by_rule
            else f"{rule.label}: nothing, on {format_minor(0, invoice.currency)} paid"
        )
        compensating.append(
            InvoiceLine(
                kind=LineKind.TAX,
                label=(
                    f"{rule.label} re-taxed for exception {exception_label}: "
                    f"{format_minor(carried, invoice.currency)} becomes "
                    f"{format_minor(now_owed, invoice.currency)} ({engine_says})"
                ),
                amount_minor=now_owed - carried,
                agreement_id=None,
                agreement_version=None,
                period_start_day=start,
                period_end_day=end,
                exception_id=exception_id,
                tax_rule_id=rule.id,
            )
        )
    return tuple(compensating)
