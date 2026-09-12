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

**ONE CAR, ONE AGREEMENT PER GARAGE.** His ruling. ``store_agreement`` keeps
``vehicle_registrations`` -- the garage-wide fact of which agreement a vehicle
identity belongs to -- and REFUSES by name (``REFUSAL_VEHICLE_ALREADY_REGISTERED``)
a vehicle that another agreement at the garage holds, naming that agreement
and, if it is cancelled, the day it frees the vehicle. A version that drops a
vehicle releases it; a cancelled agreement keeps its vehicles until its
effective day -- they are still covered until then -- and the next registration
on or after that day replaces the row. The UNIQUE in 0003 is the backstop for a
raw insert and for two registrations racing, caught by name.

**A REFUSAL WRITES NOTHING.** Every listed identity is checked and the first
one another agreement holds raises BEFORE the release ``DELETE`` and before
any insert or update -- the order every other write in this module has. The
second outside round ran the other order: a version that dropped one car and
was refused on another left the dropped car's registration deleted in the
caller's open transaction, and a caller that committed after catching the
refusal had a stored agreement covering a car the lane called NO_AGREEMENT.
Now a refusal leaves the transaction exactly as it found it. Two registrations
racing are different: the second's transaction is aborted by the UNIQUE before
the refusal names it, nothing is written, and the caller rolls back.

**THE STORE'S IDS AND THE ENGINE'S IDS ARE DIFFERENT THINGS.** The engine
compares opaque strings -- a garage id, a payer id, an agreement id -- and never
parses them. The store keys rows by uuid and carries those strings as
``external_id``. ``Stored*`` pairs the two, so a caller that needs to write a row
about an engine value has the uuid beside it and never looks one up by string
twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
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
from ..findings import REFUSAL_VEHICLE_ALREADY_REGISTERED, Refused
from ..garage import BillingDay, Garage, IdentityRule
from ..localday import day_of, zone
from .writes import as_uuid, guarded_insert, guarded_update

#: The constraint whose violation MEANS "registered elsewhere" (migration 0003).
#: The module refuses by name before it fires; this is the backstop's name.
ONE_AGREEMENT_PER_GARAGE = "vehicle_registrations_one_agreement_per_garage"


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
    *,
    now: datetime | None = None,
) -> UUID:
    """One agreement VERSION, with its vehicles, pauses, fees and mandate.

    A version is never edited -- see migration 0001 -- so a price change is a
    second call with the next version number, and the old row stays to be
    compared against. ``now`` is the registration instant; a cancelled
    agreement's vehicles are released on its effective day, judged against
    ``now`` in the garage's zone. Left unset it is the wall clock.
    """
    tenant_id = as_uuid(tenant_id)
    garage_uuid, payer_uuid = as_uuid(garage_uuid), as_uuid(payer_uuid)
    register_vehicles(cursor, tenant_id, garage, garage_uuid, agreement, now=now)
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


def register_vehicles(
    cursor: Any,
    tenant_id: Any,
    garage: Garage,
    garage_uuid: Any,
    agreement: Agreement,
    *,
    now: datetime | None = None,
) -> None:
    """Keep ``vehicle_registrations`` in step with this version of the agreement.

    Every identity the version lists is registered to the agreement's IDENTITY
    (``external_id``); one another agreement holds is refused by name unless
    that agreement is cancelled and its effective day has passed, in which case
    the row is replaced in this same transaction. Identities the previous
    version listed and this one does not are released.

    Every refusal first, then the release, then the writes: a refusal writes
    nothing, so a caller that catches it and commits has committed nothing.
    """
    import psycopg  # the store extra; the engine never imports this module

    tenant_id, garage_uuid = as_uuid(tenant_id), as_uuid(garage_uuid)
    tz = zone(garage.timezone)
    at = now if now is not None else datetime.now(tz)
    today = day_of(at, tz)
    listed = {garage.normalise_identity(v) for v in agreement.vehicles}

    held = _held_elsewhere(cursor, garage, garage_uuid, agreement, listed, today)
    # A version that drops a vehicle releases it -- AFTER every refusal.
    cursor.execute(
        "DELETE FROM vehicle_registrations WHERE garage_id = %s AND agreement_external_id = %s "
        "AND NOT (identity_normalised = ANY(%s))",
        (garage_uuid, agreement.id, sorted(listed)),
    )
    for identity in sorted(listed):
        if identity in held:
            # The holder is cancelled and its day has come: the row passes to
            # this agreement, in this transaction.
            guarded_update(
                cursor,
                "vehicle_registrations",
                {"agreement_external_id": agreement.id, "registered_at": at},
                {"id": held[identity]},
            )
            continue
        cursor.execute(
            "SELECT 1 FROM vehicle_registrations "
            "WHERE garage_id = %s AND identity_normalised = %s AND agreement_external_id = %s",
            (garage_uuid, identity, agreement.id),
        )
        if cursor.fetchone() is not None:
            continue  # already this agreement's
        try:
            guarded_insert(
                cursor,
                "vehicle_registrations",
                {
                    "tenant_id": tenant_id,
                    "garage_id": garage_uuid,
                    "identity_normalised": identity,
                    "agreement_external_id": agreement.id,
                    "registered_at": at,
                },
            )
            cursor.fetchone()
        except psycopg.errors.UniqueViolation as violation:
            # Two registrations racing: the second lands here, and the name of
            # the constraint is what says "registered elsewhere".
            if violation.diag.constraint_name != ONE_AGREEMENT_PER_GARAGE:
                raise
            raise Refused(
                REFUSAL_VEHICLE_ALREADY_REGISTERED,
                f"vehicle {identity!r} at garage {garage.id!r} was registered to another "
                "agreement at the same instant.",
            ) from None


