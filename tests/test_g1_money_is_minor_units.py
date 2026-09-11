"""G1 -- money is integer minor units, at EVERY LEAF, not at the fields we read.

The sibling module shipped this claim four times over a walk that returned early
on a container and fell off the end on a Decimal, so a bool and a Decimal were
ACCEPTED at the only leaves no other check typed. Every individual test it had
was true; the sentence over them was not.

So this test does not check a list of fields. **It walks the document and plants
at EVERY POSITION IN IT**, deriving the positions from the fixture rather than
from a list somebody typed -- which is the only shape that covers a field added
next round on the day it is added.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from fixtures import agreement_document
from monthly_billing.agreement import load_agreement
from monthly_billing.money import NotMinorUnits, as_minor, format_minor


def positions(node, path="agreement"):
    """Every leaf position in the document, derived from the document itself."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from positions(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from positions(value, f"{path}[{index}]")
    else:
        yield path


def set_at(document, path, value):
    """Write ``value`` at a dotted/indexed path produced by ``positions``."""
    parts = path.replace("]", "").split(".")[1:]
    target = document
    for part in parts[:-1]:
        if "[" in part:
            name, index = part.split("[")
            target = target[name][int(index)]
        else:
            target = target[part]
    last = parts[-1]
    if "[" in last:
        name, index = last.split("[")
        target[name][int(index)] = value
    else:
        target[last] = value


ALL_POSITIONS = sorted(positions(agreement_document()))


def test_the_probe_reaches_a_field_this_version_does_not_read():
    """The control on the control.

    A leaf walk that only reached the fields the loader consults would pass this
    whole module while proving the narrower claim. `additional_fees[0].label` is
    read; the assertion below is that the position list is not merely the set of
    money fields -- it includes strings, dates and nested list members.
    """
    assert "agreement.pauses[0].from_day" in ALL_POSITIONS
    assert "agreement.additional_fees[0].label" in ALL_POSITIONS
    assert "agreement.vehicles[0]" in ALL_POSITIONS
    assert len(ALL_POSITIONS) > 10


@pytest.mark.guarantee("G1")
@pytest.mark.parametrize("planted", [1.5, 12000.0, True, False, Decimal("120.00")])
def test_every_leaf_refuses_a_float_a_bool_or_a_decimal(planted):
    """One of the three types, at every position the document has.

    `12000.0` and `False` are in the list on purpose: a whole float and a falsy
    bool are the two values an implementation is most likely to let through, and
    both are exactly as wrong as 1.5 and True.
    """
    refused = []
    for path in ALL_POSITIONS:
        document = copy.deepcopy(agreement_document())
        set_at(document, path, planted)
        try:
            load_agreement(document)
        except NotMinorUnits:
            refused.append(path)
        except Exception:  # noqa: BLE001
            # Deliberately NOT counted. The money walk runs before any field is
            # parsed, so a correct implementation refuses EVERY position with
            # NotMinorUnits -- a position that fails some other way failed for
            # some other reason, and accepting that here is what let a plant
            # narrowing the walk to one field report green.
            pass
    assert refused == ALL_POSITIONS, (
        "these positions did not refuse with NotMinorUnits a value the module "
        "publishes as refused anywhere in an agreement: "
        f"{sorted(set(ALL_POSITIONS) - set(refused))}"
    )


@pytest.mark.guarantee("G1")
def test_the_money_walk_names_the_path_it_refused():
    """A refusal that does not say where to look is one the operator cannot act on."""
    document = agreement_document()
    document["additional_fees"][0]["amount_minor"] = 25.0
    with pytest.raises(NotMinorUnits) as caught:
        load_agreement(document)
    assert "additional_fees[0].amount_minor" in str(caught.value)


@pytest.mark.guarantee("G1")
def test_a_bool_does_not_pass_as_an_integer_amount():
    """`isinstance(True, int)` is True in Python, so this is not a formality."""
    with pytest.raises(NotMinorUnits):
        as_minor(True, "x")
    assert as_minor(0, "x") == 0  # and zero, which is falsy, is fine


@pytest.mark.guarantee("G1")
def test_the_currency_decides_the_rendering_not_the_number_100():
    """A zero-decimal currency has no minor unit to show, and a three-decimal
    one has three. Rendering 12000 as "120.00" in JPY is a correct number with an
    explanation a hundred times wrong."""
    assert format_minor(12000, "USD") == "120.00 USD"
    assert format_minor(12000, "JPY") == "12000 JPY"
    assert format_minor(12000, "KWD") == "12.000 KWD"
