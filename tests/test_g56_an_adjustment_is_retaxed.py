"""G56 -- tax is the last COMPUTATION on an invoice, not the last row.

After every owner's adjustment on a taxed invoice, each rule's tax lines sum to
the engine's tax for that rule on the non-tax total, at the recorded set: the
one writer appends the adjustment and one compensating line per rule naming the
exception, in one transaction. A waiver to zero leaves no tax. The adjustment's
agreement comes from a non-tax line. The database refuses, at commit, an
adjustment on a taxed invoice that arrives without its tax line -- and accepts
the same raw adjustment on an invoice whose set states no tax (the control).

The trigger counts no rules: it refuses an adjustment that arrives with NO
tax line naming its exception. One line per rule is proven here, by test, and
nowhere in the database.

The agreement pick's non-tax filter is defence in depth -- the database's
``invoice_lines_tax_names_no_agreement`` is what makes a money line carrying a
tax line's null agreement impossible. Its control needs a tax line the pick
would otherwise land on, so one fixture writes a tax line strictly EARLIER
than the priced lines, by an explicit ``created_at``.

Controls plant, one variable each: no compensating lines; the compensation as
the new figure rather than the difference; the commit-time trigger away; and
the agreement pick's non-tax filter away with its order untouched. Each must
go red.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.store.postgres import tenant
from store_harness import NO_TAX, needs_postgres, query
from tax_harness import NOW, TAXED, engine_tax, issue, lines_of, pre_tax, tax_by_rule

pytestmark = needs_postgres


def _exception(reference, amount, n=1, kind=ExceptionKind.CREDIT):
    return OwnerException(
        id=f"adj-{n}", agreement_id=None, invoice_reference=reference, kind=kind,
        recorded_by="owner", recorded_at=NOW + timedelta(days=n), amount_minor=amount,
    )


def _total(app, tenant_id, reference):
    ((total,),) = query(
        app, tenant_id,
        "SELECT sum(l.amount_minor) FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "WHERE i.reference = %s",
        (reference,),
    )
    return int(total)


@pytest.mark.guarantee("G56")
@pytest.mark.parametrize("kind", [ExceptionKind.CREDIT, ExceptionKind.WAIVE_FEE],
                         ids=lambda k: k.value)
def test_every_adjustment_leaves_each_rules_tax_equal_to_the_engines(app, tenant_id, kind):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    reference = line.reference
    assert tax_by_rule(app, tenant_id, reference) == engine_tax(12000)

    # Three adjustments, the middle one a single minor unit, so that at least
    # one rule's rounded figure does not move -- and the sum must still be the
    # engine's on the final base, with no drift across steps.
    for n, amount in enumerate((2000, 1, 3333), start=1):
        before = len(lines_of(app, tenant_id, reference))
        record_invoice_exception(app, tenant_id, _exception(reference, amount, n, kind))
        written = lines_of(app, tenant_id, reference)[before:]
        assert sorted(k for k, *_ in written) == ["exception_adjustment", "tax", "tax"], written
        assert {rule for k, _, rule, *_ in written if k == "tax"} == {"sales", "parking"}
        assert all(names_exception for *_, names_exception, _ in written)
        base = pre_tax(app, tenant_id, reference)
        assert tax_by_rule(app, tenant_id, reference) == engine_tax(base)
        assert _total(app, tenant_id, reference) == base + sum(engine_tax(base).values())
    assert pre_tax(app, tenant_id, reference) == 12000 - 2000 - 1 - 3333


@pytest.mark.guarantee("G56")
def test_a_waiver_to_zero_leaves_no_tax_and_reads_paid(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    state = record_invoice_exception(
        app, tenant_id, _exception(line.reference, 12000, kind=ExceptionKind.WAIVE_FEE)
    )
    assert tax_by_rule(app, tenant_id, line.reference) == {"sales": 0, "parking": 0}
    assert state.total_minor == 0 and state.paid


@pytest.mark.guarantee("G56")
def test_the_adjustment_names_the_agreement_of_a_priced_line(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    record_invoice_exception(app, tenant_id, _exception(line.reference, 500))
    rows = query(
        app, tenant_id,
        "SELECT kind, agreement_id FROM invoice_lines WHERE kind IN "
        "('full_period', 'exception_adjustment')",
    )
    agreements = {kind: agreement for kind, agreement in rows}
    assert agreements["exception_adjustment"] is not None
    assert agreements["exception_adjustment"] == agreements["full_period"]


def _raw_adjustment(app, tenant_id, reference):
    """An exception and its adjustment line written as the application role,
    past the module, with no tax line. The commit is the test."""
    ((invoice, agreement),) = query(
        app, tenant_id,
        "SELECT i.id, l.agreement_id FROM invoices i JOIN invoice_lines l "
        "ON l.invoice_id = i.id AND l.kind = 'full_period' WHERE i.reference = %s",
        (reference,),
    )
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO owner_exceptions (tenant_id, invoice_id, kind, recorded_by, "
            "recorded_at, amount_minor) VALUES (%s, %s, 'credit', 'raw', now(), 100) "
            "RETURNING id",
            (tenant_id, invoice),
        )
        (exception,) = cursor.fetchone()
        cursor.execute(
            "INSERT INTO invoice_lines (tenant_id, invoice_id, kind, label, amount_minor, "
            "agreement_id, agreement_version, period_start_day, period_end_day, exception_id) "
            "VALUES (%s, %s, 'exception_adjustment', 'raw', -100, %s, 1, '2026-04-30', "
            "'2026-05-31', %s)",
            (tenant_id, invoice, agreement, exception),
        )
    app.commit()


@pytest.mark.guarantee("G56")
def test_the_database_refuses_an_adjustment_on_a_taxed_invoice_without_its_tax(
    app, tenant_id
):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    with pytest.raises(psycopg.errors.CheckViolation) as refused:
        _raw_adjustment(app, tenant_id, line.reference)
    app.rollback()
    assert refused.value.diag.constraint_name == "invoice_lines_adjustment_is_retaxed"
    assert query(app, tenant_id, "SELECT count(*) FROM owner_exceptions") == [(0,)]


@pytest.mark.guarantee("G56")
def test_the_control_the_same_raw_adjustment_commits_where_no_tax_is_stated(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=NO_TAX).lines
    _raw_adjustment(app, tenant_id, line.reference)
    assert query(
        app, tenant_id, "SELECT count(*) FROM invoice_lines WHERE kind = 'exception_adjustment'"
    ) == [(1,)]


@pytest.mark.guarantee("G56")
def test_an_invoice_issued_untaxed_before_0006_is_adjusted_with_no_tax(app, tenant_id):
    """A row with no tax position -- the honest value of an invoice issued
    before the migration -- takes an adjustment and gains no tax line."""
    issue(app, tenant_id, sets=NO_TAX)
    ((garage, payer, agreement),) = query(
        app, tenant_id,
        "SELECT a.garage_id, a.payer_id, a.id FROM agreements a",
    )
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO invoices (tenant_id, reference, payer_id, garage_id, currency, "
            "period_start_day, due_at) VALUES (%s, 'legacy-1', %s, %s, 'USD', '2026-02-28', "
            "now()) RETURNING id",
            (tenant_id, payer, garage),
        )
        (invoice,) = cursor.fetchone()
        cursor.execute(
            "INSERT INTO invoice_lines (tenant_id, invoice_id, kind, label, amount_minor, "
            "agreement_id, agreement_version, period_start_day, period_end_day) VALUES "
            "(%s, %s, 'full_period', 'legacy', 12000, %s, 1, '2026-02-28', '2026-03-31')",
            (tenant_id, invoice, agreement),
        )
    app.commit()
    state = record_invoice_exception(app, tenant_id, _exception("legacy-1", 2000))
    assert state.total_minor == 10000
    assert tax_by_rule(app, tenant_id, "legacy-1") == {}


def _earlier_tax_line(app, tenant_id, reference):
    """A zero tax line written past the module, strictly EARLIER than every
    line the issue wrote, by an explicit ``created_at`` -- so that a pick in
    ``created_at, id`` order with no non-tax filter lands on it. Zero moves no
    money and no rule's figure; the fixture changes the ORDER and nothing else."""
    ((invoice, earliest),) = query(
        app, tenant_id,
        "SELECT i.id, min(l.created_at) FROM invoices i JOIN invoice_lines l "
        "ON l.invoice_id = i.id WHERE i.reference = %s GROUP BY i.id",
        (reference,),
    )
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "INSERT INTO invoice_lines (tenant_id, invoice_id, kind, label, amount_minor, "
            "period_start_day, period_end_day, tax_rule_id, created_at) VALUES "
            "(%s, %s, 'tax', 'earlier', 0, '2026-04-30', '2026-05-31', 'sales', %s)",
            (tenant_id, invoice, earliest - timedelta(seconds=1)),
        )
    app.commit()
    first = query(
        app, tenant_id,
        "SELECT kind FROM invoice_lines WHERE invoice_id = %s ORDER BY created_at, id LIMIT 1",
        (invoice,),
    )
    assert first == [("tax",)], "the fixture must put a tax line first in the pick's order"


@pytest.mark.guarantee("G56")
def test_the_pick_skips_a_tax_line_that_sorts_first(app, tenant_id):
    """With a tax line first in the pick's own order, a waiver still lands and
    its adjustment carries the PRICED line's agreement."""
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    _earlier_tax_line(app, tenant_id, line.reference)
    record_invoice_exception(
        app, tenant_id, _exception(line.reference, 500, kind=ExceptionKind.WAIVE_FEE)
    )
    rows = query(
        app, tenant_id,
        "SELECT kind, agreement_id FROM invoice_lines WHERE kind IN "
        "('full_period', 'exception_adjustment')",
    )
    agreements = {kind: agreement for kind, agreement in rows}
    assert agreements["exception_adjustment"] is not None
    assert agreements["exception_adjustment"] == agreements["full_period"]
