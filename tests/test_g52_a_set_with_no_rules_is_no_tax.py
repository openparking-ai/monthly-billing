"""G52 -- a set with no rules states "no tax", and yields no tax line.

The invoice is issued with no tax line, records the set, and its subtotal is its
total. A zero subtotal under a set WITH rules yields no tax line either. The
control plants a zero "no tax" line and requires red.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from monthly_billing.billing_run import RunOutcome
from monthly_billing.invoice import LineKind
from monthly_billing.tax import tax_an_invoice
from store_harness import NO_TAX, needs_postgres, query
from tax_harness import TAXED, issue

pytestmark = needs_postgres


@pytest.mark.guarantee("G52")
def test_a_garage_stating_none_is_issued_with_no_tax_line_and_subtotal_equal_total(
    app, tenant_id
):
    (line,) = issue(app, tenant_id, sets=NO_TAX).lines
    assert line.outcome is RunOutcome.ISSUED, line.detail
    assert query(app, tenant_id, "SELECT count(*) FROM invoice_lines WHERE kind = 'tax'") == [
        (0,)
    ]
    ((subtotal, effective_from, seen, total),) = query(
        app, tenant_id,
        "SELECT i.subtotal_minor, i.tax_set_effective_from, i.tax_sets_seen, "
        "(SELECT sum(amount_minor) FROM invoice_lines) FROM invoices i",
    )
    assert subtotal == total == line.total_minor == 12000
    assert effective_from == datetime.fromisoformat(NO_TAX[0]["effective_from"])
    assert seen == 1


@pytest.mark.guarantee("G52")
def test_a_zero_subtotal_under_a_set_with_rules_yields_no_tax_line(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=TAXED, price=0).lines
    assert line.outcome is RunOutcome.ISSUED, line.detail
    assert query(app, tenant_id, "SELECT kind, amount_minor FROM invoice_lines") == [
        ("full_period", 0)
    ]


@pytest.mark.guarantee("G52")
def test_the_seam_itself_appends_nothing_for_none():
    from fixtures import simple_agreement
    from monthly_billing.cycle import period_containing
    from monthly_billing.invoice import invoice_for_period
    from tax_harness import GARAGE, PERIOD_DAY

    period = period_containing(GARAGE, PERIOD_DAY)
    agreement = simple_agreement(start_day=PERIOD_DAY.replace(month=1))
    untaxed = invoice_for_period(GARAGE, (agreement,), period, agreement.payer_id)
    taxed = tax_an_invoice(untaxed, sets=NO_TAX, at=period.start_instant)
    assert taxed.invoice == replace(untaxed)
    assert not any(line.kind is LineKind.TAX for line in taxed.invoice.lines)
    assert taxed.position.subtotal_minor == taxed.invoice.total_minor
