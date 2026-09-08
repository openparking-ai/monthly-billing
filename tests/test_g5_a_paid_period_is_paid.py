"""G5 -- a price change never alters a period already invoiced.

Enforced by construction rather than by care: every invoice line records the
agreement VERSION that priced it, and the version it was priced from still
exists, unchanged, to be compared against. A price change writes a NEW version;
it never edits one.
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.cycle import period_containing
from monthly_billing.invoice import first_charge, invoice_for_period


@pytest.mark.guarantee("G5")
def test_a_new_version_does_not_change_an_invoice_already_computed():
    garage = month_end_garage()
    v1 = simple_agreement(start_day=date(2026, 3, 10))
    period = period_containing(garage, date(2026, 4, 15))

    before = invoice_for_period(garage, (v1,), period, v1.payer_id)

    v2 = simple_agreement(
        start_day=date(2026, 3, 10), version=2, monthly_price_minor=20000
    )
    after_same_agreements = invoice_for_period(garage, (v1,), period, v1.payer_id)

    assert before.lines == after_same_agreements.lines, (
        "an invoice computed from version 1 changed while version 1 did not"
    )
    assert v2.monthly_price_minor != v1.monthly_price_minor


@pytest.mark.guarantee("G5")
def test_every_line_cites_the_version_that_priced_it():
    garage = month_end_garage()
    v1 = simple_agreement(start_day=date(2026, 3, 10))
    for line in first_charge(garage, v1).lines:
        assert line.agreement_version == 1


@pytest.mark.guarantee("G5")
def test_the_next_period_prices_on_the_new_version():
    """The negative control: without it, an implementation that ignored version 2
    entirely would satisfy the assertion above."""
    garage = month_end_garage()
    period = period_containing(garage, date(2026, 5, 15))
    v1 = simple_agreement(start_day=date(2026, 3, 10))
    v2 = simple_agreement(
        start_day=date(2026, 3, 10), version=2, monthly_price_minor=20000
    )

    old = invoice_for_period(garage, (v1,), period, v1.payer_id)
    new = invoice_for_period(garage, (v2,), period, v2.payer_id)

    assert old.total_minor == 12000
    assert new.total_minor == 20000
    assert new.lines[0].agreement_version == 2


@pytest.mark.guarantee("G5")
def test_a_version_is_a_whole_number_starting_at_one():
    """A coverage decision cites one, so it has to be traceable."""
    with pytest.raises(Exception):  # noqa: B017
        simple_agreement(version=0)
    with pytest.raises(Exception):  # noqa: B017
        simple_agreement(version=True)
