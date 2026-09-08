"""G10 -- an exception records who and when, and a NOTE CAN NEVER CHANGE MONEY.

A sibling module in this project shipped a version where a free-text decision
note could buy a surcharge. A note EXPLAINS; an amount CHANGES MONEY; they are
different fields and the amount is typed.

**THE CONTROL IS A PLANT, NOT A READING.** Proving "nothing reads the note" by
reading the source proves the source says so. This plants a note carrying every
shape that might be parsed out of one -- a number, an amount with a currency
symbol, a word an implementation might switch on -- and requires every figure the
module produces to be UNCHANGED.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monthly_billing.exceptions_by_owner import (
    ExceptionKind,
    OwnerException,
    applied_grace_days,
    is_blocked,
    monetary_total_minor,
)
from monthly_billing.findings import REFUSAL_EXCEPTION_HAS_NO_AMOUNT, Refused

NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

#: Notes an implementation might be tempted to parse. Every one of them must
#: change nothing.
DANGEROUS_NOTES = [
    "credit 1000",
    "waive $10.00",
    "refund 2500 minor units",
    "amount_minor=9999",
    "block",
    "extend_grace 30",
    "the customer paid 4000 in cash",
]


def exception(**overrides):
    fields = {
        "id": "ex-1",
        "agreement_id": "ag-1",
        "invoice_reference": None,
        "kind": ExceptionKind.CREDIT,
        "recorded_by": "the owner",
        "recorded_at": NOW,
        "amount_minor": 1000,
    }
    fields.update(overrides)
    return OwnerException(**fields)


@pytest.mark.guarantee("G10")
def test_an_exception_records_who_and_when():
    ex = exception()
    assert ex.recorded_by == "the owner"
    assert ex.recorded_at == NOW


@pytest.mark.guarantee("G10")
def test_an_unattributed_exception_is_refused():
    """A change to somebody's bill that nobody made."""
    with pytest.raises(ValueError, match="records WHO"):
        exception(recorded_by="  ")


@pytest.mark.guarantee("G10")
def test_a_naive_timestamp_is_refused():
    """It would be read as the server's local time, which is a property of the
    server and not of the garage."""
    with pytest.raises(ValueError, match="timezone"):
        exception(recorded_at=datetime(2026, 4, 1, 12, 0))


@pytest.mark.guarantee("G10")
@pytest.mark.parametrize("note", DANGEROUS_NOTES)
def test_a_note_changes_no_figure_whatsoever(note):
    """THE PLANT. Every figure the module produces, with and without the note."""
    without = exception(note="")
    with_note = exception(note=note)

    assert monetary_total_minor((without,)) == monetary_total_minor((with_note,))
    assert monetary_total_minor((with_note,)) == 1000
    assert applied_grace_days(5, (with_note,)) == applied_grace_days(5, (without,)) == 5
    assert is_blocked((with_note,)) is is_blocked((without,)) is False


@pytest.mark.guarantee("G10")
def test_the_amount_is_what_changes_money_and_it_is_typed():
    """The negative control for the plant above: without it, an implementation
    where NOTHING changed money would pass every case in this file."""
    assert monetary_total_minor((exception(amount_minor=2500),)) == 2500
    with pytest.raises(Exception):  # noqa: B017 - any refusal; the type is money.py's
        exception(amount_minor=25.0)


@pytest.mark.guarantee("G10")
def test_a_monetary_exception_without_an_amount_refuses_by_name():
    """Refused rather than defaulted to zero. A silent zero is the shape of an
    owner believing they credited somebody and nothing having happened."""
    with pytest.raises(Refused) as caught:
        exception(amount_minor=None)
    assert caught.value.code == REFUSAL_EXCEPTION_HAS_NO_AMOUNT


@pytest.mark.guarantee("G10")
def test_a_non_monetary_exception_carrying_an_amount_is_refused():
    """An amount somebody entered and nothing applied is worse than an error,
    because the owner believes it took effect."""
    with pytest.raises(ValueError, match="does not move"):
        exception(kind=ExceptionKind.BLOCK, amount_minor=1000)


@pytest.mark.guarantee("G10")
def test_the_latest_block_decision_wins_by_time_not_by_storage_order():
    """Reading them in storage order would let a database's idea of ordering
    decide whether somebody gets in."""
    block = exception(id="ex-b", kind=ExceptionKind.BLOCK, amount_minor=None, recorded_at=NOW)
    unblock = exception(
        id="ex-u",
        kind=ExceptionKind.UNBLOCK,
        amount_minor=None,
        recorded_at=NOW + timedelta(hours=1),
    )
    assert is_blocked((block, unblock)) is False
    assert is_blocked((unblock, block)) is False  # order of the tuple must not matter
    assert is_blocked((block,)) is True


@pytest.mark.guarantee("G10")
def test_two_grace_extensions_add():
    """An owner who twice decided to give somebody another week meant two weeks."""
    one = exception(id="e1", kind=ExceptionKind.EXTEND_GRACE, amount_minor=None,
                    extra_grace_days=7)
    two = exception(id="e2", kind=ExceptionKind.EXTEND_GRACE, amount_minor=None,
                    extra_grace_days=7)
    assert applied_grace_days(5, (one, two)) == 19


@pytest.mark.guarantee("G10")
def test_an_exception_attaches_to_exactly_one_thing():
    with pytest.raises(ValueError, match="exactly one"):
        exception(agreement_id=None, invoice_reference=None)
    with pytest.raises(ValueError, match="exactly one"):
        exception(agreement_id="ag-1", invoice_reference="inv-1")
