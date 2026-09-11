"""G17 -- the billing run is idempotent by constraint; G18 -- a period has one owner.

**IDEMPOTENT BY CONSTRAINT, PROVEN BY REMOVING THE CONSTRAINT.** The run does
not remember what it issued; the database refuses a second invoice for the same
period. The control for G17 plants migration 0002 with the UNIQUE constraint
deleted and requires the second run to produce a duplicate -- if the run were
idempotent by convention instead, that plant would change nothing and the
control would be dead.

**OWNERSHIP IS BY PERIOD.** The first charge covers the period containing the
start day and the one after; the run covers every later one. The tests here
place one agreement in each situation and read what the run priced, by period.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import (
    RunOutcome,
    invoice_reference,
    owned_by_first_charge,
    run_billing,
)
from monthly_billing.cycle import next_period_after, period_containing
from monthly_billing.invoice import first_charge
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _invoices(app, tenant_id):
    return query(
        app,
        tenant_id,
        "SELECT reference, period_start_day, due_at, paid_at FROM invoices ORDER BY reference",
    )


@pytest.mark.guarantee("G17")
def test_a_second_run_issues_nothing_and_says_so(app, tenant_id):
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))

    first = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    second = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)

    assert [line.outcome for line in first.lines] == [RunOutcome.ISSUED]
    assert [line.outcome for line in second.lines] == [RunOutcome.ALREADY_ISSUED]
    assert not second.refused, "already-issued is an answer, not a refusal"
    rows = _invoices(app, tenant_id)
    assert len(rows) == 1, f"the second run duplicated the invoice: {rows}"
    assert rows[0][0] == invoice_reference(GARAGE, first.period, "payer-acme")


@pytest.mark.guarantee("G17")
def test_both_locks_are_in_the_catalogue(app, tenant_id):
    """TWO INDEPENDENT LOCKS, read from pg_constraint by their COLUMNS, not by
    name: the period lock from 0002 and the reference lock from 0001. Either one
    alone stops a duplicate, which is why a plant removing only one leaves the
    behavioural tests green -- and why this test exists: it goes red for the
    single-lock plant, and the both-locks plant goes red behaviourally."""
    rows = query(
        app, tenant_id,
        """
        SELECT array_agg(a.attname ORDER BY k.ordinality)
        FROM pg_constraint c
        JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conrelid = 'invoices'::regclass AND c.contype = 'u'
        GROUP BY c.oid ORDER BY 1
        """,
    )
    locks = sorted(tuple(row[0]) for row in rows)
    assert locks == [
        ("tenant_id", "garage_id", "payer_id", "period_start_day"),
        # the target of the money-history tables' composite tenant keys, not a lock
        ("tenant_id", "id"),
        ("tenant_id", "reference"),
    ], f"the invoices table carries these unique constraints: {locks}"


@pytest.mark.guarantee("G17")
def test_the_reference_is_derived_and_the_lines_are_the_lock_on_price(app, tenant_id):
    """The same garage, period and payer always get the same reference, and the
    lines of an issued invoice are not re-priced by a later run."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    lines = "SELECT kind, amount_minor, agreement_version FROM invoice_lines"
    before = query(app, tenant_id, lines)
    run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    after = query(app, tenant_id, lines)
    assert before == after == [("full_period", 12000, 1)]


@pytest.mark.guarantee("G17")
def test_due_at_is_the_periods_start_instant_in_the_garages_zone(app, tenant_id):
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    report = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    ((_, period_start, due_at, paid_at),) = _invoices(app, tenant_id)
    assert period_start == report.period.start_day == date(2026, 4, 30)
    assert due_at == report.period.start_instant
    assert due_at.astimezone(TZ).hour == 0, "due at local midnight on the billing day"
    assert paid_at is None


@pytest.mark.guarantee("G17")
def test_one_payers_refusal_does_not_stop_the_others(app, tenant_id):
    """Per payer, and non-zero if any refused.

    The store cannot hold an agreement at the wrong garage (the column is a
    foreign key), so the refusal planted here is one the schema does not
    prevent: two overlapping pauses, which the engine's constructor refuses
    rather than merges. The payer beside it is still issued.
    """
    from monthly_billing.store.postgres import tenant
    from monthly_billing.store.records import store_agreement, store_payer
    from monthly_billing.store.writes import guarded_insert

    fine = simple_agreement(start_day=date(2026, 1, 5))
    seeded = seed(app, tenant_id, GARAGE, (fine,))
    broken = simple_agreement(id="ag-broken", payer_id="payer-broken", start_day=date(2026, 1, 5))
    with tenant(app, tenant_id) as cursor:
        payer = store_payer(cursor, tenant_id, "payer-broken", "Broken")
        agreement_uuid = store_agreement(
            cursor, tenant_id, GARAGE, seeded.garage_uuid, payer, broken
        )
        for from_day, until_day in ((date(2026, 5, 1), date(2026, 5, 20)),
                                    (date(2026, 5, 10), date(2026, 5, 25))):
            guarded_insert(
                cursor, "agreement_pauses",
                {"tenant_id": tenant_id, "agreement_id": agreement_uuid,
                 "from_day": from_day, "until_day": until_day},
            )
    app.commit()

    report = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    outcomes = {line.payer_id: line.outcome for line in report.lines}
    assert outcomes == {"payer-acme": RunOutcome.ISSUED, "payer-broken": RunOutcome.REFUSED}
    assert report.refused
    assert "overlapping pauses" in next(
        line.detail for line in report.lines if line.payer_id == "payer-broken"
    )
    assert [row[0] for row in _invoices(app, tenant_id)] == [
        invoice_reference(GARAGE, report.period, "payer-acme")
    ]