def _held_elsewhere(
    cursor: Any,
    garage: Garage,
    garage_uuid: UUID,
    agreement: Agreement,
    listed: set[str],
    today: date,
) -> dict[str, UUID]:
    """The refusals, all of them, before a row changes. Walks every listed
    identity; the first one another agreement still holds raises by name.
    Returns the rows another agreement HAS released (cancelled, effective day
    reached), by identity, for the caller to take over."""
    released: dict[str, UUID] = {}
    for identity in sorted(listed):
        cursor.execute(
            "SELECT id, agreement_external_id FROM vehicle_registrations "
            "WHERE garage_id = %s AND identity_normalised = %s",
            (garage_uuid, identity),
        )
        row = cursor.fetchone()
        if row is None or row[1] == agreement.id:
            continue
        holder = row[1]
        frees_on = _released_on(cursor, garage_uuid, holder)
        if frees_on is None or frees_on > today:
            raise Refused(
                REFUSAL_VEHICLE_ALREADY_REGISTERED,
                f"vehicle {identity!r} at garage {garage.id!r} is registered to "
                f"agreement {holder!r}"
                + (f", which frees it on {frees_on}." if frees_on else ", which is active."),
            )
        released[identity] = as_uuid(row[0])
    return released


def _released_on(cursor: Any, garage_uuid: Any, agreement_external_id: str) -> date | None:
    """The day the holding agreement frees its vehicles, if it is cancelled;
    None if it is active (or unknown to the store, which holds the row forever)."""
    cursor.execute(
        "SELECT status, cancelled_effective_day FROM agreements "
        "WHERE garage_id = %s AND external_id = %s ORDER BY version DESC LIMIT 1",
        (garage_uuid, agreement_external_id),
    )
    row = cursor.fetchone()
    if row is None or row[0] != Status.CANCELLED.value:
        return None
    return row[1]


def registration_for(cursor: Any, garage_uuid: Any, identity_normalised: str) -> str | None:
    """The agreement (external id) a normalised identity is registered to, or None."""
    cursor.execute(
        "SELECT agreement_external_id FROM vehicle_registrations "
        "WHERE garage_id = %s AND identity_normalised = %s",
        (as_uuid(garage_uuid), identity_normalised),
    )
    row = cursor.fetchone()
    return None if row is None else row[0]


def registrations_at_garage(cursor: Any, garage_uuid: Any) -> tuple[tuple[str, str], ...]:
    """(identity_normalised, agreement external id) for every registered vehicle."""
    cursor.execute(
        "SELECT identity_normalised, agreement_external_id FROM vehicle_registrations "
        "WHERE garage_id = %s ORDER BY identity_normalised",
        (as_uuid(garage_uuid),),
    )
    return tuple((i, a) for i, a in cursor.fetchall())


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
    "register_vehicles",
    "registration_for",
    "registrations_at_garage",
    "store_agreement",
    "store_garage",
    "store_payer",
]
