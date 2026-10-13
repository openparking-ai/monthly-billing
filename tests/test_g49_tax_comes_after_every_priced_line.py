"""G49 -- at issue, tax is appended after every priced line, by one producer.

``first_charge`` and ``invoice_for_period`` price periods and return UNTAXED
invoices; ``tax_an_invoice`` takes a complete untaxed invoice and appends the
engine's lines after everything else, one per rule in the garage's stated
order, each naming its rule and no agreement. A taxed invoice is refused for a
second taxing. Controls plant the tax lines in front, and the re-tax refusal
away, and require red.
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import agreement_with_everything, month_end_garage, simple_agreement
from monthly_billing.cycle import period_containing
from monthly_billing.findings import REFUSAL_INVOICE_ALREADY_TAXED, Refused
from monthly_billing.invoice import LineKind, first_charge, invoice_for_period
from monthly_billing.tax import tax_an_invoice
from tax_harness import TAXED, engine_tax

GARAGE = month_end_garage()
PERIOD = period_containing(GARAGE, date(2026, 3, 15))


def _priced():
    agreement = agreement_with_everything()
    return invoice_for_period(GARAGE, (agreement,), PERIOD, agreement.payer_id)


@pytest.mark.guarantee("G49")
def test_the_tax_lines_come_after_every_priced_line_in_rule_order():
    untaxed = _priced()
    kinds = {line.kind for line in untaxed.lines}
    assert len(kinds) >= 2, "the fixture must price more than one kind of line"
    taxed = tax_an_invoice(untaxed, sets=TAXED, at=PERIOD.start_instant).invoice

    n = len(untaxed.lines)
    assert taxed.lines[:n] == untaxed.lines, "the priced lines moved or changed"
    tax = taxed.lines[n:]
    assert [line.kind for line in tax] == [LineKind.TAX, LineKind.TAX]
    assert [line.tax_rule_id for line in tax] == ["sales", "parking"]
    assert all(line.agreement_id is None and line.agreement_version is None for line in tax)
    assert all(line.exception_id is None for line in tax)
    assert {line.tax_rule_id: line.amount_minor for line in tax} == engine_tax(
        untaxed.total_minor, at=PERIOD.start_instant
    )
    assert taxed.total_minor == untaxed.total_minor + sum(line.amount_minor for line in tax)
    assert taxed.subtotal_minor == untaxed.total_minor


@pytest.mark.guarantee("G49")
def test_the_period_pricers_never_produce_a_tax_line():
    agreement = simple_agreement()
    for invoice in (_priced(), first_charge(GARAGE, agreement)):
        assert all(line.kind is not LineKind.TAX for line in invoice.lines)
        assert not invoice.taxed


@pytest.mark.guarantee("G49")
def test_a_taxed_invoice_is_refused_a_second_taxing():
    taxed = tax_an_invoice(_priced(), sets=TAXED, at=PERIOD.start_instant).invoice
    with pytest.raises(Refused) as refused:
        tax_an_invoice(taxed, sets=TAXED, at=PERIOD.start_instant)
    assert refused.value.code == REFUSAL_INVOICE_ALREADY_TAXED