@pytest.mark.guarantee("G17")
def test_a_payer_with_nothing_billable_gets_a_line_and_no_invoice(app, tenant_id):
    from monthly_billing.agreement import Pause

    paused = simple_agreement(
        start_day=date(2026, 1, 5),
        pauses=(Pause(from_day=date(2026, 4, 1), until_day=date(2026, 7, 1)),),
    )
    seed(app, tenant_id, GARAGE, (paused,))
    report = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    assert [line.outcome for line in report.lines] == [RunOutcome.NOTHING_BILLABLE]
    assert _invoices(app, tenant_id) == []


# ---------------------------------------------------------------------------
# G18 -- ownership by period
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G18")
def test_the_first_charge_owns_exactly_the_two_periods_it_emits():
    """Derived from ``first_charge`` itself, not from a list of dates: the rule
    says yes for precisely the periods the first charge's lines cover."""
    for start in (date(2026, 3, 10), date(2026, 3, 31), date(2026, 4, 29)):
        agreement = simple_agreement(start_day=start)
        covered = {line.period_start_day for line in first_charge(GARAGE, agreement).lines}
        assert len(covered) == 2, "the first charge covers two periods whatever it emits"
        period = period_containing(GARAGE, start)
        seen = set()
        for _ in range(5):
            if owned_by_first_charge(GARAGE, agreement, period):
                seen.add(period.start_day)
            period = next_period_after(GARAGE, period)
        assert seen == covered


@pytest.mark.guarantee("G18")
def test_the_run_prices_neither_first_charge_period_and_every_one_after(app, tenant_id):
    """One agreement starting 2026-04-10: the first charge owns Mar-31..Apr-30 and
    Apr-30..May-31. The run for May issues nothing for it; the run for June does."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 4, 10)),))

    may = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    assert [line.outcome for line in may.lines] == [RunOutcome.NOTHING_BILLABLE]
    assert "owned by the first charge" in may.lines[0].detail

    june = run_billing(app, tenant_id, GARAGE.id, date(2026, 6, 1), now=NOW)
    assert [line.outcome for line in june.lines] == [RunOutcome.ISSUED]
    rows = _invoices(app, tenant_id)
    assert [row[1] for row in rows] == [date(2026, 5, 31)]


@pytest.mark.guarantee("G18")
def test_ownership_is_per_agreement_within_one_payers_invoice(app, tenant_id):
    """A payer with an old agreement and a brand-new one: the run's invoice for
    May carries the old agreement's line and nothing for the new one."""
    old = simple_agreement(start_day=date(2026, 1, 5))
    new = simple_agreement(
        id="ag-0002", start_day=date(2026, 4, 10),
        vehicles=tuple(f"NEW{n:03d}" for n in range(3)),
    )
    seed(app, tenant_id, GARAGE, (old, new))
    report = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW)
    assert [line.outcome for line in report.lines] == [RunOutcome.ISSUED]
    lines = query(
        app, tenant_id,
        "SELECT a.external_id FROM invoice_lines l JOIN agreements a ON a.id = l.agreement_id",
    )
    assert lines == [("ag-0001",)]


@pytest.mark.guarantee("G17")
def test_the_command_line_runs_it_and_exits_by_the_report(app, tenant_id, capsys):
    """The operator's platform calls a command; the command is the same run,
    and its exit code is the report's: zero on issued or already-issued,
    non-zero only when a payer was refused."""
    from monthly_billing.cli import main
    from store_harness import APP_PASSWORD, DSN

    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    argv = [
        "run", "--tenant", str(tenant_id), "--garage", GARAGE.id,
        "--period-containing", "2026-05-01", "--now", NOW.isoformat(),
        "--dsn", f"{DSN} user=monthly_billing_app password={APP_PASSWORD}",
    ]
    assert main(argv) == 0
    assert "payer-acme: ISSUED" in capsys.readouterr().out
    assert main(argv) == 0
    assert "payer-acme: ALREADY_ISSUED" in capsys.readouterr().out
    assert len(_invoices(app, tenant_id)) == 1
