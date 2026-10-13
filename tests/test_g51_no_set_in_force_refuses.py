"""G51 -- no tax set in force at ``due_at`` refuses the invoice; it is never a zero.

A garage that has stated nothing, and a garage whose only set takes effect after
the period's due instant, each get a REFUSED line per payer carrying the
engine's sentence, no invoice row, and a run that reports refused. The control
on the refusal is the same garage stating "none", which issues. Controls plant
"nothing stated means zero" and "nothing in force means zero" and require red.
"""

from __future__ import annotations

import pytest

from monthly_billing.billing_run import RunOutcome
from monthly_billing.findings import REFUSAL_NO_TAX_SET_IN_FORCE
from store_harness import NO_TAX, needs_postgres, query
from tax_harness import SALES, issue

pytestmark = needs_postgres

TWO = ("payer-1", "payer-2")


def _refused_and_nothing_written(app, tenant_id, report, *, engine_words: str):
    assert report.refused, report.rendered()
    assert [line.payer_id for line in report.lines] == list(TWO), (
        "a payer was not given its line -- the refusal stopped the run"
    )
    for line in report.lines:
        assert line.outcome is RunOutcome.REFUSED, line.detail
        assert REFUSAL_NO_TAX_SET_IN_FORCE in line.detail
        assert engine_words in line.detail, line.detail
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(0,)]
    assert query(app, tenant_id, "SELECT count(*) FROM invoice_lines") == [(0,)]


@pytest.mark.guarantee("G51")
def test_a_garage_that_stated_nothing_is_refused_and_not_taxed_at_zero(app, tenant_id):
    report = issue(app, tenant_id, sets=None, payers=TWO)
    _refused_and_nothing_written(
        app, tenant_id, report, engine_words="has not said it charges no tax"
    )


@pytest.mark.guarantee("G51")
def test_a_set_that_takes_effect_after_due_at_is_refused_by_the_engines_finding(
    app, tenant_id
):
    later_only = [{"effective_from": "2026-04-30T00:00:01-06:00", "rules": [SALES]}]
    report = issue(app, tenant_id, sets=later_only, payers=TWO)
    _refused_and_nothing_written(
        app, tenant_id, report, engine_words="GAP_NO_TAX_SET_IN_FORCE"
    )


@pytest.mark.guarantee("G51")
def test_the_control_a_garage_stating_none_is_issued(app, tenant_id):
    report = issue(app, tenant_id, sets=NO_TAX, payers=TWO)
    assert not report.refused, report.rendered()
    assert {line.outcome for line in report.lines} == {RunOutcome.ISSUED}
