"""The tax sets and the issued, taxed invoice the tax guarantees share.

Not a test module (no ``test_`` prefix). Imported by name, so a reader of any
tax test sees where its sets came from.

**THE EXPECTED FIGURES ARE THE ENGINE'S, NEVER TYPED.** A test that wrote "7%
of 12000 is 840" would be a second tax implementation -- in the tests -- free to
agree with a wrong one here. ``engine_tax`` asks ``rate-engine`` what the tax on
an amount is, and the tests compare the invoice's lines with that.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from store_harness import query, seed

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
#: Issued the night before the period containing 1 May starts (the 30th, local).
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
PERIOD_DAY = date(2026, 5, 1)

SALES = {"id": "sales", "label": "Sales tax", "percent_bp": 290, "rounding": "nearest",
         "sequence": 1}
PARKING = {"id": "parking", "label": "Parking tax", "percent_bp": 700, "rounding": "down",
           "sequence": 2}

#: Two rules, in force from the start of the year.
TAXED: list[dict[str, Any]] = [
    {"effective_from": "2026-01-01T00:00:00-07:00", "rules": [SALES, PARKING]},
]

#: A later set at a different rate -- in force from June, so after the May
#: period's due instant and before anything a test does to that invoice later.
LATER: list[dict[str, Any]] = [
    {
        "effective_from": "2026-06-01T00:00:00-06:00",
        "rules": [{**SALES, "percent_bp": 900}, PARKING],
    },
]


def engine_tax(subtotal_minor: int, *, sets=TAXED, at: datetime | None = None) -> dict[str, int]:
    """The engine's tax per rule id on ``subtotal_minor``, at ``at`` (the May
    period's due instant by default). The arithmetic is the engine's."""
    from rate_engine.tax import load_tax_sets, tax_lines

    from monthly_billing.cycle import period_containing

    at = at or period_containing(GARAGE, PERIOD_DAY).start_instant
    lines = tax_lines(load_tax_sets(sets), subtotal_minor=subtotal_minor, currency="USD", at=at)
    return {line.rule_id: line.delta_minor for line in lines}


def issue(app, tenant_id, *, sets=TAXED, price: int = 12000, payers=("payer-1",)):
    """Seed the garage stating ``sets`` and one agreement per payer, and run
    the May period. Returns the run report."""
    agreements = tuple(
        simple_agreement(id=f"ag-{payer}", payer_id=payer, monthly_price_minor=price,
                         start_day=date(2026, 1, 5),
                         vehicles=tuple(f"P{i}CAR{n}" for n in range(2)))
        for i, payer in enumerate(payers)
    )
    seed(app, tenant_id, GARAGE, agreements, tax_sets=sets)
    return run_billing(app, tenant_id, GARAGE.id, PERIOD_DAY, now=NOW)


def lines_of(app, tenant_id, reference: str) -> list[tuple]:
    """(kind, amount, tax_rule_id, exception_id is not null, agreement_id is not null)
    for every line of the invoice, in written order."""
    return query(
        app, tenant_id,
        "SELECT l.kind, l.amount_minor, l.tax_rule_id, l.exception_id IS NOT NULL, "
        "l.agreement_id IS NOT NULL FROM invoice_lines l JOIN invoices i ON i.id = l.invoice_id "
        "WHERE i.reference = %s ORDER BY l.created_at, l.kind = 'tax', l.id",
        (reference,),
    )


def tax_by_rule(app, tenant_id, reference: str) -> dict[str, int]:
    """The sum of each rule's tax lines on the invoice."""
    rows = query(
        app, tenant_id,
        "SELECT l.tax_rule_id, sum(l.amount_minor) FROM invoice_lines l "
        "JOIN invoices i ON i.id = l.invoice_id WHERE i.reference = %s AND l.kind = 'tax' "
        "GROUP BY l.tax_rule_id",
        (reference,),
    )
    return {rule: int(total) for rule, total in rows}


def pre_tax(app, tenant_id, reference: str) -> int:
    ((total,),) = query(
        app, tenant_id,
        "SELECT coalesce(sum(l.amount_minor), 0) FROM invoice_lines l "
        "JOIN invoices i ON i.id = l.invoice_id WHERE i.reference = %s AND l.kind <> 'tax'",
        (reference,),
    )
    return int(total)
