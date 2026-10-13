"""G55 -- this module computes no percentage; ``rate-engine`` is the only tax arithmetic.

Three proofs. The engine's answer is replaced with a figure no percentage of the
subtotal could produce, and the invoice carries exactly that figure -- so the
amount came from the engine and nowhere else. ``tax.py``'s source has no
multiplicative operator, and no other source file imports the engine. And in a
fresh interpreter where ``rate_engine`` cannot be imported, the entitlement
answer and the invoice arithmetic still work, while tax is refused by name.
Controls plant a percentage into the seam, and an engine import into the
entitlement path, and require red.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.cycle import period_containing
from monthly_billing.invoice import LineKind, invoice_for_period
from monthly_billing.tax import tax_an_invoice
from tax_harness import TAXED

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "monthly_billing"
GARAGE = month_end_garage()
PERIOD = period_containing(GARAGE, date(2026, 5, 1))
#: No percentage of 12000 with a whole number of basis points is 4321.
ODD = 4321


@pytest.mark.guarantee("G55")
def test_every_tax_amount_is_the_engines_answer_and_nothing_else(monkeypatch):
    from rate_engine import tax as engine_tax
    from rate_engine.breakdown import Line

    def answered(sets, *, subtotal_minor, currency, at):
        engine_tax.select_tax_set(sets, at)
        return [Line(code="tax.applied", rule_id="sales", text="the engine's answer",
                     delta_minor=ODD)]

    monkeypatch.setattr(engine_tax, "tax_lines", answered)
    agreement = simple_agreement(start_day=date(2026, 1, 5))
    untaxed = invoice_for_period(GARAGE, (agreement,), PERIOD, agreement.payer_id)
    taxed = tax_an_invoice(untaxed, sets=TAXED, at=PERIOD.start_instant).invoice
    (tax,) = [line for line in taxed.lines if line.kind is LineKind.TAX]
    assert tax.amount_minor == ODD and tax.label == "the engine's answer"


_MULTIPLICATIVE = (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)


@pytest.mark.guarantee("G55")
def test_the_seam_has_no_multiplicative_operator_and_is_the_only_importer():
    tree = ast.parse((SRC / "tax.py").read_text())
    found = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(node.op, _MULTIPLICATIVE)
    ]
    assert not found, f"tax.py computes something multiplicative at {found}"

    importers = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            )
            if any(name.split(".")[0] == "rate_engine" for name in names):
                importers.append(path.relative_to(SRC).as_posix())
    assert sorted(set(importers)) == ["tax.py"], importers


_WITHOUT_THE_ENGINE = textwrap.dedent(
    """
    import sys
    sys.modules["rate_engine"] = None  # any import of it now raises ImportError
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    sys.path.insert(0, "tests")
    from fixtures import month_end_garage, simple_agreement
    import monthly_billing.billing_run, monthly_billing.cli, monthly_billing.entitlement
    import monthly_billing.entitlement_store, monthly_billing.exceptions_store
    from monthly_billing.cycle import period_containing
    from monthly_billing.entitlement import is_covered
    from monthly_billing.findings import Refused
    from monthly_billing.invoice import first_charge, invoice_for_period
    from monthly_billing.tax import tax_an_invoice
    garage = month_end_garage()
    agreement = simple_agreement()
    answer = is_covered(garage=garage, agreements=(agreement,), vehicle_identity="CAR001",
                        at=datetime(2026, 4, 1, 9, tzinfo=ZoneInfo(garage.timezone)))
    assert answer.covered, answer
    period = period_containing(garage, date(2026, 5, 1))
    invoice = invoice_for_period(garage, (agreement,), period, agreement.payer_id)
    assert invoice.total_minor > 0 and first_charge(garage, agreement).lines
    try:
        tax_an_invoice(invoice, sets=[], at=period.start_instant)
    except Refused as refused:
        print(refused.code)
    """
)


@pytest.mark.guarantee("G55")
def test_without_the_engine_everything_but_tax_works_and_tax_is_refused_by_name():
    done = subprocess.run(
        [sys.executable, "-c", _WITHOUT_THE_ENGINE], cwd=ROOT, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "REFUSAL_TAX_NEEDS_THE_ENGINE"
