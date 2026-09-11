"""The store, behind a port — so the engine never depends on a database.

**WHY THERE IS A PORT AT ALL.** Everything that decides money in this module is
arithmetic on values: an agreement, a period, a set of exceptions. None of it
needs a database, and an integrator embedding the entitlement answer in a lane
should not have to install one. So the engine imports nothing from here, the
driver is an optional extra rather than a runtime dependency, and
``pip install monthly-billing`` still pulls in nothing at all.

**AND WHY THERE IS A REAL POSTGRES ONE ANYWAY.** The records this module keeps --
agreements, invoices, mandates, exceptions, retry state -- are multi-tenant data
that outlives a process, and row-level security from migration 0001 is a standing
constraint on every repository in this project. An in-memory store would satisfy
the tests and none of the constraint.

``postgres`` is imported lazily, inside the function that needs it, so that
importing this package on a machine with no driver is not an error.
"""

from __future__ import annotations

from .writes import (
    InstrumentRefusedAtTheStore,
    guarded_insert,
    refuse_instrument_in_record,
)

__all__ = [
    "InstrumentRefusedAtTheStore",
    "guarded_insert",
    "refuse_instrument_in_record",
]
