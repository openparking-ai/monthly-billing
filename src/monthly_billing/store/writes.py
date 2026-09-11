"""Every write to the store goes through here, and every write is scanned.

**THE CHOKEPOINT IS THE POINT.** The rule -- no card number, no bank account
number, no token that could substitute for one, in the database or the logs -- is
not enforceable field by field, because the next field somebody adds will not
have the check. It is enforceable at the one place every row passes through.

``guarded_insert`` builds the statement from the column names it was given, so a
column added next round is scanned the day it exists without anybody remembering
to add it here. That derivation is the whole design: a guard that walks a
hard-coded list of columns cannot notice anything added to what it is supposed to
cover.

**THE REFUSAL NAMES THE TABLE, THE COLUMN AND THE SHAPE, AND NEVER THE VALUE.**
This message is written to a log. A guard that echoed what it found would write
the instrument into the log it exists to keep clean, which is the detector
becoming the leak.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ..sensitive import InstrumentLike, refuse_instrument_like


class InstrumentRefusedAtTheStore(InstrumentLike):
    """An instrument-shaped value was refused on its way into the database."""


def as_uuid(value: Any) -> UUID:
    """The store's ids are ``UUID`` OBJECTS, never their text.

    A uuid rendered as text is a string of digits and dashes, and the instrument
    guard reads strings: one in a few dozen has a digit-and-dash stretch long
    enough to satisfy the card check and is refused as a card number. It is a
    false positive, but the guard cannot know that, and loosening the guard for
    "things that look like uuids" would be a hole shaped exactly like a card
    number with dashes in it. So the ids stay typed, the guard skips non-strings,
    and every public entry point converts what it was handed here.
    """
    return value if isinstance(value, UUID) else UUID(str(value))


class Cursor(Protocol):
    """Just enough of a DB-API cursor to write a row, so nothing here depends on
    a particular driver -- and so the guarantee's control can pass a recorder."""

    def execute(self, statement: str, parameters: tuple[Any, ...]) -> Any: ...


def refuse_instrument_in_record(table: str, record: dict[str, Any]) -> None:
    """Scan every column of a row about to be written.

    Keys as well as values: a column NAME is attacker-influenced wherever a
    record is assembled from input, and a name is written to the statement.
    """
    for column, value in record.items():
        refuse_instrument_like(column, f"{table}.<column name>")
        try:
            refuse_instrument_like(value, f"{table}.{column}")
        except InstrumentLike as exc:
            raise InstrumentRefusedAtTheStore(str(exc)) from None


def guarded_insert(cursor: Cursor, table: str, record: dict[str, Any]) -> Any:
    """Insert one row, refusing anything instrument-shaped first.

    The statement is built from ``record``'s own keys and the values go through
    parameters, never interpolation. The column names are checked before they
    reach the statement text, which is the only place in this module where a
    name is not a literal in the source.
    """
    refuse_instrument_in_record(table, record)

    if not record:
        raise ValueError(f"an insert into {table} with no columns writes nothing.")
    for identifier in (table, *record):
        if not identifier.replace("_", "").isalnum():
            raise ValueError(
                f"{identifier!r} is not a table or column name. These reach the "
                "statement TEXT rather than its parameters, so they are restricted to "
                "letters, digits and underscores rather than escaped -- an escape is a "
                "thing that can be got wrong, and this is the one place in the module "
                "where a name is not a literal in the source."
            )

    columns = ", ".join(record)
    placeholders = ", ".join(["%s"] * len(record))
    return cursor.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id",
        tuple(record.values()),
    )


def guarded_update(
    cursor: Cursor, table: str, record: dict[str, Any], where: dict[str, Any]
) -> Any:
    """Update the columns in ``record`` on the rows matching ``where``, scanned
    first exactly as an insert is.

    The only UPDATE this module makes is the derived ``invoices.paid_at`` -- the
    three append-only tables have no UPDATE granted at all -- but a second write
    path that skipped the scan would be a second path, so it goes through the
    same chokepoint and the same identifier rule.
    """
    refuse_instrument_in_record(table, {**record, **where})
    if not record or not where:
        raise ValueError(f"an update on {table} needs both columns to set and rows to match.")
    for identifier in (table, *record, *where):
        if not identifier.replace("_", "").isalnum():
            raise ValueError(
                f"{identifier!r} is not a table or column name. See guarded_insert for "
                "why these are restricted rather than escaped."
            )
    assignments = ", ".join(f"{column} = %s" for column in record)
    conditions = " AND ".join(f"{column} = %s" for column in where)
    return cursor.execute(
        f"UPDATE {table} SET {assignments} WHERE {conditions}",
        (*record.values(), *where.values()),
    )
