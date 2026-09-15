"""The entitlement answer, read from the store — the call a lane actually makes.

**THE PURE CALL DOES NOT CHANGE.** ``entitlement.is_covered`` still takes
``has_unpaid_invoice_since`` as an argument; that is what the tests and the
contract pin, and it is what an integrator with no database calls. This module
is the store-backed caller of it: it finds the agreement, derives the argument
from the invoices, and hands the pure function everything it needs. The answer
that comes back is the pure function's ``Answer`` -- the same 7 fields, no money
-- and this module adds nothing to it.

**"UNPAID SINCE" IS THE EARLIEST DUE DATE AMONG THE PAYER'S UNPAID INVOICES AT
THE AGREEMENT'S HOME GARAGE -- NEVER THE ASKING ONE.** Not the latest: a payer
two months behind has been unpaid since the first of them, and grace counts from
there. And the home's, because the invoice LIVES at the garage that bills the
agreement: the moment an agreement covers a second garage, a read keyed on the
asking garage finds no invoice there and calls an unpaid monthly COVERED at
every door except the one that bills it. The exceptions read follows the home
for the same reason -- an owner's block recorded on the home's invoice must
reach every door -- and so does the grace period, which is a property of the
invoice and not of the barrier. Two agreements of one payer homed at two
garages stay INDEPENDENT: each is judged by the unpaid invoices at ITS home,
so an unpaid invoice on one does not make the other not-covered anywhere.
Aggregating across a payer's homes would create a blocking relationship that
does not exist today, which is a money decision and not this round's. ``paid_at`` is derived by
``payments.rederive_paid_at`` and never set by hand, so an unpaid invoice is
exactly one whose unreversed payments have not reached its total.

**THE OWNER'S GRACE EXTENSIONS ARE READ, OR THE LANE IGNORES THEM.** An
``extend_grace`` exception exists in M1 (``applied_grace_days``); an owner who
recorded one expects the barrier to honour it, and a store-backed call that
read the garage's base grace alone would quietly not. So the exceptions on the
agreement and on the payer's unpaid invoices are read, the extended grace is
computed, and the pure function is handed a garage carrying that figure. A
``block`` is read the same way. A control plants a call that ignores the
exceptions and requires red.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from .entitlement import Answer, is_covered
from .exceptions_by_owner import (
    ExceptionKind,
    OwnerException,
    applied_grace_days,
    is_blocked,
)
from .store.postgres import tenant
from .store.records import (
    GarageNotFound,
    StoredGarage,
    load_agreements_covering_garage,
    load_garage,
    registration_for,
)
from .store.writes import as_uuid


def covered_from_store(
    connection: Any,
    tenant_id: Any,
    garage_id: str,
    vehicle_identity: str,
    at: datetime,
    *,
    stay_entered_at: datetime | None = None,
) -> Answer:
    """Is this vehicle covered at this garage right now, per the store.

    **WHAT ``at`` DECIDES, AND WHAT IT DOES NOT -- a description of mechanism.**
    The AGREEMENT axes are answered at ``at``: not started, cancelled, paused,
    access hours. The PAYMENT state is read as of NOW -- ``paid_at`` as the
    store holds it when the call is made. Asking about a past instant after a
    later cheque or reversal therefore answers with today's payment state, not
    that day's. This is by design and not an oversight: a reversal reopens an
    invoice from its ORIGINAL due date because a cheque that bounced was never
    money (G19), so a historical payment answer is undefined here. The lane asks
    about the barrier's now; the command line, given a past ``--at``, says so.
    """
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        stored = load_garage(cursor, garage_id)
        if stored is None:
            raise GarageNotFound(f"no garage with id {garage_id!r} in the store.")
        garage = stored.garage
        # WHICH agreement a vehicle belongs to is the REGISTRATION, the garage-wide
        # fact 0003 keeps (one car, one agreement per garage) -- not a scan of every
        # agreement's vehicle list, which would still find the plate on a version
        # that has since released it or on a row written past the module.
        holder = registration_for(cursor, stored.uuid, garage.normalise_identity(vehicle_identity))
        # The ACCESS door: agreements whose latest version COVERS this garage,
        # billed here or not. The money doors read load_agreements_at_garage.
        mine = [
            item
            for item in load_agreements_covering_garage(cursor, stored.uuid)
            if item.agreement.id == holder
            and any(garage.identities_match(v, vehicle_identity) for v in item.agreement.vehicles)
        ]
        if not mine:
            connection.rollback()
            return is_covered(
                garage=garage, agreements=(), vehicle_identity=vehicle_identity, at=at,
                stay_entered_at=stay_entered_at,
            )
        (chosen,) = mine  # one identity, and the loader already gave its latest version
        # The invoice, the exceptions and the grace live at the agreement's HOME.
        home = _home_garage(cursor, stored, chosen.agreement.garage_id)
        unpaid_since = _earliest_unpaid_due_at(cursor, chosen.payer_uuid, home.uuid)
        exceptions = _exceptions_for(cursor, chosen.agreement.id, chosen.payer_uuid, home.uuid)
    connection.rollback()

    grace = applied_grace_days(home.garage.payment_grace_days, exceptions)
    return is_covered(
        garage=garage,
        agreements=(chosen.agreement,),
        vehicle_identity=vehicle_identity,
        at=at,
        stay_entered_at=stay_entered_at,
        has_unpaid_invoice_since=unpaid_since,
        blocked_by_owner=is_blocked(exceptions),
        home_garage=replace(home.garage, payment_grace_days=grace),
    )


def _home_garage(cursor: Any, asking: StoredGarage, home_id: str) -> StoredGarage:
    """The garage that bills the chosen agreement -- the asking garage itself
    when the agreement is homed here, which is every single-garage agreement."""
    if home_id == asking.garage.id:
        return asking
    home = load_garage(cursor, home_id)
    if home is None:
        # The agreement loaded, so its home row exists; this is a store the
        # module did not write. Refuse rather than answer with no grace.
        raise GarageNotFound(f"agreement home garage {home_id!r} is not in the store.")
    return home


def _earliest_unpaid_due_at(cursor: Any, payer_uuid: Any, garage_uuid: Any) -> datetime | None:
    """``garage_uuid`` is the agreement's HOME -- see the module docstring."""
    cursor.execute(
        "SELECT min(due_at) FROM invoices "
        "WHERE payer_id = %s AND garage_id = %s AND paid_at IS NULL",
        (payer_uuid, garage_uuid),
    )
    (earliest,) = cursor.fetchone()
    return earliest


