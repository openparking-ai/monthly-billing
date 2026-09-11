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

from ..sensitive import InstrumentLike, refuse_instrument_like


class InstrumentRefusedAtTheStore(InstrumentLike):
    """An instrument-shaped value was refused on its way into the database."""


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
