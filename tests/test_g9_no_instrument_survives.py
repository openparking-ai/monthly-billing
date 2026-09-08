"""G9 -- nothing shaped like a card or a bank account reaches the store or a log.

**THIS FILE CONTAINS NO CARD NUMBER, AND THAT IS DELIBERATE.** The plants are
BUILT arithmetically from the checksum the detector uses, so the repository
carries a routine that can produce a card-shaped string and never the string
itself. A fixture holding a literal would put in the tests exactly the class of
value this module exists to keep out of the store -- and a test fixture is in the
repository just as permanently as a source file is.

**THE PLANTS GO IN THROUGH THE FIELDS A PERSON WOULD ACTUALLY PASTE INTO**, not
through the detector's front door: a processor's error string, an owner's note, a
mandate's terms. A guard tested only by calling it directly proves the guard
works and nothing about whether it is connected.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.payment import ChargeResult, Outcome, RetryState
from monthly_billing.sensitive import (
    InstrumentLike,
    find_instrument_like,
    luhn_ok,
    routing_checksum_ok,
)
from monthly_billing.store import guarded_insert, refuse_instrument_in_record


def build_card_shaped(prefix: str = "4", length: int = 16) -> str:
    """A card-SHAPED string, built to satisfy Luhn. Never a real number.

    Built rather than written down, for the reason in the module docstring.
    """
    body = prefix + "1" * (length - len(prefix) - 1)
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("no check digit satisfies Luhn, which is arithmetically impossible")


def build_routing_shaped() -> str:
    """Nine digits satisfying the ABA 3-7-1 checksum."""
    for candidate in range(100_000_000, 100_001_000):
        if routing_checksum_ok(str(candidate)):
            return str(candidate)
    raise AssertionError("no routing-shaped value found in the search range")


class RecordingCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters):
        self.statements.append((statement, parameters))


@pytest.mark.guarantee("G9")
def test_the_plant_builder_really_builds_something_the_detector_would_catch():
    """The control on the control. If this went blunt, every test below passes."""
    card = build_card_shaped()
    assert len(card) == 16 and luhn_ok(card)
    assert find_instrument_like(card) == "a payment card number"


@pytest.mark.guarantee("G9")
@pytest.mark.parametrize("separator", ["", " ", "-"])
def test_separators_do_not_hide_it(separator):
    """The same sixteen digits written in groups of four are the same number.

    **THE GROUPED RENDERING IS BUILT BELOW, NOT SHOWN HERE.** This docstring
    used to carry one to illustrate the point -- sixteen digits, Luhn-valid --
    which put in the tests exactly the class of value this module exists to keep
    out of the store, in the one file the repository-wide sweep could not see.
    A detector that only reads unbroken runs is one space away from blind, and
    grouped is the form these values are most often pasted in.
    """
    card = build_card_shaped()
    spaced = separator.join(card[i : i + 4] for i in range(0, 16, 4))
    assert find_instrument_like(spaced) == "a payment card number"


@pytest.mark.guarantee("G9")
@pytest.mark.parametrize(
    "innocent",
    [
        "invoice INV-2026-03-000412",
        "period 2026-03-01 to 2026-03-31",
        "12000 minor units, USD",
        "agreement ag-99887766554433221100",
        "garage 4111111111111112",  # 16 digits, and Luhn REJECTS it
    ],
)
def test_ordinary_identifiers_are_not_refused(innocent):
    """A guard that fires constantly is one somebody turns off.

    The last case is the load-bearing one: 16 digits that fail the checksum. It
    is what makes this a card detector rather than a long-number detector.
    """
    assert find_instrument_like(innocent) is None


@pytest.mark.guarantee("G9")
def test_a_routing_and_account_pair_is_refused():
    assert find_instrument_like(f"{build_routing_shaped()}/1234567890") == (
        "a bank routing number followed by an account number"
    )


@pytest.mark.guarantee("G9")
def test_the_refusal_never_echoes_what_it_found():
    """The message is written to a log. A guard that echoed the value would write
    the instrument into the log it exists to keep clean."""
    card = build_card_shaped()
    with pytest.raises(InstrumentLike) as caught:
        refuse_instrument_in_record("mandates", {"terms_shown": card})
    message = str(caught.value)
    assert card not in message
    assert card[:6] not in message
    assert "mandates.terms_shown" in message


@pytest.mark.guarantee("G9")
def test_a_processor_detail_carrying_one_is_refused_at_construction():
    """The field a real processor's error string lands in."""
    with pytest.raises(InstrumentLike):
        ChargeResult(outcome=Outcome.DECLINE, detail=f"declined for {build_card_shaped()}")


