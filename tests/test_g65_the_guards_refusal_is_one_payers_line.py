"""G65 -- the instrument guard refuses one payer's invoice, never the whole run.

**THE REFERENCE IS DERIVED FROM THE GARAGE ID.** An invoice's reference is the
garage id, the period's first day and the payer id, joined by slashes. For a
garage id ending in nine digits that pass the bank routing checksum, every
reference at that garage reads as a routing number followed by an account
number -- the year is the account -- and the store's guard refuses that shape on
every row, rightly, and is not loosened for references. The run did not catch
the guard's refusal: it stopped at its first payer with nothing issued, the
payers after it got no line at all, and the command printed no report.

Now a refusal by the guard is that payer's REFUSED line, as every other refusal
is: nothing is written for it and the run goes on to the next payer. The guard
and the garage are as they were -- such a garage is still stored, and its
invoices are still refused, each by name. THE CONTROL: ids that keep nine digits
the guard passes -- a checksum that fails, digits that are not the end of the
id -- are billed, so the refusal is the guard's judgement and not a rule about
digits.

Neither the routing-shaped digits nor anything containing them is written in
this file: they are BUILT from the checksum, for the reason sensitive.py gives.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.cli import main
from monthly_billing.sensitive import routing_checksum_ok
from store_harness import APP_PASSWORD, DSN, needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _nine_digits(*, routing: bool) -> str:
    """Nine digits whose last one does, or does not, complete the routing
    checksum -- computed, never written down."""
    body = "12345678"
    for check in "0123456789":
        if routing_checksum_ok(body + check) is routing:
            return body + check
    raise AssertionError("every check digit gave the same answer, which is impossible")


ROUTING = _nine_digits(routing=True)
NOT_ROUTING = _nine_digits(routing=False)


def _two_payers_at(app, tenant_id, garage_id: str) -> str:
    """A garage of that id, stored as any garage is, with two payers, each with
    an agreement billed there. Its id."""
    garage = replace(GARAGE, id=garage_id)
    seed(app, tenant_id, garage, tuple(
        simple_agreement(
            id=f"ag-{payer}", payer_id=payer, garage_id=garage_id,
            start_day=date(2026, 1, 5), vehicles=(f"P{n}CAR0",),
        )
        for n, payer in enumerate(("payer-a", "payer-b"))
    ))
    return garage_id


@pytest.mark.guarantee("G65")
def test_the_guards_refusal_is_each_payers_line_and_the_run_goes_on(app, tenant_id):
    garage_id = _two_payers_at(app, tenant_id, f"lot-{ROUTING}")
    report = run_billing(app, tenant_id, garage_id, date(2026, 5, 1), now=NOW)
    assert [(line.payer_id, line.outcome) for line in report.lines] == [
        ("payer-a", RunOutcome.REFUSED),
        ("payer-b", RunOutcome.REFUSED),
    ], "a payer was not given its line -- the guard's refusal stopped the run"
    assert report.refused
    for line in report.lines:
        assert "invoices.reference contains a bank routing number" in line.detail, line.detail
        assert ROUTING not in line.detail, "the line echoed the digits the guard found"
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(0,)]
    assert query(app, tenant_id, "SELECT count(*) FROM invoice_lines") == [(0,)]


@pytest.mark.guarantee("G65")
def test_the_command_line_prints_the_report_and_exits_one(app, tenant_id, capsys):
    """The run's own exit for a refused payer (1), with every payer's line --
    never the whole command refused on the first payer (2) with no report."""
    garage_id = _two_payers_at(app, tenant_id, f"lot-{ROUTING}")
    dsn = f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"
    code = main(["run", "--tenant", str(tenant_id), "--garage", garage_id,
                 "--period-containing", "2026-05-01", "--now", NOW.isoformat(), "--dsn", dsn])
    out = capsys.readouterr()
    assert code == 1, (out.out, out.err)
    assert "payer-a: REFUSED" in out.out and "payer-b: REFUSED" in out.out, out.out


@pytest.mark.guarantee("G65")
@pytest.mark.parametrize(
    "garage_id",
    [f"lot-{NOT_ROUTING}", f"lot-{ROUTING}-east"],
    ids=["nine-digits-not-a-routing-number", "routing-digits-not-at-the-end"],
)
def test_the_control_an_id_the_guard_passes_is_billed(app, tenant_id, garage_id):
    _two_payers_at(app, tenant_id, garage_id)
    report = run_billing(app, tenant_id, garage_id, date(2026, 5, 1), now=NOW)
    assert [line.outcome for line in report.lines] == [RunOutcome.ISSUED] * 2, (
        report.rendered()
    )
