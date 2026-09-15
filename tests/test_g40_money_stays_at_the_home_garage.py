"""G40 -- money is not multi-garage. Every money door is keyed on the HOME.

**THE TRAP THIS ROUND COULD SHIP, AND IT IS A MONEY ONE.** ``load_agreements_at_garage``
has three callers that do not want the same thing: the billing run and the
charge are MONEY doors and must keep returning only the agreements BILLED at the
garage; the store-backed coverage call is the ACCESS door and is the one that
widened. Widening the shared loader would invoice one agreement at every garage
it is valid at. So the access door is a second function, and the tests here
prove BY EXECUTION that a run at a covered-but-not-home garage issues nothing
for the agreement -- with a payer who has another agreement billed there, so
the widened loader would have had somewhere to put the lines, and with a payer
who has not, so the widened payer read would have found a payer to report.

The reason money stays home is not caution: the billing day, currency, timezone
and grace are stated per garage with no defaults, and a multi-garage agreement
has no rule for which one governs. Inventing one would price somebody. The
fixture's two garages differ on all of them, and the invoice the home issues is
asserted to carry the HOME's currency and the HOME's period.

Controls: the money loader widened to the covered set; the payer read widened;
``_same_garage`` reading the covered set instead of the home; the store filing
an agreement under a garage that is not its home.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import MULTI_HOME, MULTI_OTHER, multi_garage_agreement, simple_agreement
from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.cycle import period_containing
from monthly_billing.findings import REFUSAL_GARAGE_MISMATCH, Refused
from monthly_billing.invoice import first_charge, invoice_for_period
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import store_agreement
from store_harness import needs_postgres, query, seed_garages

HOME = MULTI_HOME()  # Denver, USD, month-end
OTHER = MULTI_OTHER()  # Phoenix, JPY, the 1st
HOME_TZ = ZoneInfo(HOME.timezone)
MULTI = multi_garage_agreement(start_day=date(2026, 1, 5))
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=HOME_TZ)


def store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


# ---------------------------------------------------------------------------
# The pure money doors
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G40")
def test_the_first_charge_refuses_a_covered_but_not_home_garage_by_name():
    """The same refusal as for a garage the agreement has nothing to do with,
    and the detail says which of the two it was."""
    with pytest.raises(Refused) as covered:
        first_charge(OTHER, MULTI)
    assert covered.value.code == REFUSAL_GARAGE_MISMATCH
    assert "covers but is not billed at" in covered.value.detail
    assert f"billed at garage {HOME.id!r}" in covered.value.detail
    stranger = simple_agreement(garage_id="garage-tenth", covered_garage_ids=("garage-tenth",))
    with pytest.raises(Refused) as unrelated:
        first_charge(OTHER, stranger)
    assert unrelated.value.code == REFUSAL_GARAGE_MISMATCH
    assert "covers" not in unrelated.value.detail
    # The control: at the home it prices, in the home's currency and periods.
    invoice = first_charge(HOME, MULTI)
    assert invoice.garage_id == HOME.id and invoice.currency == HOME.currency == "USD"
    assert invoice.issued_for_period_start == period_containing(HOME, MULTI.start_day).start_day


@pytest.mark.guarantee("G40")
def test_the_period_invoice_refuses_a_covered_but_not_home_garage_by_name():
    period = period_containing(OTHER, date(2026, 5, 15))
    with pytest.raises(Refused) as refused:
        invoice_for_period(OTHER, (MULTI,), period, MULTI.payer_id)
    assert refused.value.code == REFUSAL_GARAGE_MISMATCH
    at_home = invoice_for_period(HOME, (MULTI,), period_containing(HOME, date(2026, 5, 15)),
                                 MULTI.payer_id)
    assert at_home.currency == "USD" and len(at_home.lines) == 1


# ---------------------------------------------------------------------------
# The store: the run and the charge
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G40")
@store_backed
def test_a_run_at_a_covered_but_not_home_garage_issues_nothing_for_the_agreement(app, tenant_id):
    """The payer ALSO has an agreement billed at OTHER, so a widened loader
    would have put MULTI's lines on that invoice. The invoice at OTHER carries
    ag-other's line only, in OTHER's currency; MULTI's invoice is at the home."""
    other_agreement = simple_agreement(
        id="ag-other", garage_id=OTHER.id, covered_garage_ids=(OTHER.id,),
        vehicles=("OTHER-1",), monthly_price_minor=5000, start_day=date(2026, 1, 5),
    )
    assert other_agreement.payer_id == MULTI.payer_id
    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI, other_agreement), now=NOW)

    at_other = run_billing(app, tenant_id, OTHER.id, date(2026, 5, 15), now=NOW)
    (line,) = at_other.lines
    assert line.outcome is RunOutcome.ISSUED and line.total_minor == 5000, line
    lines = query(
        app, tenant_id,
        "SELECT a.external_id, l.amount_minor, i.currency, g.external_id "
        "FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "JOIN agreements a ON a.id = l.agreement_id JOIN garages g ON g.id = i.garage_id "
        "ORDER BY a.external_id",
    )
    assert lines == [("ag-other", 5000, "JPY", OTHER.id)], (
        "the run at the covered-but-not-home garage priced the multi-garage agreement"
    )

    at_home = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW)
    (home_line,) = at_home.lines
    assert home_line.outcome is RunOutcome.ISSUED and home_line.total_minor == 12000
    assert query(
        app, tenant_id,
        "SELECT g.external_id, i.currency, i.period_start_day FROM invoices i "
        "JOIN garages g ON g.id = i.garage_id JOIN invoice_lines l ON l.invoice_id = i.id "
        "JOIN agreements a ON a.id = l.agreement_id WHERE a.external_id = %s",
        (MULTI.id,),
    ) == [(HOME.id, "USD", date(2026, 4, 30))], (
        "MULTI's invoice is at its home, in the home's currency, on the home's period"
    )


