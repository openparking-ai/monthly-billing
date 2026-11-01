"""G18 -- the first charge is a stored invoice, and the periods it owns are
stored with it.

OP-7-2: the first charge -- the period containing the start day and the next
one -- was computed from documents and stored nowhere, and the run skips both
periods for that agreement. covered-in-store found nothing unpaid and answered
COVERED for a parker who never paid, and no payment could be recorded against
it. ``issue_first_charge`` now writes it as an invoice through the run's own
path, with a ``first_charges`` row naming the two periods; until it is
issued, the agreement's days inside those periods are owed from its start day.

OP-7-3: the run decided which periods the first charge owned from the LATEST
version's start day, so a version that moved the start day moved them and the
run billed a period already paid. The run reads the stored periods, and a
version that moves the start day after a first charge is refused by name.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import RunOutcome, issue_first_charge, run_billing
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.invoice import LineKind
from monthly_billing.payments import PaymentMethod, record_payment
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import show_garage_register
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()  # grace 5 days; bills on the last day of the month
TZ = ZoneInfo(GARAGE.timezone)
START = date(2026, 6, 10)  # periods: 05-31..06-30 (part) and 06-30..07-31


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _covered(app, tenant_id, day: date) -> bool:
    return covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(day)).covered


@pytest.mark.guarantee("G18")
def test_an_agreement_whose_first_charge_was_never_issued_is_not_covered_past_grace(
    app, tenant_id
):
    """OP-7-2. No first charge stored, no invoice in the store: before the fix
    the parker was COVERED for both first periods without paying anything.
    Now those days are owed from the start day -- covered through grace, not
    covered after it, until the second period ends -- and the register says
    the same days."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=START),))
    assert _covered(app, tenant_id, date(2026, 6, 15))
    assert not _covered(app, tenant_id, date(2026, 6, 16))
    assert not _covered(app, tenant_id, date(2026, 7, 30))
    with tenant(app, tenant_id) as cursor:
        (entry,) = show_garage_register(cursor, tenant_id, GARAGE.id).as_document()["agreements"]
    app.rollback()
    assert entry["first_charge_owed"] == {
        "not_covered_from": "2026-06-16", "until_day": "2026-07-31",
    }


@pytest.mark.guarantee("G18")
def test_the_first_charge_is_issued_as_an_invoice_paid_like_any_other(app, tenant_id):
    """Issued, it is an ordinary invoice: the first charge's lines, the tax
    the engine gives at its one moment, unpaid until a payment reaches its
    total. Grace runs from its issue; once paid the parker is covered, and a
    second issue is ALREADY_ISSUED with nothing written."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=START),))
    line = issue_first_charge(app, tenant_id, "ag-0001", now=_at(START, 8))
    assert line.outcome is RunOutcome.ISSUED, line
    kinds = [k for (k,) in query(
        app, tenant_id,
        "SELECT l.kind FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "WHERE i.reference = %s", (line.reference,))]
    assert sorted(kinds) == sorted([LineKind.PARTIAL_PERIOD.value, LineKind.FULL_PERIOD.value])
    assert query(app, tenant_id,
                 "SELECT first_period_start_day, second_period_start_day FROM first_charges"
                 ) == [(date(2026, 5, 31), date(2026, 6, 30))]
    assert _covered(app, tenant_id, date(2026, 6, 15))
    assert not _covered(app, tenant_id, date(2026, 6, 16))
    record_payment(app, tenant_id, line.reference, PaymentMethod.CHEQUE, line.total_minor,
                   _at(date(2026, 6, 17)), recorded_by="operator")
    assert _covered(app, tenant_id, date(2026, 6, 18))
    assert _covered(app, tenant_id, date(2026, 7, 30))
    again = issue_first_charge(app, tenant_id, "ag-0001", now=_at(START, 9))
    assert again.outcome is RunOutcome.ALREADY_ISSUED
    assert query(app, tenant_id, "SELECT count(*) FROM first_charges") == [(1,)]


@pytest.mark.guarantee("G18")
def test_the_run_bills_neither_stored_period_and_every_one_after(app, tenant_id):
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=START),))
    issue_first_charge(app, tenant_id, "ag-0001", now=_at(START, 8))
    for day in (date(2026, 6, 15), date(2026, 7, 15)):
        (line,) = run_billing(app, tenant_id, GARAGE.id, day, now=_at(day)).lines
        assert line.outcome is RunOutcome.NOTHING_BILLABLE, (day, line)
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 8, 15),
                          now=_at(date(2026, 7, 31))).lines
    assert line.outcome is RunOutcome.ISSUED


@pytest.mark.guarantee("G18")
def test_a_start_on_the_billing_day_never_takes_the_payers_period_invoice_key(app, tenant_id):
    """The invoice lock is one per payer per day. A first charge is keyed on
    its start day, or the day before when it starts on a period's first day,
    so the payer's other agreement is still invoiced for that period."""
    seed(app, tenant_id, GARAGE, (
        simple_agreement(start_day=date(2026, 1, 5)),
        simple_agreement(id="ag-0002", start_day=date(2026, 6, 30),
                         vehicles=("CAR777",)),
    ))
    first = issue_first_charge(app, tenant_id, "ag-0002", now=_at(date(2026, 6, 30), 1))
    assert first.outcome is RunOutcome.ISSUED, first
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 7, 15),
                          now=_at(date(2026, 6, 30), 2)).lines
    assert line.outcome is RunOutcome.ISSUED, line
    assert query(app, tenant_id,
                 "SELECT period_start_day FROM invoices ORDER BY period_start_day") == [
        (date(2026, 6, 29),), (date(2026, 6, 30),)]
