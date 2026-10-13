"""G54 -- the first charge's two periods take ONE tax moment. Function-level.

The first charge is computed from documents and persisted nowhere, so this is
proven on the function and the command that print it. A set change landing
between its two periods prices neither: the whole invoice is taxed once, at the
start of the FIRST period it pays for. Without ``--tax-sets`` the output says it
is untaxed. Controls plant the moment at the second period, and the UNTAXED
sentence away, and require red.
"""

from __future__ import annotations

import json

import pytest

from monthly_billing.cli import main
from monthly_billing.cycle import next_period_after, period_containing
from monthly_billing.invoice import LineKind, first_charge, first_charge_due_at
from monthly_billing.tax import tax_an_invoice
from tax_harness import engine_tax

GARAGE_DOC = "tests/documents/garage_downtown.json"
AGREEMENT_DOC = "tests/documents/agreement_acme.json"
RULE = {"id": "sales", "label": "Sales tax", "rounding": "nearest", "sequence": 1}


def _documents():
    from monthly_billing.agreement import load_agreement_file
    from monthly_billing.cli import load_garage_file

    return load_garage_file(GARAGE_DOC), load_agreement_file(AGREEMENT_DOC)


def _split_sets():
    """One set in force at the first period's start, another from the second's."""
    garage, agreement = _documents()
    first = period_containing(garage, agreement.start_day)
    second = next_period_after(garage, first)
    return [
        {"effective_from": "2026-01-01T00:00:00-07:00", "rules": [{**RULE, "percent_bp": 290}]},
        {"effective_from": second.start_instant.isoformat(),
         "rules": [{**RULE, "percent_bp": 900}]},
    ], first


@pytest.mark.guarantee("G54")
def test_the_whole_first_charge_is_taxed_once_at_its_first_periods_start():
    garage, agreement = _documents()
    sets, first = _split_sets()
    assert first_charge_due_at(garage, agreement) == first.start_instant

    untaxed = first_charge(garage, agreement)
    periods = {line.period_start_day for line in untaxed.lines}
    assert len(periods) == 2, "the fixture must span two periods"
    taxed = tax_an_invoice(untaxed, sets=sets, at=first_charge_due_at(garage, agreement))
    tax = [line for line in taxed.invoice.lines if line.kind is LineKind.TAX]
    assert len(tax) == 1, "one rule, one moment: exactly one tax line"
    assert {tax[0].tax_rule_id: tax[0].amount_minor} == engine_tax(
        untaxed.total_minor, sets=sets, at=first.start_instant
    )
    assert taxed.position.tax_set_effective_from.isoformat() == "2026-01-01T00:00:00-07:00"


@pytest.mark.guarantee("G54")
def test_the_command_taxes_once_and_says_when(tmp_path, capsys):
    sets, first = _split_sets()
    path = tmp_path / "sets.json"
    path.write_text(json.dumps(sets))
    assert main(["first-charge", "--garage", GARAGE_DOC, "--agreement", AGREEMENT_DOC,
                 "--tax-sets", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"taxed once at {first.start_instant.isoformat()}" in out
    assert out.count("Sales tax:") == 1
    assert "UNTAXED" not in out


@pytest.mark.guarantee("G54")
def test_without_tax_sets_the_command_says_the_figure_is_untaxed(capsys):
    assert main(["first-charge", "--garage", GARAGE_DOC, "--agreement", AGREEMENT_DOC]) == 0
    out = capsys.readouterr().out
    assert "UNTAXED" in out and "taxed once" not in out
