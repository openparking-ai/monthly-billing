"""G50 -- an invoice's subtotal is its issue-time non-tax lines, held by the database.

The run writes ``subtotal_minor`` beside the tax lines, and migration 0006
checks it at COMMIT against the non-tax lines written in the issuing
transaction. A raw insert whose subtotal disagrees is refused by name; the same
insert with the right subtotal commits (the control on the refusal). The three
tax-position columns are all present or all absent. A later adjustment moves the
current pre-tax total and never the recorded one. Controls plant the trigger
away and the run's subtotal wrong, and require red.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest

from monthly_billing.billing_run import RunOutcome
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.store.postgres import tenant
from store_harness import needs_postgres, query
from tax_harness import NOW, TAXED, engine_tax, issue, pre_tax

pytestmark = needs_postgres


def _row(app, tenant_id, reference):
    ((subtotal, effective_from, seen),) = query(
        app, tenant_id,
        "SELECT subtotal_minor, tax_set_effective_from, tax_sets_seen FROM invoices "
        "WHERE reference = %s",
        (reference,),
    )
    return subtotal, effective_from, seen


@pytest.mark.guarantee("G50")
def test_the_run_records_the_subtotal_the_set_and_the_count(app, tenant_id):
    (line,) = issue(app, tenant_id).lines
    assert line.outcome is RunOutcome.ISSUED, line.detail
    subtotal, effective_from, seen = _row(app, tenant_id, line.reference)
    assert subtotal == pre_tax(app, tenant_id, line.reference) == 12000
    assert effective_from == datetime.fromisoformat(TAXED[0]["effective_from"])
    assert seen == 1
    assert line.total_minor == subtotal + sum(engine_tax(12000).values())


def _raw_invoice(app, tenant_id, *, subtotal, line_amount, with_set=True):
    """An invoice and one line, written as the application role in ONE
    transaction, past the module. Returns nothing; the commit is the test."""
    ((garage, payer, agreement),) = query(
        app, tenant_id,
        "SELECT g.id, a.payer_id, a.id FROM agreements a JOIN garages g ON g.id = a.garage_id",
    )
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
            "period_start_day, due_at, subtotal_minor, tax_set_effective_from, tax_sets_seen) "
            "VALUES (%s, 'raw-1', %s, %s, 'USD', '2026-07-31', now(), %s, "
            "%s::timestamptz, %s) RETURNING id",
            (tenant_id, payer, garage, subtotal,
             "2026-01-01T00:00:00-07:00" if with_set else None, 1 if with_set else None),
        )
        (invoice,) = cursor.fetchone()
        cursor.execute(
            "INSERT INTO invoice_lines (tenant_id, invoice_id, kind, label, amount_minor, "
            "agreement_id, agreement_version, period_start_day, period_end_day) "
            "VALUES (%s, %s, 'full_period', 'raw', %s, %s, 1, '2026-07-31', '2026-08-31')",
            (tenant_id, invoice, line_amount, agreement),
        )
    app.commit()


@pytest.mark.guarantee("G50")
def test_the_database_refuses_a_subtotal_that_is_not_the_issued_lines(app, tenant_id):
    issue(app, tenant_id)
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        _raw_invoice(app, tenant_id, subtotal=999, line_amount=12000)
    app.rollback()
    assert refused.value.diag.constraint_name == "invoices_subtotal_is_the_issue_lines"
    assert query(app, tenant_id, "SELECT count(*) FROM invoices WHERE reference = 'raw-1'") == [
        (0,)
    ]
    # The control on the refusal: the same insert, subtotal right, commits.
    _raw_invoice(app, tenant_id, subtotal=12000, line_amount=12000)
    assert query(app, tenant_id, "SELECT count(*) FROM invoices WHERE reference = 'raw-1'") == [
        (1,)
    ]


@pytest.mark.guarantee("G50")
def test_the_tax_position_is_all_three_columns_or_none(app, tenant_id):
    issue(app, tenant_id)
    ((garage, payer),) = query(app, tenant_id, "SELECT garage_id, payer_id FROM invoices")
    with tenant(app, tenant_id) as cursor, pytest.raises(psycopg.errors.CheckViolation) as bad:
        cursor.execute(
            "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
            "period_start_day, due_at, subtotal_minor) "
            "VALUES (%s, 'raw-2', %s, %s, 'USD', '2026-08-31', now(), 100)",
            (tenant_id, payer, garage),
        )
    app.rollback()
    assert bad.value.diag.constraint_name == "invoices_tax_position_is_whole"


@pytest.mark.guarantee("G50")
def test_an_adjustment_moves_the_pre_tax_total_and_never_the_recorded_subtotal(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="credit-1", agreement_id=None, invoice_reference=line.reference,
            kind=ExceptionKind.CREDIT, recorded_by="owner",
            recorded_at=NOW + timedelta(days=1), amount_minor=2000,
        ),
    )
    assert _row(app, tenant_id, line.reference)[0] == 12000
    assert pre_tax(app, tenant_id, line.reference) == 10000