@pytest.mark.guarantee("G40")
@store_backed
def test_a_run_at_a_covered_but_not_home_garage_finds_no_payer_when_nothing_is_billed_there(
    app, tenant_id
):
    """The payer's only agreement is MULTI. At OTHER there is no payer to
    report -- not NOTHING_BILLABLE, no line at all -- because the payer read is
    keyed on the home too."""
    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI,), now=NOW)
    report = run_billing(app, tenant_id, OTHER.id, date(2026, 5, 15), now=NOW)
    assert report.lines == (), report.rendered()
    assert "no payer has an agreement here" in report.rendered()
    assert query(app, tenant_id, "SELECT count(*) FROM invoices") == [(0,)]
    # The control: the same run at the home issues it.
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    assert line.outcome is RunOutcome.ISSUED


@pytest.mark.guarantee("G40")
@store_backed
def test_the_homes_invoice_is_charged_through_the_home_loader(app, tenant_id):
    """The charge reads the agreements billed at the invoice's garage. For a
    multi-garage agreement that is the home, and the mandate gate finds it."""
    from monthly_billing.charging import attempt_charge

    class Succeeds:
        def __init__(self) -> None:
            self.amounts: list[int] = []

        def charge(self, request):
            self.amounts.append(request.amount_minor)
            return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-1")

    seed_garages(app, tenant_id, (HOME, OTHER), (MULTI,), now=NOW)
    (line,) = run_billing(app, tenant_id, HOME.id, date(2026, 5, 15), now=NOW).lines
    processor = Succeeds()
    outcome = attempt_charge(
        app, tenant_id, processor, line.reference, recorded_by="cron", now=NOW + timedelta(hours=1)
    )
    assert outcome.paid is not None and outcome.paid.paid
    assert processor.amounts == [12000]
    assert query(app, tenant_id, "SELECT currency FROM payments") == [("USD",)]


@pytest.mark.guarantee("G40")
@store_backed
def test_the_store_refuses_to_file_an_agreement_under_a_garage_that_is_not_its_home(
    app, tenant_id
):
    seeded = seed_garages(app, tenant_id, (HOME, OTHER), (), now=NOW)
    try:
        with tenant(app, tenant_id) as cursor:
            from monthly_billing.store.records import store_payer

            payer = store_payer(cursor, tenant_id, MULTI.payer_id, "Acme")
            with pytest.raises(Refused) as refused:
                store_agreement(
                    cursor, tenant_id, OTHER, seeded.garage_uuids[OTHER.id], payer, MULTI, now=NOW
                )
            assert refused.value.code == REFUSAL_GARAGE_MISMATCH
            assert "stored under" in refused.value.detail
            cursor.execute("SELECT count(*) FROM agreements")
            assert cursor.fetchone() == (0,)
            # The control: under its home it is stored.
            store_agreement(
                cursor, tenant_id, HOME, seeded.garage_uuids[HOME.id], payer, MULTI, now=NOW
            )
    except BaseException:
        app.rollback()  # a red here is this test's alone; see test_g39
        raise
    app.commit()
    assert query(app, tenant_id, "SELECT count(*) FROM agreements") == [(1,)]
