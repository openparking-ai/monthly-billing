"""Money is a Python ``int`` of minor units. Nothing else is money here.

A float, a bool or a ``Decimal`` is refused at ANY depth of an agreement -- by
``refuse_non_integer_money``, whether or not the field is one this version
reads. A string is refused wherever money is expected, by ``as_minor``; strings
are of course ordinary elsewhere in an agreement. A price that cannot be
expressed in minor units is a price this module refuses, and it refuses it at
load rather than at the point the arithmetic goes wrong.

Three traps this file exists to close, all of them things that pass a naive
``isinstance(value, int)``:

* **``bool`` is a subclass of ``int`` in Python.** ``isinstance(True, int)`` is
  ``True``, so ``True`` sails through an integer check and then charges an
  account one minor unit a month. It is rejected explicitly, first, before the
  int check.
* **JSON exponent form parses to a float.** ``json.loads("1e4")`` is ``10000.0``,
  not ``10000`` -- an operator writing ``1e4`` for a hundred-dollar monthly rate
  produces a float that happens to be integral. ``float`` is rejected whatever
  its value, so ``10000.0`` is refused exactly as ``10000.5`` is. There is no
  "but it is a whole number" branch, because that branch is how floats get in.
* **``Decimal`` looks like the safe choice and is not, here.** It is refused too.
  Not because it is inaccurate -- it is not -- but because two money types in
  one codebase means every function has to handle both, and the one that
  eventually forgets is the one that ships. One type, all the way down.

**AND THE WALK GOES OVER VALUES, NOT OVER KEYS.** The sibling module in this
project shipped this function walking a document's structure in a way that
returned early on a container and fell off the end on a ``Decimal``, so a bool
and a Decimal were ACCEPTED at the four leaves no other check typed -- while four
published sentences said all three were refused anywhere. Every individual test
was true; the sentence over them was not. The walk below is ordered so that the
three refused types are tested at every node BEFORE any container recursion and
BEFORE any fall-through, and ``test_g1_money_is_minor_units.py`` probes EVERY
POSITION in a real agreement document rather than the positions this version
happens to read.

**Nothing in this module rounds money.** Proration divides, and division is where
a remainder appears; ``prorate`` states what it does with one and is the only
place in the module that produces a fraction of anything. See ``cycle.py``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .currency import minor_unit_digits


class NotMinorUnits(TypeError):
    """A value that was supposed to be money is not an integer of minor units."""


def as_minor(value: Any, label: str) -> int:
    """Return ``value`` as minor units, or refuse and say which field was wrong.

    The label is the document's own path to the field
    (``agreement.additional_fees[2].amount_minor``), because a refusal that does
    not say where to look is a refusal the operator cannot act on.
    """
    if isinstance(value, bool):
        raise NotMinorUnits(
            f"{label} is a boolean ({value!r}). Money is an integer of minor units; "
            "`bool` is an `int` subclass in Python and would otherwise charge this "
            "at 0 or 1 without complaint."
        )
    if isinstance(value, float):
        raise NotMinorUnits(
            f"{label} is a float ({value!r}). Money is an integer of minor units -- "
            "12000 means 120.00, not 120.0. Note that JSON exponent form (1e4) parses "
            "as a float even though it looks whole."
        )
    if not isinstance(value, int):
        raise NotMinorUnits(
            f"{label} is {type(value).__name__} ({value!r}). Money is an integer of "
            "minor units. Decimal and str are refused deliberately: one money type, "
            "all the way down."
        )
    return value


def as_non_negative_minor(value: Any, label: str) -> int:
    minor = as_minor(value, label)
    if minor < 0:
        raise NotMinorUnits(f"{label} is negative ({minor}). A price is not negative.")
    return minor


def refuse_non_integer_money(node: Any, path: str = "agreement") -> None:
    """Walk a loaded document and refuse a float, a bool or a Decimal ANYWHERE.

    ``as_minor`` guards the fields this module reads. This guards the fields it
    does not -- a float sitting in a field the current version ignores is a float
    that starts being read the round somebody adds the feature that reads it, and
    by then it is in an agreement an operator believes is live.

    The check is on the TYPE, not on the value: ``3.0`` is refused. An agreement
    is data somebody wrote, and the moment this makes an exception for a float
    that happens to be whole, every float becomes one bug away from whole.

    **What the fix actually was, stated precisely, because the first version of
    this docstring overstated it.** It claimed the ORDER of the branches below is
    load-bearing. It is not: a container is never a bool, so moving the container
    branches above the type checks changes no answer, and a control planted to
    prove otherwise reported GREEN -- correctly.

    The load-bearing property is COVERAGE, not order: this function is called on
    the WHOLE document before any field is parsed, so it reaches the leaves the
    module does not read. That is what the control plants against, and that is
    the half a sibling module shipped broken.
    """
    if isinstance(node, bool):
        raise NotMinorUnits(
            f"{path} is a boolean ({node!r}). No bool appears anywhere in an agreement, "
            "at any depth. `bool` is an `int` subclass in Python, so one sitting in a "
            "field this version does not read is an integer waiting for the round that "
            "starts reading it."
        )
    if isinstance(node, float):
        raise NotMinorUnits(
            f"{path} is a float ({node!r}). No float appears anywhere in an agreement, "
            "at any depth -- not in a field this version reads, and not in one it "
            "ignores."
        )
    if isinstance(node, Decimal):
        raise NotMinorUnits(
            f"{path} is a Decimal ({node!r}). Accurate, and still refused: two money "
            "types in one codebase means every function has to handle both, and the "
            "one that eventually forgets is the one that ships. `as_minor` catches a "
            "Decimal in a field that asks for money and names the field; this catches "
            "one anywhere else and names the path."
        )
    if isinstance(node, dict):
        for key, value in node.items():
            refuse_non_integer_money(value, f"{path}.{key}")
        return
    if isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            refuse_non_integer_money(value, f"{path}[{index}]")
        return
    # Anything else -- str, int, None -- is what a document is made of. This is a
    # DENY list of the three types the published sentence names, not an allow-list
    # of the types JSON can produce: an allow-list would be a larger claim than
    # the contract makes.


def format_minor(minor: int, currency: str) -> str:
    """For an invoice line's text. Never feed the result back into arithmetic.

    **The divisor comes from the CURRENCY, not from the number 100.** A charge of
    12000 minor units in a zero-decimal currency is 12000 of them, and rendering
    it "120.00" would be a correct number with an explanation a hundred times
    wrong -- on the invoice this module exists to make trustworthy. A
    zero-decimal currency renders with no decimal point at all; a three-decimal
    one renders three places.

    Currencies are validated at load, so an unknown code cannot reach here from
    an agreement. If one does, `minor_unit_digits` raises rather than falling
    back to 2 -- a guessed exponent is the defect, not the mitigation.
    """
    digits = minor_unit_digits(currency)
    sign = "-" if minor < 0 else ""
    if digits == 0:
        return f"{sign}{abs(minor)} {currency}"
    divisor = 10**digits
    whole, part = divmod(abs(minor), divisor)
    return f"{sign}{whole}.{part:0{digits}d} {currency}"