def _exceptions_for(
    cursor: Any, agreement_external_id: str, payer_uuid: Any, garage_uuid: Any
) -> tuple[OwnerException, ...]:
    """The exceptions on the agreement, plus those on the payer's unpaid invoices
    at the agreement's HOME garage -- the two places a grace extension or a block
    can sit. ``garage_uuid`` is the home, never the asking garage.

    **BY THE AGREEMENT'S IDENTITY, ACROSS EVERY VERSION.** An exception is stored
    against the version row the owner was looking at -- that is a fact worth
    keeping -- but it is READ by ``external_id``, because a price change is a new
    version row and an owner's block must not lift on the day the price moved.
    The engine's ``OwnerException.agreement_id`` is the external id for the same
    reason. A control plants the read back onto one version's row and requires
    red.
    """
    # A block or an unblock on an invoice is read whatever the invoice's paid
    # state: a block the owner recorded is lifted by an unblock the owner
    # records, never by a cheque. A grace extension on an invoice is read only
    # while that invoice is unpaid -- it has no meaning on a paid one.
    cursor.execute(
        """
        SELECT e.id, a.external_id, i.reference, e.kind, e.recorded_by, e.recorded_at,
               e.note, e.amount_minor, e.extra_grace_days
        FROM owner_exceptions e
        LEFT JOIN agreements a ON a.id = e.agreement_id
        LEFT JOIN invoices i ON i.id = e.invoice_id
        WHERE a.external_id = %s
           OR (i.payer_id = %s AND i.garage_id = %s
               AND (i.paid_at IS NULL OR e.kind IN ('block', 'unblock')))
        ORDER BY e.recorded_at
        """,
        (agreement_external_id, payer_uuid, garage_uuid),
    )
    out = []
    for row in cursor.fetchall():
        eid, agreement_ref, invoice_ref, kind, by, at, note, amount, extra = row
        out.append(
            OwnerException(
                id=str(eid),
                agreement_id=agreement_ref,
                invoice_reference=invoice_ref,
                kind=ExceptionKind(kind),
                recorded_by=by,
                recorded_at=at,
                note=note,
                amount_minor=amount,
                extra_grace_days=extra,
            )
        )
    return tuple(out)
