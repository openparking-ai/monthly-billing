"""Putting the documents into the store, and reading them back out as the engine's
own types.

**THE ENGINE NEVER SEES A ROW.** ``billing_run``, ``payments`` and the
store-backed entitlement call all work on the same ``Garage`` and ``Agreement``
values the pure functions take, loaded from rows by this module. Two
representations of an agreement would mean two sets of validation, and the one
that skipped a check would be the one that priced somebody wrong -- so the rows
are read back through ``Agreement``'s own constructor, which refuses exactly what
the document loader refuses.

**EVERY WRITE GOES THROUGH ``guarded_insert``.** Nothing here builds an INSERT
of its own; the chokepoint scans every column of every row, and a column added
here next round is scanned the day it exists.

**THE STORE'S IDS AND THE ENGINE'S IDS ARE DIFFERENT THINGS.** The engine
compares opaque strings -- a garage id, a payer id, an agreement id -- and never
parses them. The store keys rows by uuid and carries those strings as
``external_id``. ``Stored*`` pairs the two, so a caller that needs to write a row
about an engine value has the uuid beside it and never looks one up by string
twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from uuid import UUID

from ..agreement import (
    AccessHours,
    AdditionalFee,
    Agreement,
    FeeCadence,
    Mandate,
    Pause,
    Status,
)
from ..garage import BillingDay, Garage, IdentityRule
from .writes import as_uuid, guarded_insert


def _insert(cursor: Any, table: str, record: dict[str, Any]) -> UUID:
    """One row, through the chokepoint, returning the uuid the database chose.

    A ``UUID`` object, never its text -- see ``as_uuid`` for why.
    """
    guarded_insert(cursor, table, record)
    (new_id,) = cursor.fetchone()
    return as_uuid(new_id)


# ---------------------------------------------------------------------------
# Writing the documents in
# ---------------------------------------------------------------------------


def store_garage(cursor: Any, tenant_id: Any, garage: Garage) -> UUID:
    return _insert(
        cursor,
        "garages",
        {
            "tenant_id": as_uuid(tenant_id),
            "external_id": garage.id,
            "timezone": garage.timezone,
            "currency": garage.currency,
            "billing_day": garage.billing_day.value,
            "billing_day_of_month": garage.billing_day_of_month,
            "payment_grace_days": garage.payment_grace_days,
            "identity_rule": garage.identity_rule.value,
        },
    )


def store_payer(cursor: Any, tenant_id: Any, external_id: str, name: str) -> UUID:
    return _insert(
        cursor,
        "payers",
        {"tenant_id": as_uuid(tenant_id), "external_id": external_id, "name": name},
    )


def store_agreement(
    cursor: Any,
    tenant_id: Any,
    garage: Garage,
    garage_uuid: Any,
    payer_uuid: Any,
    agreement: Agreement,
) -> UUID:
    """One agreement VERSION, with its vehicles, pauses, fees and mandate.

    A version is never edited -- see migration 0001 -- so a price change is a
    second call with the next version number, and the old row stays to be
    compared against.
    """
    tenant_id = as_uuid(tenant_id)
    garage_uuid, payer_uuid = as_uuid(garage_uuid), as_uuid(payer_uuid)
    agreement_uuid = _insert(
        cursor,
        "agreements",
        {
            "tenant_id": tenant_id,
            "external_id": agreement.id,
            "version": agreement.version,
            "garage_id": garage_uuid,
            "payer_id": payer_uuid,
            "spots": agreement.spots,
            "monthly_price_minor": agreement.monthly_price_minor,
            "start_day": agreement.start_day,
            "status": agreement.status.value,
            "cancelled_effective_day": agreement.cancelled_effective_day,
            "access_entry_from": (
                agreement.access_hours.entry_from if agreement.access_hours else None
            ),
            "access_exit_by": agreement.access_hours.exit_by if agreement.access_hours else None,
        },
    )
    for identity in agreement.vehicles:
        _insert(
            cursor,
            "agreement_vehicles",
            {
                "tenant_id": tenant_id,
                "agreement_id": agreement_uuid,
                "identity": identity,
                "identity_normalised": garage.normalise_identity(identity),
            },
        )
    for pause in agreement.pauses:
        _insert(
            cursor,
            "agreement_pauses",
            {
                "tenant_id": tenant_id,
                "agreement_id": agreement_uuid,
                "from_day": pause.from_day,
                "until_day": pause.until_day,
            },
        )
    for fee in agreement.additional_fees:
        _insert(
            cursor,
            "agreement_fees",
            {
                "tenant_id": tenant_id,
                "agreement_id": agreement_uuid,
                "label": fee.label,
                "amount_minor": fee.amount_minor,
                "cadence": fee.cadence.value,
                "effective_from": fee.effective_from,
            },
        )
    if agreement.mandate is not None:
        mandate = agreement.mandate
        _insert(
            cursor,
            "mandates",
            {
                "tenant_id": tenant_id,
                "agreement_id": agreement_uuid,
                "agreed_by": mandate.agreed_by,
                "agreed_at": datetime.fromisoformat(mandate.agreed_at_iso),
                "terms_shown": mandate.terms_shown,
                "frequency_shown": mandate.frequency_shown,
                "amount_basis_shown": mandate.amount_basis_shown,
                "cancellation_shown": mandate.cancellation_shown,
            },
        )
    return agreement_uuid


# ---------------------------------------------------------------------------
# Reading them back out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredGarage:
    uuid: UUID
    garage: Garage


@dataclass(frozen=True)
class StoredAgreement:
    uuid: UUID
    payer_uuid: UUID
    agreement: Agreement


def load_garage(cursor: Any, external_id: str) -> StoredGarage | None:
    cursor.execute(
        "SELECT id, external_id, timezone, currency, billing_day, billing_day_of_month, "
        "payment_grace_days, identity_rule FROM garages WHERE external_id = %s",
        (external_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    uuid, ext, tz, currency, billing_day, nth, grace, rule = row
    return StoredGarage(
        uuid=as_uuid(uuid),
        garage=Garage(
            id=ext,
            timezone=tz,
            currency=currency,
            billing_day=BillingDay(billing_day),
            billing_day_of_month=nth,
            payment_grace_days=grace,
            identity_rule=IdentityRule(rule),
        ),
    )


def load_payers_at_garage(cursor: Any, garage_uuid: Any) -> tuple[tuple[UUID, str], ...]:
    """(payer uuid, payer id) for every payer with an agreement at the garage."""
    cursor.execute(
        "SELECT DISTINCT p.id, p.external_id FROM agreements a JOIN payers p ON p.id = a.payer_id "
        "WHERE a.garage_id = %s ORDER BY p.external_id",
        (as_uuid(garage_uuid),),
    )
    return tuple((as_uuid(uuid), external) for uuid, external in cursor.fetchall())


def load_agreements_at_garage(
    cursor: Any, garage_uuid: Any, payer_uuid: Any = None
) -> tuple[StoredAgreement, ...]:
    """Every agreement at a garage -- or one payer's -- at its LATEST version, as
    engine values.

    The latest version per external id is what the run prices and what the lane
    reads -- the same rule ``is_covered`` applies when handed several versions.
    A row the engine's constructor refuses raises here, so a caller loading one
    payer at a time can refuse that payer and go on to the next.
    """
    cursor.execute(
        """
        SELECT DISTINCT ON (a.external_id)
               a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,
               a.monthly_price_minor, a.start_day, a.status, a.cancelled_effective_day,
               a.access_entry_from, a.access_exit_by, g.external_id
        FROM agreements a
        JOIN payers p ON p.id = a.payer_id
        JOIN garages g ON g.id = a.garage_id
        WHERE a.garage_id = %s AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)
        ORDER BY a.external_id, a.version DESC
        """,
        (as_uuid(garage_uuid), None if payer_uuid is None else as_uuid(payer_uuid),
         None if payer_uuid is None else as_uuid(payer_uuid)),
    )
    heads = cursor.fetchall()
    out: list[StoredAgreement] = []
    for row in heads:
        (
            uuid, external_id, version, payer_uuid, payer_external, spots, price,
            start_day, status, cancelled_day, entry_from, exit_by, garage_external,
        ) = row
        out.append(
            StoredAgreement(
                uuid=as_uuid(uuid),
                payer_uuid=as_uuid(payer_uuid),
                agreement=Agreement(
                    id=external_id,
                    version=version,
                    garage_id=garage_external,
                    payer_id=payer_external,
                    spots=spots,
                    vehicles=_vehicles(cursor, uuid),
                    monthly_price_minor=price,
                    start_day=start_day,
                    mandate=_mandate(cursor, uuid),
                    status=Status(status),
                    cancelled_effective_day=cancelled_day,
                    access_hours=(
                        AccessHours(entry_from=_as_time(entry_from), exit_by=_as_time(exit_by))
                        if entry_from is not None
                        else None
                    ),
                    pauses=_pauses(cursor, uuid),
                    additional_fees=_fees(cursor, uuid),
                ),
            )
        )
    return tuple(out)


def _as_time(value: Any) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def _vehicles(cursor: Any, agreement_uuid: Any) -> tuple[str, ...]:
    cursor.execute(
        "SELECT identity FROM agreement_vehicles WHERE agreement_id = %s ORDER BY identity",
        (agreement_uuid,),
    )
    return tuple(row[0] for row in cursor.fetchall())


def _pauses(cursor: Any, agreement_uuid: Any) -> tuple[Pause, ...]:
    cursor.execute(
        "SELECT from_day, until_day FROM agreement_pauses WHERE agreement_id = %s "
        "ORDER BY from_day",
        (agreement_uuid,),
    )
    return tuple(Pause(from_day=f, until_day=u) for f, u in cursor.fetchall())


def _fees(cursor: Any, agreement_uuid: Any) -> tuple[AdditionalFee, ...]:
    cursor.execute(
        "SELECT label, amount_minor, cadence, effective_from FROM agreement_fees "
        "WHERE agreement_id = %s ORDER BY label",
        (agreement_uuid,),
    )
    return tuple(
        AdditionalFee(
            label=label,
            amount_minor=amount,
            cadence=FeeCadence(cadence),
            effective_from=effective_from,
        )
        for label, amount, cadence, effective_from in cursor.fetchall()
    )


def _mandate(cursor: Any, agreement_uuid: Any) -> Mandate | None:
    cursor.execute(
        "SELECT agreed_by, agreed_at, terms_shown, frequency_shown, amount_basis_shown, "
        "cancellation_shown FROM mandates WHERE agreement_id = %s",
        (agreement_uuid,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    agreed_by, agreed_at, terms, frequency, basis, cancellation = row
    return Mandate(
        agreed_by=agreed_by,
        agreed_at_iso=agreed_at.isoformat(),
        terms_shown=terms,
        frequency_shown=frequency,
        amount_basis_shown=basis,
        cancellation_shown=cancellation,
    )


def payer_uuid_for(cursor: Any, external_id: str) -> UUID | None:
    cursor.execute("SELECT id FROM payers WHERE external_id = %s", (external_id,))
    row = cursor.fetchone()
    return None if row is None else as_uuid(row[0])


__all__ = [
    "StoredAgreement",
    "StoredGarage",
    "load_agreements_at_garage",
    "load_garage",
    "load_payers_at_garage",
    "payer_uuid_for",
    "store_agreement",
    "store_garage",
    "store_payer",
]
