"""G60 -- a recurring fee is prorated from its own start.

The module's own rule: a recurring fee is prorated exactly as the monthly
price is, "a half month of it is half of it". A fee that STARTED inside a
period was skipped only if it started after the period ended, and was
otherwise prorated by the agreement's billable days -- so a reserved-space fee
added on the 20th billed the whole month for days it did not exist. It is now
billed for the agreement's billable days from the later of its own
``effective_from`` and the period's first day, and its line says 'part month'.
A fee in force for the whole period, and a one-time fee, are unchanged.

Controls, each with a plant in ``scripts/fail_controls.py``: the fee's own
start ignored (the agreement's days again).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.agreement import AdditionalFee, FeeCadence, Pause
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.cycle import period_containing
from monthly_billing.invoice import LineKind, first_charge, invoice_for_period
from store_harness import needs_postgres, query, seed

GARAGE = month_end_garage()  # the period runs from the last day of one month to the next
MAY = period_containing(GARAGE, date(2026, 5, 15))  # 2026-04-30 .. 2026-05-31, 31 days


def _fee(effective_from: date, amount_minor: int = 3100) -> AdditionalFee:
    return AdditionalFee(label="Reserved space 12", amount_minor=amount_minor,
                         cadence=FeeCadence.RECURRING, effective_from=effective_from)


def _fee_line(invoice):
    (line,) = [line for line in invoice.lines if line.kind is LineKind.RECURRING_FEE]
    return line


def _may(fee: AdditionalFee, **overrides):
    agreement = simple_agreement(start_day=date(2026, 1, 5), additional_fees=(fee,), **overrides)
    return invoice_for_period(GARAGE, (agreement,), MAY, agreement.payer_id)


@pytest.mark.guarantee("G60")
def test_a_fee_starting_mid_period_is_billed_from_its_own_start():
    """FO-M17(a): a $31 fee effective May 20 on a whole May. May 20 to May 30
    are 11 of the period's 31 days: 11.00, 'part month' -- not the 31.00 the
    agreement's whole month would give. The price line is untouched."""
    assert MAY.days == 31 and MAY.start_day == date(2026, 4, 30)
    invoice = _may(_fee(date(2026, 5, 20)))
    line = _fee_line(invoice)
    assert line.amount_minor == 3100 * 11 // 31 == 1100
    assert line.label == "Reserved space 12 (part month)"
    (price,) = [line for line in invoice.lines if line.kind is LineKind.FULL_PERIOD]
    assert price.amount_minor == 12000


@pytest.mark.guarantee("G60")
def test_the_fees_own_days_are_the_agreements_billable_days_from_its_start():
    """Removed together, as the price's days are: a pause from May 25 leaves
    the fee May 20 to May 24, five days."""
    invoice = _may(
        _fee(date(2026, 5, 20)),
        pauses=(Pause(from_day=date(2026, 5, 25), until_day=date(2026, 7, 1)),),
    )
    assert _fee_line(invoice).amount_minor == 3100 * 5 // 31


@pytest.mark.guarantee("G60")
def test_a_fee_in_force_all_period_is_unchanged_and_one_after_it_is_absent():
    """The over-reach controls, must stay green: a fee in force before the
    period, or starting ON its first day, is the whole monthly figure; one
    starting on the period's end day is not on the invoice at all."""
    for start in (date(2026, 1, 5), MAY.start_day):
        line = _fee_line(_may(_fee(start)))
        assert (line.amount_minor, line.label) == (3100, "Reserved space 12 (monthly)"), start
    after = _may(_fee(MAY.end_day))
    assert [line for line in after.lines if line.kind is LineKind.RECURRING_FEE] == []


@pytest.mark.guarantee("G60")
def test_the_first_charge_bills_a_fee_from_its_own_start_too():
    """The agreement starts March 10 (21 of 31 billable days); its fee starts
    March 20 -- 11 of them. The part-period fee line is 11/31 of the fee, and
    the following full period carries the whole fee."""
    agreement = simple_agreement(start_day=date(2026, 3, 10),
                                 additional_fees=(_fee(date(2026, 3, 20), 2500),))
    lines = [line for line in first_charge(GARAGE, agreement).lines
             if line.kind is LineKind.RECURRING_FEE]
    assert [(line.amount_minor, line.label) for line in lines] == [
        (2500 * 11 // 31, "Reserved space 12 (part month)"),
        (2500, "Reserved space 12 (monthly)"),
    ]


def _store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


@pytest.mark.guarantee("G60")
@_store_backed
def test_the_run_issues_the_fee_from_its_own_start(app, tenant_id):
    """End to end: the billing run writes the 11-day fee line for May."""
    agreement = simple_agreement(start_day=date(2026, 1, 5),
                                 additional_fees=(_fee(date(2026, 5, 20)),))
    seed(app, tenant_id, GARAGE, (agreement,))
    now = datetime(2026, 4, 30, 0, 30, tzinfo=ZoneInfo(GARAGE.timezone))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=now).lines
    assert line.outcome is RunOutcome.ISSUED
    assert query(
        app, tenant_id,
        "SELECT label, amount_minor FROM invoice_lines WHERE kind = 'recurring_fee'",
    ) == [("Reserved space 12 (part month)", 1100)]
