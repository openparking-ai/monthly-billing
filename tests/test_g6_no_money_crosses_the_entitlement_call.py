"""G6 -- the entitlement answer carries no monetary field. Derived, not listed.

**THE FIELD SET COMES FROM THE CLASS.** A test that walked a hard-coded list of
field names could not notice anything added to what it is supposed to cover, so
this reads `Answer.__dataclass_fields__` and judges every name and every
annotation it finds. A monetary field added next round is caught the day it
exists, by a test nobody edited.

**AND THE SENTENCE IS JUDGED TOO.** A field called `reason` carrying "you owe
$40" would pass every field-name check ever written. So the rendered answer is
searched for a currency symbol and a decimal amount as well.
"""

from __future__ import annotations

import re

import pytest

from fixtures import month_end_garage, simple_agreement, sometime_on
from monthly_billing.entitlement import Answer, is_covered

#: Words that name money. Not a list of the fields that exist -- a list of the
#: VOCABULARY, applied to whatever fields do exist.
MONEY_WORDS = (
    "fee", "amount", "price", "balance", "owed", "owing", "due", "cost",
    "charge", "minor", "currency", "invoice", "total", "debt", "arrears",
)

#: A currency symbol, or a decimal amount, in rendered prose.
MONEY_IN_PROSE = re.compile(r"[$£€¥]|\b\d+\.\d{2}\b")


def field_names():
    return tuple(Answer.__dataclass_fields__)


@pytest.mark.guarantee("G6")
def test_the_probe_is_pointed_at_a_real_field_set():
    """The control on the derivation: if this went empty, everything below passes."""
    names = field_names()
    assert "covered" in names and "entitlement" in names
    assert len(names) >= 6


@pytest.mark.guarantee("G6")
def test_no_field_on_the_answer_names_money():
    names = field_names()
    offending = [
        name for name in names if any(word in name.lower() for word in MONEY_WORDS)
    ]
    assert offending == [], (
        f"these fields on the entitlement answer name money: {offending}. This call "
        "is an access fact -- a parking lane asks whether a vehicle is covered and "
        "gets covered or not, and nothing about what anybody owes."
    )


@pytest.mark.guarantee("G6")
def test_no_annotation_on_the_answer_is_a_money_type():
    """A field called `extra` typed `Money` would pass the name check."""
    for name, spec in Answer.__dataclass_fields__.items():
        annotation = str(spec.type).lower()
        assert not any(word in annotation for word in ("money", "minor", "decimal")), (
            f"{name} is annotated {spec.type!r}, which is a money type."
        )


@pytest.mark.guarantee("G6")
@pytest.mark.parametrize("covered_case", [True, False])
def test_no_rendered_answer_contains_an_amount(covered_case):
    """Both branches, because only one of them was ever going to be checked."""
    from datetime import date

    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 1))
    answer = is_covered(
        garage=garage,
        agreements=(agreement,),
        vehicle_identity="CAR000" if covered_case else "NOT-ON-ANY-AGREEMENT",
        at=sometime_on(date(2026, 4, 1), garage.timezone),
    )
    assert answer.covered is covered_case
    assert not MONEY_IN_PROSE.search(answer.reason), (
        f"the answer's own sentence carries an amount: {answer.reason!r}"
    )


@pytest.mark.guarantee("G6")
def test_a_not_covered_answer_does_not_report_an_entitlement():
    """Two answers where one was asked for. "You may park ten cars, and no.\""""
    from datetime import date

    garage = month_end_garage()
    answer = is_covered(
        garage=garage,
        agreements=(simple_agreement(start_day=date(2026, 3, 1)),),
        vehicle_identity="NOT-ON-ANY-AGREEMENT",
        at=sometime_on(date(2026, 4, 1), garage.timezone),
    )
    assert answer.covered is False
    assert answer.entitlement is None
