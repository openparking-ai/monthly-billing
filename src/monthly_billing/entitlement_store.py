"""The entitlement answer, read from the store — the call a lane actually makes.

**THE PURE CALL DOES NOT CHANGE.** ``entitlement.is_covered`` still takes
``has_unpaid_invoice_since`` as an argument; that is what the tests and the
contract pin, and it is what an integrator with no database calls. This module
is the store-backed caller of it: it finds the agreement, derives the argument
from the invoices, and hands the pure function everything it needs. The answer
that comes back is the pure function's ``Answer`` -- the same 7 fields, no money
-- and this module adds nothing to it.

**"UNPAID SINCE" IS THE EARLIEST DUE DATE AMONG THE PAYER'S UNPAID INVOICES AT
THIS GARAGE.** Not the latest: a payer two months behind has been unpaid since
the first of them, and grace counts from there. ``paid_at`` is derived by
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
from .store.records import StoredAgreement, load_agreements_at_garage, load_garage
from .store.writes import as_uuid


class GarageNotFound(LookupError):
    """The garage id names no row in the store."""


def covered_from_store(
    connection: Any,
    tenant_id: Any,
    garage_id: str,
    vehicle_identity: str,
    at: datetime,
    *,
    stay_entered_at: datetime | None = None,
) -> Answer:
    """Is this vehicle covered at this garage right now, per the store."""
    tenant_id = as_uuid(tenant_id)
    with tenant(connection, tenant_id) as cursor:
        stored = load_garage(cursor, garage_id)
        if stored is None:
            raise GarageNotFound(f"no garage with id {garage_id!r} in the store.")
        garage = stored.garage
        agreements = load_agreements_at_garage(cursor, stored.uuid)

        mine = [
            item
            for item in agreements
            if any(garage.identities_match(v, vehicle_identity) for v in item.agreement.vehicles)
        ]
        if not mine:
            connection.rollback()
            return is_covered(
                garage=garage, agreements=(), vehicle_identity=vehicle_identity, at=at,
                stay_entered_at=stay_entered_at,
            )

        chosen: StoredAgreement = max(mine, key=lambda i: (i.agreement.id, i.agreement.version))
        unpaid_since = _earliest_unpaid_due_at(cursor, chosen.payer_uuid, stored.uuid)
        exceptions = _exceptions_for(cursor, chosen.agreement.id, chosen.payer_uuid, stored.uuid)
    connection.rollback()

    grace = applied_grace_days(garage.payment_grace_days, exceptions)
    return is_covered(
        garage=replace(garage, payment_grace_days=grace),
        agreements=(chosen.agreement,),
        vehicle_identity=vehicle_identity,
        at=at,
        stay_entered_at=stay_entered_at,
        has_unpaid_invoice_since=unpaid_since,
        blocked_by_owner=is_blocked(exceptions),
    )


def _earliest_unpaid_due_at(cursor: Any, payer_uuid: Any, garage_uuid: Any) -> datetime | None:
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
    at this garage -- the two places a grace extension or a block can sit.

    **BY THE AGREEMENT'S IDENTITY, ACROSS EVERY VERSION.** An exception is stored
    against the version row the owner was looking at -- that is a fact worth
    keeping -- but it is READ by ``external_id``, because a price change is a new
    version row and an owner's block must not lift on the day the price moved.
    The engine's ``OwnerException.agreement_id`` is the external id for the same
    reason. A control plants the read back onto one version's row and requires
    red.
    """
    cursor.execute(
        """
        SELECT e.id, a.external_id, i.reference, e.kind, e.recorded_by, e.recorded_at,
               e.note, e.amount_minor, e.extra_grace_days
        FROM owner_exceptions e
        LEFT JOIN agreements a ON a.id = e.agreement_id
        LEFT JOIN invoices i ON i.id = e.invoice_id
        WHERE a.external_id = %s
           OR (i.payer_id = %s AND i.garage_id = %s AND i.paid_at IS NULL)
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
