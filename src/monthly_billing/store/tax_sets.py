"""A garage's tax sets in the store: stated through one door, read back whole.

**JUDGE, STORE, PROVE, READ** -- the shape platform's ``taxes.js`` settled on, for
this module's own copy of a garage's money-deciding facts:

* **judge** is the ENGINE's, and only the engine's. The document goes to
  ``load_tax_sets`` (through ``tax.load_sets``) before any field of it is read
  here, and what it refuses is refused in its words. No second validator.
* **store** what the engine accepted, as it READ it -- the instant it parsed,
  the rules in the garage's stated order -- one row per set, append-only by
  grant (migration 0006). Through ``guarded_insert``, like every write.
* **prove**: before the caller commits, the garage's WHOLE list is read back in
  the form a load gets it and handed to the engine again. A stored set that
  would not load is not stored: the engine loads a garage's sets whole, so one
  unloadable set would make every invoice after it unpriceable.
* **read**: every set the garage holds, oldest first. No selection here -- the
  engine chooses the set in force.

A set already stated at the same instant is refused by name: sets are never
edited, and a later one is how anything changes. The table's UNIQUE is what sees
two statements racing; each would load alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..findings import REFUSAL_TAX_SETS_INVALID, Refused
from ..tax import echoed, load_sets
from .writes import as_uuid, guarded_insert

#: The constraint whose violation MEANS "a set already takes effect then".
ONE_SET_PER_INSTANT = "garage_tax_sets_one_set_per_instant"


def load_tax_set_documents(cursor: Any, garage_uuid: Any) -> list[dict[str, Any]]:
    """The garage's whole list, as documents the engine loads, oldest first.

    Read in the caller's transaction. The billing run calls this INSIDE each
    payer's transaction, so the sets that priced a payer are the sets that
    payer's transaction saw -- a set stated mid-run cannot price two payers of
    one run differently without the count on their rows saying so.
    """
    cursor.execute(
        "SELECT effective_from, rules FROM garage_tax_sets "
        "WHERE garage_id = %s ORDER BY effective_from",
        (as_uuid(garage_uuid),),
    )
    return [
        {"effective_from": _instant(effective_from), "rules": rules}
        for effective_from, rules in cursor.fetchall()
    ]


def _instant(value: datetime) -> str:
    """An instant as the engine parses it: ISO 8601 with its offset, to the
    microsecond the column holds."""
    return value.isoformat()


def state_tax_sets(
    cursor: Any,
    tenant_id: Any,
    garage_uuid: Any,
    document: object,
    *,
    stated_by: str,
) -> list[dict[str, Any]]:
    """Store the sets ``document`` states for the garage, and return the
    garage's whole list as it now stands. In the caller's transaction; the
    caller commits, or rolls back on anything raised."""
    import psycopg  # the store extra
    from psycopg.types.json import Jsonb

    tenant_id, garage_uuid = as_uuid(tenant_id), as_uuid(garage_uuid)
    stated = echoed(load_sets(document))
    for one in stated:
        try:
            guarded_insert(
                cursor,
                "garage_tax_sets",
                {
                    "tenant_id": tenant_id,
                    "garage_id": garage_uuid,
                    "effective_from": datetime.fromisoformat(one["effective_from"]),
                    "rules": Jsonb(one["rules"]),
                    "stated_by": stated_by,
                },
            )
        except psycopg.errors.UniqueViolation as violation:
            if violation.diag.constraint_name != ONE_SET_PER_INSTANT:
                raise
            raise Refused(
                REFUSAL_TAX_SETS_INVALID,
                f"the garage already has a set taking effect at {one['effective_from']}. "
                "A set is never edited: a change is a new set at a later instant.",
            ) from None
        except psycopg.errors.UntranslatableCharacter:
            raise ValueError(
                f"a rule in the set taking effect at {one['effective_from']} holds a "
                "character the store cannot keep (a NUL byte). That is a limit of where "
                "the set is kept, not a judgement of the set."
            ) from None
        cursor.fetchone()

    whole = load_tax_set_documents(cursor, garage_uuid)
    load_sets(whole, where="the garage's stored tax_sets")  # the write-back proof
    kept = {d["effective_from"]: d["rules"] for d in whole}
    for one in stated:
        at = datetime.fromisoformat(one["effective_from"])
        back = next((r for e, r in kept.items() if datetime.fromisoformat(e) == at), None)
        if back != one["rules"]:
            raise ValueError(
                f"the set taking effect at {one['effective_from']} read back differently "
                "from how it was stated, so it is not stored."
            )
    return whole
