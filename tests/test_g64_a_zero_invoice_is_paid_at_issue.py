"""G64 -- an invoice issued with a zero total is paid from the moment it is issued.

**THE ISSUE DERIVES ``paid_at`` TOO.** Paid is derived, never set by hand: an
invoice is paid when its unreversed payments reach its total. For a total of
zero that is true the moment the invoice exists -- nothing is owed -- and the
module already reads a total credited down to zero as paid (G33). The billing
run wrote its invoices without deriving it, so a comped agreement's $0 invoice
stayed unpaid for ever: a $0 payment is refused, a waiver of nothing is refused,
and the car read NOT COVERED, UNPAID_PAST_GRACE, from the day after grace.

The control plants the derivation at issue away and requires red. The last test
is the control on the first two: an invoice that owes something is still unpaid
when it is issued, so the derivation decides; it does not mark things paid.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.entitlement_store import covered_from_store
from store_harness import needs_postgres, query, seed
from tax_harness import TAXED, issue

pytestmark = needs_postgres

GARAGE = month_end_garage()  # grace 5 days; the May period is due 2026-04-30
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _invoice(app, tenant_id):
    ((total, issued_at, paid_at),) = query(
        app, tenant_id,
        "SELECT (SELECT coalesce(sum(amount_minor), 0) FROM invoice_lines l "
        "        WHERE l.invoice_id = i.id), i.issued_at, i.paid_at FROM invoices i",
    )
    return int(total), issued_at, paid_at


@pytest.mark.guarantee("G64")
def test_a_zero_invoice_is_paid_at_issue_and_its_car_stays_covered_past_grace(app, tenant_id):
    seed(app, tenant_id, GARAGE, (
        simple_agreement(start_day=date(2026, 1, 5), monthly_price_minor=0),
    ))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 0, line.detail

    total, issued_at, paid_at = _invoice(app, tenant_id)
    assert total == 0
    assert paid_at == issued_at == NOW, "a $0 invoice is paid by the issue that wrote it"

    # Past the 5 days of grace from 04-30: the 6th is the first day an unpaid
    # invoice reads UNPAID_PAST_GRACE. Nothing was owed, so nothing is unpaid.
    for day in (date(2026, 5, 6), date(2026, 5, 20)):
        answer = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(day))
        assert answer.covered, (day, answer.reason_code, answer.reason)


@pytest.mark.guarantee("G64")
def test_a_zero_subtotal_under_a_taxed_set_is_paid_at_issue_too(app, tenant_id):
    """A zero subtotal yields no tax line (G52), so the total is zero as well."""
    (line,) = issue(app, tenant_id, sets=TAXED, price=0).lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 0, line.detail
    total, issued_at, paid_at = _invoice(app, tenant_id)
    assert total == 0 and paid_at == issued_at


@pytest.mark.guarantee("G64")
def test_an_invoice_that_owes_something_is_still_unpaid_at_issue(app, tenant_id):
    """THE CONTROL: the issue derives ``paid_at``; it does not set it. 12000 owed
    and nothing received is unpaid, and the car is not covered past grace."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 12000
    total, _, paid_at = _invoice(app, tenant_id)
    assert total == 12000 and paid_at is None
    past = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 6)))
    assert not past.covered and past.reason_code == "UNPAID_PAST_GRACE"
