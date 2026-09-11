"""G4 -- the first charge is two separate lines, never one summed figure.

A partial month plus the next full month. The customer can see which part is
which, which is the requirement: people get confused very easily and they get
frustrated, and one number they cannot decompose is where that starts.

**AND THE LINE COUNT IS WHAT MAKES THIS A GUARANTEE RATHER THAN A HABIT.** It
does not depend on the start date. An agreement starting ON the billing day gets
the whole of the first period -- so its first line is a full month, and it is
still its own line. A caller reading `lines[1]` for the following period gets the
same answer whatever the start date, which a first charge that sometimes had one
line and sometimes two would not give them.
"""

from __future__ import annotations

from datetime import date

import pytest

from fixtures import agreement_with_everything, month_end_garage, simple_agreement
from monthly_billing.cycle import period_containing
from monthly_billing.invoice import LineKind, first_charge


@pytest.mark.guarantee("G4")
def test_two_lines_and_they_are_not_summed():
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 10))
    invoice = first_charge(garage, agreement)

    kinds = [line.kind for line in invoice.lines]
    assert kinds == [LineKind.PARTIAL_PERIOD, LineKind.FULL_PERIOD]

    partial, full = invoice.lines
    assert partial.amount_minor != full.amount_minor
    assert full.amount_minor == agreement.monthly_price_minor
    assert invoice.total_minor == partial.amount_minor + full.amount_minor


@pytest.mark.guarantee("G4")
def test_the_partial_line_says_how_many_days_of_how_many():
    """A figure with no derivation beside it is a figure the customer disputes."""
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 10))
    invoice = first_charge(garage, agreement)
    partial = invoice.lines[0]

    period = period_containing(garage, agreement.start_day)
    assert f"of {period.days} days" in partial.label
    assert str(period.start_day) in partial.label


@pytest.mark.guarantee("G4")
def test_starting_on_the_billing_day_still_emits_two_lines():
    """Starting on the billing day charges the whole first period -- as its own line.

    Written first as a ZERO case, and that was wrong: the period containing a
    billing day starts on it, so every day of it is billable. The line count is
    the invariant, not the amount.
    """
    garage = month_end_garage()
    period = period_containing(garage, date(2026, 3, 15))
    agreement = simple_agreement(start_day=period.start_day)

    invoice = first_charge(garage, agreement)
    assert [line.kind for line in invoice.lines] == [
        LineKind.PARTIAL_PERIOD,
        LineKind.FULL_PERIOD,
    ]
    partial = invoice.lines[0]
    assert partial.amount_minor == agreement.monthly_price_minor, (
        "starting on the billing day means the whole period is billable, so the "
        "'partial' line is a full one"
    )


@pytest.mark.guarantee("G4")
def test_starting_the_day_after_the_billing_day_charges_almost_a_whole_period():
    """The other end of the axis, so the zero case above is not the only sample."""
    garage = month_end_garage()
    period = period_containing(garage, date(2026, 3, 15))
    day_after = date(period.start_day.year, period.start_day.month, period.start_day.day)
    agreement = simple_agreement(start_day=period.end_day.replace(day=period.end_day.day - 1))

    invoice = first_charge(garage, agreement)
    partial = invoice.lines[0]
    assert 0 < partial.amount_minor < agreement.monthly_price_minor
    assert day_after == period.start_day  # the fixture really is at the boundary


@pytest.mark.guarantee("G4")
def test_a_recurring_fee_is_prorated_and_a_one_time_fee_is_not():
    """Neither was settled by the specification; both are stated by the code.

    A recurring fee is part of what the account pays every month, so a half month
    of it is half of it. A one-time fee is a thing that happened on a day.
    """
    garage = month_end_garage()
    agreement = agreement_with_everything()
    invoice = first_charge(garage, agreement)

    by_label = {line.label: line for line in invoice.lines}
    reserved_part = next(
        line for label, line in by_label.items()
        if "Reserved space 12" in label and "part month" in label
    )
    reserved_full = next(
        line for label, line in by_label.items()
        if "Reserved space 12" in label and "monthly" in label
    )
    card = next(line for label, line in by_label.items() if "Access card" in label)

    assert reserved_part.amount_minor < 2500
    assert reserved_full.amount_minor == 2500
    assert card.amount_minor == 1500
    assert card.kind is LineKind.ONE_TIME_FEE


@pytest.mark.guarantee("G4")
def test_every_line_names_the_agreement_and_the_version_that_priced_it():
    """The structural half of "a paid period is paid" -- see G5."""
    garage = month_end_garage()
    agreement = agreement_with_everything()
    for line in first_charge(garage, agreement).lines:
        assert line.agreement_id == agreement.id
        assert line.agreement_version == agreement.version
        assert line.period_end_day > line.period_start_day
