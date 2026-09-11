"""Refuse anything shaped like a payment instrument, anywhere it could be stored.

**THE RULE.** No card number, no bank account number, and no token that could
substitute for one, in this module's database or its logs. Ever.

**THIS IS A GUARANTEE WITH A CONTROL, NOT A SENTENCE IN A DOCUMENT.** A
card-shaped value planted into the payment path makes this go red. An assertion
that a thing does not happen is not this rule; a check that fires is.

---------------------------------------------------------------------------
THREE THINGS THIS DETECTOR DELIBERATELY DOES NOT DO
---------------------------------------------------------------------------

**IT STORES NO EXAMPLE OF WHAT IT LOOKS FOR.** There is no card number in this
file, not even an invalid one, and not in the tests either -- the test builds its
plant arithmetically from a checksum, so the repository contains a routine that
can produce a card-shaped string and never the string itself. A guard that
carries a specimen has put the specimen in the repository, which is where it was
not supposed to be.

**IT HAS NO SELF-EXEMPTION.** This module scans its own values like any other. A
detector that skips itself is how two real values sat unread inside a scanner
while every run reported clean -- that happened in a sibling repository in this
project and the exemption was removed for exactly this reason.

**IT REFUSES ON SHAPE, AND THE SHAPE IS NARROW ON PURPOSE.** A guard that
rejected every long digit run would fire on an invoice reference, a period, an
amount in minor units and half the identifiers this module uses -- and a guard
that fires constantly is one somebody turns off. So:

* A card is 13 to 19 digits AND passes the Luhn checksum. The checksum is what
  makes this specific: an arbitrary 16-digit number passes Luhn about one time in
  ten, and requiring it takes the false-positive rate on ordinary identifiers
  down with it.
* A bank account is recognised by the ROUTING-plus-ACCOUNT shape rather than by
  length alone: a 9-digit routing number has its own checksum, and this refuses a
  value carrying one next to a 4-to-17-digit account run.

**AND SEPARATORS ARE STRIPPED BEFORE THE CHECK.** The same digits written in
groups of four, separated by spaces or by hyphens, are the same number. A
detector that only reads unbroken runs is one space away from blind, and grouped
is the form these values are most often pasted in.

---------------------------------------------------------------------------
AND THIS DOCSTRING IS WHERE THE FIRST ONE GOT IN
---------------------------------------------------------------------------
The paragraph above originally ILLUSTRATED the grouping with a specimen -- two
renderings of a sixteen-digit, Luhn-valid value -- which is exactly what the
first rule in this file says the repository does not contain. Nobody noticed
while writing it; the guarantee's own control found it, by running this detector
over this file, on the first run.

It is recorded rather than quietly deleted because it is the strongest evidence
the design is right on two points at once. The self-exemption had to be absent
for the check to look here at all, and the specimen had to be built rather than
written for the tests to be clean while the source was not. Either concession --
skipping our own file, or keeping "just one example" -- and this would have
shipped.

AND THEN A SECOND ONE GOT IN, IN THE PLACE THE CHECK COULD NOT LOOK
---------------------------------------------------------------------------
The check above ran over ONE path: this file. A review planted a card-shaped
value into a different tracked file and watched the whole suite, both repository
scanners and the anchor pre-flight report green -- and then found that a
sixteen-digit Luhn-valid specimen had been sitting in the G9 test module's own
docstring the entire time, illustrating the grouping rule, in the one directory
the check was structurally unable to see.

So the guarantee is no longer measured over a path. It is measured over EVERY
FILE GIT TRACKS, with the file set derived from `git ls-files` and no exemption
for `tests/` -- because "the tests are different" is the self-exemption argument
again, one directory along, and a fixture is in the repository just as
permanently as a source file is.

The lesson is not "we missed one". It is that an absence claim is only ever as
wide as the set it was measured over, and this one published a repository-wide
sentence while reading a single file.
"""

from __future__ import annotations

import re


class InstrumentLike(ValueError):
    """A value shaped like a payment instrument reached a field that stores it."""


#: A run of digits, possibly broken by single spaces or hyphens. Bounded at 19
#: significant digits by the caller rather than here, so the pattern stays one
#: thing and the length rules live where they are explained.
_DIGIT_RUN = re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,22}[0-9](?![0-9])")

_ROUTING_THEN_ACCOUNT = re.compile(
    r"(?<![0-9])([0-9]{9})[ \-:/]{1,3}([0-9]{4,17})(?![0-9])"
)


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def luhn_ok(digits: str) -> bool:
    """The card checksum. Public because the CONTROL needs it to build a plant.

    Exposing it is what lets the test construct a card-shaped value arithmetically
    instead of the repository carrying one. The alternative -- a literal in a
    fixture -- would put in the tests exactly the class of value this module
    exists to keep out of the store.
    """
    if not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def routing_checksum_ok(digits: str) -> bool:
    """The ABA routing checksum: 3-7-1 weights, mod 10.

    Same reason as `luhn_ok` for being public and for existing at all -- it is
    what makes "nine digits" mean "a routing number" rather than "any nine
    digits", and nine-digit identifiers are ordinary.
    """
    if len(digits) != 9 or not digits.isdigit():
        return False
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    # strict=True: nine digits against nine weights. A length mismatch here
    # would silently truncate the checksum, which is the shape of a guard that
    # stops guarding without stopping running.
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 10 == 0


def find_instrument_like(value: object) -> str | None:
    """Say WHICH shape was found, or None. Never returns the value itself.

    The caller puts this in a refusal message and that message goes to a log, so
    returning the offending text would write the instrument into the log this
    module is protecting -- the detector would become the leak.
    """
    if not isinstance(value, str) or not value:
        return None

    for match in _ROUTING_THEN_ACCOUNT.finditer(value):
        if routing_checksum_ok(match.group(1)):
            return "a bank routing number followed by an account number"

    for match in _DIGIT_RUN.finditer(value):
        digits = _digits(match.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return "a payment card number"

    return None


def refuse_instrument_like(value: object, label: str) -> None:
    """Refuse, naming the FIELD and the SHAPE and never the value.

    Called from every field in this module that a person could paste one into,
    and from the store on the way in. The refusal is loud and immediate: a
    quietly redacted value is a value that reached the code that redacted it, and
    the next field to be added will not have the redaction.
    """
    found = find_instrument_like(value)
    if found is not None:
        raise InstrumentLike(
            f"{label} contains {found}. This module never stores a card number, a "
            "bank account number, or any token that could substitute for one -- not "
            "in its database and not in its logs. The value is not echoed here, "
            "because this message is itself written to a log."
        )


def walk_and_refuse(node: object, path: str = "record") -> None:
    """Every string leaf of a record, before it is written.

    The walk goes over VALUES at every depth, not over the fields this version
    knows about. A field added next round is covered the day it exists, which is
    the same reason the money walk is shaped this way -- and the same defect it
    was fixed for.
    """
    if isinstance(node, str):
        refuse_instrument_like(node, path)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            refuse_instrument_like(key, f"{path}.<key>")
            walk_and_refuse(value, f"{path}.{key}")
        return
    if isinstance(node, (list, tuple, set, frozenset)):
        for index, value in enumerate(node):
            walk_and_refuse(value, f"{path}[{index}]")
        return
    if hasattr(node, "__dataclass_fields__"):
        for name in node.__dataclass_fields__:  # type: ignore[attr-defined]
            walk_and_refuse(getattr(node, name), f"{path}.{name}")