@pytest.mark.guarantee("G9")
def test_a_retry_detail_carrying_one_is_refused():
    with pytest.raises(InstrumentLike):
        RetryState("inv-1", attempts=1, last_detail=build_card_shaped())


@pytest.mark.guarantee("G9")
def test_an_owner_note_carrying_one_is_refused():
    """A note is free text an owner types, which makes it the likeliest route."""
    with pytest.raises(InstrumentLike):
        OwnerException(
            id="ex-1",
            agreement_id="ag-1",
            invoice_reference=None,
            kind=ExceptionKind.BLOCK,
            recorded_by="the owner",
            recorded_at=datetime.now(UTC),
            note=f"customer gave card {build_card_shaped()} over the phone",
        )


@pytest.mark.guarantee("G9")
def test_the_store_scans_every_column_from_the_record_itself():
    """Derived from the record's keys, so a column added next round is scanned.

    A guard walking a hard-coded list of columns cannot notice anything added to
    what it is supposed to cover.
    """
    cursor = RecordingCursor()
    guarded_insert(cursor, "mandates", {"agreed_by": "a person", "terms_shown": "monthly"})
    assert len(cursor.statements) == 1

    with pytest.raises(InstrumentLike):
        guarded_insert(
            cursor,
            "mandates",
            {
                "agreed_by": "a person",
                "terms_shown": "monthly",
                # A column nothing in this module knows about, which is the point.
                "a_column_added_next_round": build_card_shaped(),
            },
        )
    assert len(cursor.statements) == 1, "the row was written before it was scanned"


ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> tuple[Path, ...]:
    """Every file git tracks, DERIVED -- never a list written down here.

    A hand-listed set is the defect this sweep exists to close: it cannot notice
    a file that arrives tomorrow, which is exactly where the last specimen sat.

    A failure to read the file list is raised, never swallowed. A sweep that
    quietly scanned nothing would report the repository clean for the one reason
    that proves nothing at all.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return tuple(ROOT / name for name in listing.split("\0") if name)


@pytest.mark.guarantee("G9")
def test_the_sweep_is_pointed_at_a_real_file_set():
    """The control on the denominator. If this went empty, the sweep below passes
    over nothing and reports the repository clean."""
    files = tracked_files()
    assert len(files) > 40, f"the sweep found only {len(files)} tracked files"
    names = {path.name for path in files}
    assert "sensitive.py" in names, "the detector's own source is outside the sweep"
    assert Path(__file__).name in names, "this test file is outside the sweep"
    assert any(path.parent.name == "tests" for path in files), (
        "no file under tests/ is in the sweep, which is where the last specimen was"
    )


@pytest.mark.guarantee("G9")
def test_no_instrument_shaped_value_survives_anywhere_in_the_repository():
    """EVERY TRACKED FILE, AND THE FILE SET IS DERIVED FROM GIT.

    The guarantee is that the repository contains no card- or account-shaped
    value. It used to be measured over ONE path -- `sensitive.py` -- so a
    specimen anywhere else was invisible to the check that exists to find it,
    and one was: sixteen Luhn-valid digits in this module's own docstring, in
    the blind spot, found by planting rather than by the guard.

    **THERE IS NO EXEMPTION FOR `tests/`.** A detector that skips where the
    specimen lives is the self-exemption defect this project removed once
    already, wearing a new name. Fixtures are as permanent as source.
    """
    offenders = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # not text; nothing to read a number out of
        found = find_instrument_like(text)
        if found is not None:
            offenders.append(f"{path.relative_to(ROOT)}: {found}")

    assert offenders == [], (
        "a value shaped like a payment instrument is in the repository:\n  "
        + "\n  ".join(offenders)
        + "\n\nBuild it from the checksum instead of writing it down -- see "
        "build_card_shaped in this module. A guard that carries a specimen has "
        "put the specimen in the repository, which is where it was not supposed "
        "to be."
    )


@pytest.mark.guarantee("G9")
def test_the_guard_does_not_exempt_itself():
    """A detector that skips its own file is how two real values sat unread inside
    a scanner while every run reported clean. Asserted against the source."""
    source = ROOT / "src" / "monthly_billing" / "sensitive.py"
    text = source.read_text()
    assert "sensitive.py" not in text.replace('"""', ""), (
        "sensitive.py names itself, which is what a self-exemption looks like"
    )
    assert find_instrument_like(text) is None, (
        "the detector's own source contains an instrument-shaped value"
    )
