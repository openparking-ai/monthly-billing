"""G53 -- an issued invoice is never re-priced against another tax set.

A set stated after issue, later or BACKDATED to before ``due_at``, changes
nothing on it: a re-run is ALREADY_ISSUED with the lines and tax position as
issued, and an adjustment re-taxes at the set the invoice RECORDED. The three
tax-position columns refuse an UPDATE by name; ``paid_at`` stays writable.
Controls plant "re-tax at today's newest set" and the immutability trigger away.
"""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from monthly_billing.billing_run import RunOutcome, run_billing
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_invoice_exception
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import load_garage
from monthly_billing.store.tax_sets import state_tax_sets
from store_harness import needs_postgres, query
from tax_harness import (
    GARAGE,
    LATER,
    NOW,
    PERIOD_DAY,
    SALES,
    TAXED,
    engine_tax,
    issue,
    lines_of,
    pre_tax,
    tax_by_rule,
)

pytestmark = needs_postgres

#: Backdated: in force from February, so it WOULD be the set at the May
#: invoice's due instant if anything re-selected -- at a different rate.
BACKDATED = [{"effective_from": "2026-02-01T00:00:00-07:00",
              "rules": [{**SALES, "percent_bp": 1500}]}]


def _state(app, tenant_id, sets):
    with tenant(app, tenant_id) as cursor:
        state_tax_sets(cursor, tenant_id, load_garage(cursor, GARAGE.id).uuid, sets,
                       stated_by="owner")
    app.commit()


def _position(app, tenant_id, reference):
    return query(
        app, tenant_id,
        "SELECT subtotal_minor, tax_set_effective_from, tax_sets_seen FROM invoices "
        "WHERE reference = %s",
        (reference,),
    )


@pytest.mark.guarantee("G53")
@pytest.mark.parametrize("stated_after", [LATER, BACKDATED], ids=["later", "backdated"])
def test_a_set_stated_after_issue_changes_nothing_on_a_rerun(app, tenant_id, stated_after):
    (first,) = issue(app, tenant_id, sets=TAXED).lines
    lines_before = lines_of(app, tenant_id, first.reference)
    position_before = _position(app, tenant_id, first.reference)
    _state(app, tenant_id, stated_after)

    (again,) = run_billing(app, tenant_id, GARAGE.id, PERIOD_DAY, now=NOW).lines
    assert again.outcome is RunOutcome.ALREADY_ISSUED, again.detail
    assert lines_of(app, tenant_id, first.reference) == lines_before
    assert _position(app, tenant_id, first.reference) == position_before


@pytest.mark.guarantee("G53")
def test_an_adjustment_after_a_backdated_set_retaxes_at_the_recorded_set(app, tenant_id):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    _state(app, tenant_id, BACKDATED)
    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="credit-1", agreement_id=None, invoice_reference=line.reference,
            kind=ExceptionKind.CREDIT, recorded_by="owner",
            recorded_at=NOW + timedelta(days=1), amount_minor=3000,
        ),
    )
    base = pre_tax(app, tenant_id, line.reference)
    assert base == 9000
    assert tax_by_rule(app, tenant_id, line.reference) == engine_tax(base, sets=TAXED)
    assert engine_tax(base, sets=TAXED) != engine_tax(base, sets=TAXED + BACKDATED), (
        "the fixture must make the two sets disagree, or this test proves nothing"
    )


@pytest.mark.guarantee("G53")
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("subtotal_minor", "1"),
        ("tax_set_effective_from", "'2000-01-01T00:00:00Z'"),
        ("tax_sets_seen", "7"),
    ],
)
def test_the_tax_position_columns_refuse_an_update_by_name(app, tenant_id, column, value):
    (line,) = issue(app, tenant_id, sets=TAXED).lines
    with tenant(app, tenant_id) as cursor, pytest.raises(psycopg.errors.CheckViolation) as bad:
        cursor.execute(
            f"UPDATE invoices SET {column} = {value} WHERE reference = %s", (line.reference,)
        )
    app.rollback()
    assert bad.value.diag.constraint_name == "invoices_tax_position_is_immutable"
    assert column in str(bad.value)
    # The control: the derived column the grant exists for still writes.
    with tenant(app, tenant_id) as cursor:
        cursor.execute(
            "UPDATE invoices SET paid_at = now() WHERE reference = %s", (line.reference,)
        )
    app.rollback()
