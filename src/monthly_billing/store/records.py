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

**AN AGREEMENT IS HOMED AT ONE GARAGE AND COVERS THE GARAGES IT LISTS, AND THE
STORE HAS ONE DOOR FOR EACH QUESTION.** ``load_agreements_at_garage`` answers
"which agreements are BILLED here" -- the billing run and the charge read it, and
it must never widen, because a loader that returned every agreement valid at a
garage would invoice one agreement at every garage it covers.
``load_agreements_covering_garage`` answers "which agreements are GOOD here" --
the store-backed coverage call reads it, and it is the one that fans out. Two
functions rather than a flag on one, so that the money door cannot be widened by
a default somebody forgets to pass. Migration 0004 holds the covered set, one
row per version per garage.

**ONE CAR, ONE AGREEMENT PER GARAGE -- AT EVERY GARAGE THE AGREEMENT COVERS.**
His ruling. ``store_agreement`` keeps ``vehicle_registrations`` -- the
garage-wide fact of which agreement a vehicle identity belongs to -- and REFUSES
by name (``REFUSAL_VEHICLE_ALREADY_REGISTERED``) a vehicle that another
agreement at the garage holds, naming that garage and that agreement and, if it
is cancelled, the day it frees the vehicle. The registrations FAN OUT: one row
per covered garage, each normalised under THAT garage's own identity rule,
because the rule is a property of the reader at that barrier and the same plate
folds differently at two garages. All or none: every listed identity is checked
at every covered garage before a row changes, and the first collision anywhere
refuses the whole registration. A version that drops a
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

**ONE AGREEMENT, ONE REGISTRAR -- AND THE STORE HAS A DOOR FOR THE OTHER
ONE.** An agreement whose ``registrar`` is OUTSIDE has its registrations
written by some system of the owner's, not by this module: ``store_agreement``
writes none of them and never releases one BY IDENTITY for such an agreement,
and the document lists no vehicles. What it still does is release the rows at
a garage the version no longer covers: which garages an agreement covers is a
property of the version, whoever writes the vehicles, and a row left at a
garage the door can no longer reach would be a registration nobody could
release. The registrar itself never changes between versions
(``REFUSAL_REGISTRAR_CHANGED``): handing the register from one writer to the
other is an operation nobody designed, refused as the home move is. The
outside registrar registers and releases ONE vehicle identity at a time
through ``register_from_outside`` and ``release_from_outside``, which walk the
same path a version's list walks: the same fan-out over the covered set, the
same per-garage normalisation, every refusal at every garage first, then the
writes, and the same handover of a cancelled holder's row on its day. A
release may name ONE covered garage and reach that garage alone (G46): two
garages that fold one plate differently can hold a stale row and a live one
under the same text, and the fan-out cannot take the one without the other --
and in that state the unnamed release refuses by name rather than take both
(G47, ``REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES``), writing nothing.
Both doors refuse by name (``REFUSAL_REGISTRAR_IS_THIS_MODULE``) an agreement whose
registrations this module writes, and the version path's writer refuses by
name (``REFUSAL_REGISTRAR_IS_OUTSIDE``) an agreement an outside registrar
writes -- ONE check, ``_registrar_must_be``, reached from both: two writers of
one agreement's rows would race, and the other path says so instead. A release
through the door that finds no row at any covered garage is refused by name
too (``REFUSAL_VEHICLE_NOT_REGISTERED``): for a register kept by one writer a
silent no-op hides exactly the divergence the rule exists to surface. The
door's answer names the normalised identity it stored, per covered garage,
because two garages fold the same plate differently and a caller that could
not see the stored form could not reconcile its register with this one. What
the door reads back is ``vehicle_registrations`` -- the garage's holder claim
-- never ``agreement_vehicles``, which is a version's own list and stays empty
for an outside registrar's agreement.

**AND THE REGISTER CAN BE READ, BY ANY READER.** ``show_register`` answers,
for an agreement of either registrar, what the store holds: the latest
version's registrar, status, cancellation day, home and covered garages, and
every ``vehicle_registrations`` row naming the agreement at ANY garage as the
identity stored there -- the rows a reconciliation reads, including one at a
garage the latest version no longer covers, which it names. It picks the
latest version with the same helper the door does (``_latest_version_of``),
builds no ``Agreement`` and no ``Garage`` -- every field it returns is a
column's own value under that column's constraint -- so a version the loaders
refuse still shows its register; it takes no instant and writes nothing.

**THE STORE'S IDS AND THE ENGINE'S IDS ARE DIFFERENT THINGS.** The engine
compares opaque strings -- a garage id, a payer id, an agreement id -- and never
parses them. The store keys rows by uuid and carries those strings as
``external_id``. ``Stored*`` pairs the two, so a caller that needs to write a row
about an engine value has the uuid beside it and never looks one up by string
twice.
"""

from __future__ import annotations

from collections.abc import Iterable
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
    Registrar,
    Status,
)
from ..findings import (
    REFUSAL_AGREEMENT_HOME_MOVED,
    REFUSAL_GARAGE_MISMATCH,
    REFUSAL_GARAGE_NOT_COVERED,
    REFUSAL_REGISTRAR_CHANGED,
    REFUSAL_REGISTRAR_IS_OUTSIDE,
    REFUSAL_REGISTRAR_IS_THIS_MODULE,
    REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES,
    REFUSAL_VEHICLE_ALREADY_REGISTERED,
    REFUSAL_VEHICLE_NOT_REGISTERED,
    Refused,
)
from ..garage import BillingDay, Garage, IdentityRule
from ..localday import day_of, zone
from .writes import as_uuid, guarded_insert, guarded_update


class GarageNotFound(LookupError):
    """A garage id names no row in the store. The ONE class of that name: the
    billing run, the store-backed coverage call and the store itself all raise
    this one, so a caller catching it catches every garage-not-found."""


class AgreementNotFound(LookupError):
    """An agreement id names no version row in the store. Raised by the
    registration door, which registers TO an agreement and cannot invent one."""


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
    """One agreement VERSION, with its covered garages, vehicles, pauses, fees
    and mandate.

    ``garage`` and ``garage_uuid`` are the HOME garage -- the one the agreement
    is billed at, and the one ``agreement.garage_id`` names; a mismatch is
    refused before anything is written, and so is a version whose home differs
    from the versions already stored for the same agreement: THE HOME NEVER
    MOVES (``REFUSAL_AGREEMENT_HOME_MOVED``). Every other garage the agreement covers
    is looked up in the store by its id, and one the store does not hold raises
    ``GarageNotFound`` -- a covered set naming a garage that does not exist
    cannot be registered, and inventing the garage is not this module's to do.

    A version is never edited -- see migration 0001 -- so a price change is a
    second call with the next version number, and the old row stays to be
    compared against. ``now`` is the registration instant; a cancelled
    agreement's vehicles are released per garage on its effective day, judged
    against ``now`` in THAT garage's zone. Left unset it is the wall clock.

    THE SWITCH. For an agreement whose ``registrar`` is OUTSIDE nothing here
    writes a ``vehicle_registrations`` row or releases one by identity. Those
    rows are the outside registrar's, written and released one identity at a
    time through the door below. What IS released, for either registrar, is
    every row of the agreement's at a garage this version no longer covers --
    the covered set is the version's own, whoever writes the cars. The version
    row, its covered set, pauses, fees and mandate are stored exactly as for
    any other agreement, and a version that would change the registrar is
    refused by name before anything is written.
    """
    tenant_id = as_uuid(tenant_id)
    garage_uuid, payer_uuid = as_uuid(garage_uuid), as_uuid(payer_uuid)
    if garage.id != agreement.garage_id:
        raise Refused(
            REFUSAL_GARAGE_MISMATCH,
            f"agreement {agreement.id!r} is billed at garage {agreement.garage_id!r} and "
            f"was stored under garage {garage.id!r}.",
        )
    # THE HOME NEVER MOVES. A later version billed at a different garage from
    # the versions already stored is refused by name, before anything is
    # written: moving an agreement's home is an operation nobody designed --
    # nothing says what it does to the month already invoiced at the old home,
    # to an unpaid invoice or a block sitting there, or to which garage's
    # billing day and currency govern next -- and three rounds each found a
    # different door answering that undesigned question differently. The prior
    # version is found by IDENTITY alone (UNIQUE (tenant_id, external_id,
    # version), 0001), so no garage enters the lookup. With the move refused,
    # the home is stable for the agreement's whole life, which is what makes
    # the home-keyed reads in entitlement_store correct for every state this
    # module can store. No cross-row database backstop exists for this; the
    # contract says so, as it does for G33.
    stored_already = _stored_identity_of(cursor, agreement.id)
    if stored_already is not None:
        home_already, registrar_already = stored_already
        if home_already != garage_uuid:
            raise Refused(
                REFUSAL_AGREEMENT_HOME_MOVED,
                f"agreement {agreement.id!r} version {agreement.version} is billed at garage "
                f"{garage.id!r}, but the versions the store already holds for it are billed "
                "at another garage. The home never moves; a different home is a different "
                "agreement.",
            )
        # THE REGISTRAR NEVER CHANGES EITHER, for the home move's reason: the
        # same one lookup, by identity, and nothing says what a hand-over does
        # to the rows the other writer already wrote.
        if registrar_already is not agreement.registrar:
            raise Refused(
                REFUSAL_REGISTRAR_CHANGED,
                f"agreement {agreement.id!r} version {agreement.version} names registrar "
                f"{agreement.registrar.value!r}, but the versions the store already holds "
                f"for it name {registrar_already.value!r}. The registrar never changes; a "
                "different registrar is a different agreement.",
            )
    covered = covered_garages_of(cursor, agreement, home=StoredGarage(garage_uuid, garage))
    if agreement.registrar is Registrar.OUTSIDE:
        # The outside registrar's rows are not this module's to write or to
        # release by identity -- register_vehicles refuses them by name -- but a
        # garage this version stopped covering is released for either registrar.
        _release_at_dropped_garages(cursor, agreement.id, covered)
    else:
        register_vehicles(cursor, tenant_id, covered, agreement, now=now)
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
            "registrar": agreement.registrar.value,
        },
    )
    for stored in covered:
        _insert(
            cursor,
            "agreement_garages",
            {"tenant_id": tenant_id, "agreement_id": agreement_uuid, "garage_id": stored.uuid},
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


def _stored_identity_of(cursor: Any, agreement_external_id: str) -> tuple[UUID, Registrar] | None:
    """The garage uuid every stored version of this agreement is billed at, and
    the registrar every stored version names -- None when the store holds no
    version yet. One lookup, by identity alone; the tenant policy scopes the
    read. Every version has the same home and the same registrar by
    construction (the two refusals above), so the latest one's are the
    agreement's."""
    cursor.execute(
        "SELECT garage_id, registrar FROM agreements WHERE external_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (agreement_external_id,),
    )
    row = cursor.fetchone()
    return None if row is None else (as_uuid(row[0]), Registrar(row[1]))


def covered_garages_of(
    cursor: Any, agreement: Agreement, *, home: StoredGarage
) -> tuple[StoredGarage, ...]:
    """Every garage the agreement covers, as the store holds it, home first.

    The home is handed in rather than looked up again -- the caller has it --
    and every other id is resolved by ``load_garage``. One the store does not
    hold raises ``GarageNotFound`` by id: nothing here invents a garage.
    """
    out = [home]
    for garage_id in agreement.covered_garage_ids:
        if garage_id == home.garage.id:
            continue
        stored = load_garage(cursor, garage_id)
        if stored is None:
            raise GarageNotFound(
                f"agreement {agreement.id!r} covers garage {garage_id!r}, which is not in "
                "the store."
            )
        out.append(stored)
    return tuple(out)


def register_vehicles(
    cursor: Any,
    tenant_id: Any,
    covered: tuple[StoredGarage, ...],
    agreement: Agreement,
    *,
    now: datetime | None = None,
) -> None:
    """Keep ``vehicle_registrations`` in step with this version of the agreement,
    at EVERY garage it covers.

    Every identity the version lists is registered to the agreement's IDENTITY
    (``external_id``) at each covered garage, normalised under that garage's own
    rule; one another agreement holds at any of them is refused by name, naming
    the garage and the holder, unless that agreement is cancelled and its
    effective day has passed at that garage, in which case the row is replaced
    in this same transaction. Identities the previous version listed and this
    one does not are released, at every garage; a garage the previous version
    covered and this one does not has every one of this agreement's rows
    released.

    Every refusal first -- at every garage -- then the releases, then the
    writes: a refusal writes nothing, so a caller that catches it and commits
    has committed nothing.

    The refusal walk and the writes are the shared halves the registration
    door reuses (``_plan_registrations``, ``_write_registrations``); the
    release BY IDENTITY between them is this path's alone, because only a
    version's own list can say which of the agreement's cars are no longer
    listed. The release at a garage the version dropped is shared with
    ``store_agreement``'s outside-registrar path, whose rows it may not touch
    otherwise. An agreement an outside registrar writes is refused here by
    name (``REFUSAL_REGISTRAR_IS_OUTSIDE``) -- this is the module's writer, and
    it is exported, so the check lives in it and not at a call site.
    """
    tenant_id = as_uuid(tenant_id)
    _registrar_must_be(agreement.id, agreement.registrar, Registrar.THIS_MODULE)
    plan = _plan_registrations(cursor, covered, agreement.id, agreement.vehicles, now)

    # Every refusal has now had its chance. A garage this version no longer
    # covers releases every row of this agreement's there -- BEFORE the
    # per-garage release, and after every refusal, like it.
    _release_at_dropped_garages(cursor, agreement.id, covered)
    for stored, at, listed, held in plan:
        # A version that drops a vehicle releases it -- AFTER every refusal.
        cursor.execute(
            "DELETE FROM vehicle_registrations WHERE garage_id = %s AND agreement_external_id = %s "
            "AND NOT (identity_normalised = ANY(%s))",
            (stored.uuid, agreement.id, sorted(listed)),
        )
        _write_registrations(cursor, tenant_id, stored, at, listed, held, agreement.id)


def _plan_registrations(
    cursor: Any,
    covered: tuple[StoredGarage, ...],
    agreement_id: str,
    vehicles: tuple[str, ...],
    now: datetime | None,
) -> list[tuple[StoredGarage, datetime, set[str], dict[str, UUID]]]:
    """THE REFUSALS, at every covered garage, before a row changes anywhere.
    Shared by the version path and the door: one walk, one order. Each entry
    is one covered garage's share: the garage, the instant judged in its zone,
    the identities normalised under its rule, and the rows a cancelled holder
    has released there (by identity) for this agreement to take."""
    plan: list[tuple[StoredGarage, datetime, set[str], dict[str, UUID]]] = []
    for stored in covered:
        tz = zone(stored.garage.timezone)
        at = now if now is not None else datetime.now(tz)
        today = day_of(at, tz)
        listed = {stored.garage.normalise_identity(v) for v in vehicles}
        held = _held_elsewhere(cursor, stored.garage, stored.uuid, agreement_id, listed, today)
        plan.append((stored, at, listed, held))
    return plan


def _write_registrations(
    cursor: Any,
    tenant_id: UUID,
    stored: StoredGarage,
    at: datetime,
    listed: set[str],
    held: dict[str, UUID],
    agreement_id: str,
) -> None:
    """THE WRITES at one garage, after every refusal everywhere: a released
    holder's row passes to this agreement; a row already this agreement's is
    left; anything else is inserted, with the UNIQUE as the backstop for a
    registration racing this one."""
    import psycopg  # the store extra; the engine never imports this module

    garage, garage_uuid = stored.garage, stored.uuid
    for identity in sorted(listed):
        if identity in held:
            # The holder is cancelled and its day has come: the row passes to
            # this agreement, in this transaction.
            guarded_update(
                cursor,
                "vehicle_registrations",
                {"agreement_external_id": agreement_id, "registered_at": at},
                {"id": held[identity]},
            )
            continue
        cursor.execute(
            "SELECT 1 FROM vehicle_registrations "
            "WHERE garage_id = %s AND identity_normalised = %s AND agreement_external_id = %s",
            (garage_uuid, identity, agreement_id),
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
                    "agreement_external_id": agreement_id,
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


def _release_at_dropped_garages(
    cursor: Any, agreement_id: str, covered: tuple[StoredGarage, ...]
) -> None:
    """Every row of this agreement's at a garage NOT in its covered set is
    released -- for either registrar, because which garages the agreement
    covers is the version's own fact, and a row at a garage the door can no
    longer reach is a registration nobody could release."""
    cursor.execute(
        "DELETE FROM vehicle_registrations WHERE agreement_external_id = %s "
        "AND NOT (garage_id = ANY(%s))",
        (agreement_id, [stored.uuid for stored in covered]),
    )


def _registrar_must_be(agreement_id: str, registrar: Registrar, expected: Registrar) -> None:
    """ONE AGREEMENT, ONE REGISTRAR -- the one check, reached from every writer.
    The version path's writer requires this module; both halves of the door
    require an outside registrar; each refuses the other by the other's name."""
    if registrar is expected:
        return
    if expected is Registrar.OUTSIDE:
        raise Refused(
            REFUSAL_REGISTRAR_IS_THIS_MODULE,
            f"agreement {agreement_id!r} names {registrar.value!r} as its registrar; the "
            "registration door is for an agreement whose registrar is "
            f"{Registrar.OUTSIDE.value!r}.",
        )
    raise Refused(
        REFUSAL_REGISTRAR_IS_OUTSIDE,
        f"agreement {agreement_id!r} names {registrar.value!r} as its registrar; the "
        "version path writes registrations for an agreement whose registrar is "
        f"{Registrar.THIS_MODULE.value!r}.",
    )


def _held_elsewhere(
    cursor: Any,
    garage: Garage,
    garage_uuid: UUID,
    agreement_id: str,
    listed: set[str],
    today: date,
) -> dict[str, UUID]:
    """The refusals at ONE garage, all of them, before a row changes. Walks
    every listed identity; the first one another agreement still holds raises
    by name, naming the garage. Returns the rows another agreement HAS released
    (cancelled, effective day reached), by identity, for the caller to take
    over."""
    released: dict[str, UUID] = {}
    for identity in sorted(listed):
        cursor.execute(
            "SELECT id, agreement_external_id FROM vehicle_registrations "
            "WHERE garage_id = %s AND identity_normalised = %s",
            (garage_uuid, identity),
        )
        row = cursor.fetchone()
        if row is None or row[1] == agreement_id:
            continue
        holder = row[1]
        frees_on = _released_on(cursor, holder)
        if frees_on is None or frees_on > today:
            raise Refused(
                REFUSAL_VEHICLE_ALREADY_REGISTERED,
                f"vehicle {identity!r} at garage {garage.id!r} is registered to "
                f"agreement {holder!r}"
                + (f", which frees it on {frees_on}." if frees_on else ", which is active."),
            )
        released[identity] = as_uuid(row[0])
    return released


def _released_on(cursor: Any, agreement_external_id: str) -> date | None:
    """The day the holding agreement frees its vehicles, if it is cancelled;
    None if it is active (or unknown to the store, which holds the row forever).

    By the agreement's IDENTITY alone. The holder of a registration at garage X
    may be billed at garage Y and merely cover X, so a read keyed on X's home
    rows would find nothing and call a cancelled holder active forever."""
    cursor.execute(
        "SELECT status, cancelled_effective_day FROM agreements "
        "WHERE external_id = %s ORDER BY version DESC LIMIT 1",
        (agreement_external_id,),
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
# The registration door: an OUTSIDE registrar's one vehicle at a time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredIdentity:
    """What the door stored (or released) at one covered garage: the garage's
    id and the identity in the form THAT garage compares in. The caller's
    means of reconciling its own register with this one -- two garages fold
    one plate differently, and a door that hid the stored form would leave the
    caller unable to tell one car from two. Nothing else travels on it."""

    garage_id: str
    identity_normalised: str


def register_from_outside(
    cursor: Any,
    tenant_id: Any,
    agreement_id: str,
    identity: str,
    *,
    now: datetime | None = None,
) -> tuple[RegisteredIdentity, ...]:
    """Register ONE vehicle identity to an agreement whose registrar is OUTSIDE,
    at every garage its latest version covers.

    The path a version's list walks, for one identity: every refusal at every
    covered garage first (``REFUSAL_VEHICLE_ALREADY_REGISTERED``, naming the
    garage and the holder), then the writes -- a cancelled holder's row whose
    day has come passes to this agreement in this same transaction; a row
    already this agreement's is left; the rest are inserted. Nothing is
    released: the door adds one car and takes none away, and
    ``release_from_outside`` is the other half. All or none, and a refusal
    writes nothing.

    An agreement the store does not hold raises ``AgreementNotFound``; one
    whose registrations this module writes is refused by name
    (``REFUSAL_REGISTRAR_IS_THIS_MODULE``) -- one agreement, one registrar.
    ``now`` is the registration instant, judged in each garage's zone; unset,
    the wall clock.

    Returns the normalised identity stored per covered garage, ordered by the
    garage's id.
    """
    tenant_id = as_uuid(tenant_id)
    covered = _outside_registrars_covered_set(cursor, tenant_id, agreement_id)
    plan = _plan_registrations(cursor, covered, agreement_id, (identity,), now)
    for stored, at, listed, held in plan:
        _write_registrations(cursor, tenant_id, stored, at, listed, held, agreement_id)
    return _stored_forms((stored, listed) for stored, _at, listed, _held in plan)


def release_from_outside(
    cursor: Any,
    tenant_id: Any,
    agreement_id: str,
    identity: str,
    garage_id: str | None = None,
) -> tuple[RegisteredIdentity, ...]:
    """Release ONE vehicle identity from an agreement whose registrar is
    OUTSIDE, at every garage its latest version covers -- or, given
    ``garage_id``, at THAT covered garage alone (G46): the row is deleted, as
    a version that drops a car deletes it -- there is no end date on a
    registration. The same mode check as the other half: an agreement whose
    registrations this module writes is refused by name.

    Returns the normalised identity released per garage AT WHICH A ROW WAS
    RELEASED, in the same shape as a registration, ordered by the garage's
    id. A garage where this agreement held no row for the identity is absent
    from the answer -- a version may have added a garage after the car was
    registered -- and an identity that held no row at ANY garage the release
    reached is refused by name (``REFUSAL_VEHICLE_NOT_REGISTERED``): for a
    register kept by one writer a silent no-op would hide that the two
    registers have diverged, and nothing is written.

    **Why a release can name a garage.** Two covered garages that disagree on
    the identity rule can hold, under one text, a STALE row at the exact-rule
    garage and the LIVE car's row at the folded one -- the car was re-registered
    under a plate that differs only in formatting, and at the folded garage the
    two plates are one row. The fan-out releases both; the only text that reaches
    the stale row is its own, and that text folds to the live form. So the
    caller names the garage, and the refusals come in this order, every one
    before any write: no such agreement (``AgreementNotFound``); a registrar
    that is this module; a garage the tenant does not hold (``GarageNotFound``,
    found by tenant AND id in this path's own lookup, so the tenant policy on
    or off makes no difference); a garage the agreement's LATEST version does
    not cover (``REFUSAL_GARAGE_NOT_COVERED`` -- a row left at a garage a
    version dropped is not reachable this way; the version that dropped it
    released it); no row of this agreement for this identity there
    (``REFUSAL_VEHICLE_NOT_REGISTERED``).

    **Why the fan-out can refuse (G47).** Without ``garage_id`` the release
    reaches every covered garage, and in the state above the text reaches the
    stale row AND the live car's: the module has no car, only text, and the
    fan-out cannot tell one from two. So before any DELETE the unnamed path
    asks whether the text names one row across the covered set -- whether a
    row it would take at one garage is ALSO the fold of a different identity
    this agreement holds as its own row at another covered garage. When it is,
    the release is refused by name (``REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES``),
    the detail naming both identities and both garages, and nothing is
    written; the way through is the named form. The check cannot fire for an
    agreement covering one garage -- there is no other garage -- and it fires
    for no other agreement's row and for no covered garage that holds no row.
    The unnamed refusals, in order, every one before any write: no such
    agreement; a registrar that is this module; the ambiguity; no row at any
    garage the release reached (``REFUSAL_VEHICLE_NOT_REGISTERED``) -- the
    last two cannot collide, since a text that reaches no row cannot be
    ambiguous. An unnamed release that is not ambiguous is what it was before
    the check existed: output, exit code, rows.
    """
    tenant_id = as_uuid(tenant_id)  # the tenant policy scopes the write; typed for the same reason
    covered = _outside_registrars_covered_set(cursor, tenant_id, agreement_id)
    reached = covered
    if garage_id is not None:
        reached = (_covered_garage_named(cursor, tenant_id, agreement_id, covered, garage_id),)
    # Every garage's form first, then the deletes: an identity that normalises
    # to nothing at one garage refuses before any garage's row has gone.
    forms = [(stored, {stored.garage.normalise_identity(identity)}) for stored in reached]
    if garage_id is None:
        _refuse_an_ambiguous_release(cursor, agreement_id, identity, covered)
    released = []
    for stored, listed in forms:
        cursor.execute(
            "DELETE FROM vehicle_registrations WHERE garage_id = %s "
            "AND agreement_external_id = %s AND identity_normalised = ANY(%s)",
            (stored.uuid, agreement_id, sorted(listed)),
        )
        if cursor.rowcount:
            released.append((stored, listed))
    if not released:
        raise Refused(
            REFUSAL_VEHICLE_NOT_REGISTERED,
            f"vehicle {identity!r} is registered to agreement {agreement_id!r} at none of "
            f"the {len(covered)} garage(s) it covers; nothing to release."
            if garage_id is None else
            f"vehicle {identity!r} holds no row of agreement {agreement_id!r} at garage "
            f"{garage_id!r}, the one garage this release named; nothing to release.",
        )
    return _stored_forms(released)


def _refuse_an_ambiguous_release(
    cursor: Any,
    agreement_id: str,
    identity: str,
    covered: tuple[StoredGarage, ...],
) -> None:
    """Refuse the unnamed release when the text names no single row across the
    covered set (G47). Reads and raises; writes nothing.

    For each covered garage ``g`` let ``n_g`` be the text in ``g``'s form and
    ``rows[g]`` the identities this agreement holds there. The fan-out would
    delete at every ``g`` with ``n_g`` in ``rows[g]``. The release is AMBIGUOUS
    when for such a ``g`` there are a DIFFERENT covered garage ``h`` and a row
    ``m`` of this agreement at ``h``, ``m`` not the text's own form there, whose
    form under ``g``'s rule is ``n_g``: the row the fan-out would take at ``g``
    is the fold of the text AND of a distinct identity the agreement holds at
    ``h``, and taking it removes coverage for a registration the release did
    not name.

    Stated over identities and folds, not cars: the store has no column that
    links one registration's rows across garages, so text is all there is.
    ``h is not g`` is in the rule rather than left to the ``m != n_h``
    exclusion, so that the check is independent of whether a garage's own rule
    is idempotent on what it stored -- a one-garage agreement can never reach
    this refusal. Only rows of THIS agreement count: another agreement's
    identity at a covered garage is that agreement's, and refuses nothing
    here. A stored form that normalises to nothing under ``g``'s rule (legal
    at an exact garage, empty under a folding one) is the fold of nothing, and
    is skipped rather than raised on.
    """
    at = []
    for stored in covered:
        # Sorted here, by code point: which pair the detail names when there is
        # more than one is then the same on every platform, not the collation's.
        rows = sorted(
            form
            for form, holder in registrations_at_garage(cursor, stored.uuid)
            if holder == agreement_id
        )
        at.append((stored, stored.garage.normalise_identity(identity), rows))
    for g, n_g, rows_g in at:
        if n_g not in rows_g:
            continue  # the fan-out takes nothing here, so nothing here is at stake
        for h, n_h, rows_h in at:
            if h is g:
                continue
            for m in rows_h:
                if m == n_h:
                    continue  # the text's own row there, the one the release names
                try:
                    folded = g.garage.normalise_identity(m)
                except ValueError:
                    continue
                if folded == n_g:
                    raise Refused(
                        REFUSAL_RELEASE_AMBIGUOUS_ACROSS_GARAGES,
                        f"vehicle {identity!r} would release row {n_g!r} of agreement "
                        f"{agreement_id!r} at garage {g.garage.id!r}, which is also the "
                        f"form there of {m!r}, a different identity this agreement holds "
                        f"at garage {h.garage.id!r}; the text names no single row across "
                        f"the covered set, so nothing was released.",
                    )


def _covered_garage_named(
    cursor: Any,
    tenant_id: UUID,
    agreement_id: str,
    covered: tuple[StoredGarage, ...],
    garage_id: str,
) -> StoredGarage:
    """The one covered garage a release names, or the two refusals in the
    order G46 states them: a garage this TENANT does not hold, then a garage
    the agreement's latest version does not cover.

    The lookup is by ``tenant_id AND external_id`` in this path's own SQL --
    the ``_latest_version_of`` precedent -- rather than through ``load_garage``,
    which the money doors read and which is scoped by the tenant policy alone:
    with the policy off, ``load_garage`` would find another tenant's garage of
    the same id and this function would then refuse it as 'not covered', a
    different sentence for the same fact. Only the uuid is read: the garage's
    options and rule come from the covered set, already loaded, so nothing
    here builds a ``Garage`` a second time.
    """
    cursor.execute(
        "SELECT id FROM garages WHERE tenant_id = %s AND external_id = %s",
        (tenant_id, garage_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise GarageNotFound(f"no garage with id {garage_id!r} in the store.")
    garage_uuid = as_uuid(row[0])
    for stored in covered:
        if stored.uuid == garage_uuid:
            return stored
    raise Refused(
        REFUSAL_GARAGE_NOT_COVERED,
        f"agreement {agreement_id!r} covers "
        f"{', '.join(repr(stored.garage.id) for stored in covered)} in its latest version, "
        f"and the release named garage {garage_id!r}; nothing to release there.",
    )


def _stored_forms(
    forms: Iterable[tuple[StoredGarage, set[str]]],
) -> tuple[RegisteredIdentity, ...]:
    """The door's answer: one entry per covered garage, the garage's id and the
    one identity as stored there, ordered by garage id -- deterministic, for the
    reason the release walks ``sorted(listed)``."""
    return tuple(
        sorted(
            (
                RegisteredIdentity(garage_id=stored.garage.id, identity_normalised=form)
                for stored, listed in forms
                for form in listed
            ),
            key=lambda entry: entry.garage_id,
        )
    )


@dataclass(frozen=True)
class _LatestVersion:
    """The agreement's latest version row, as stored: the columns a reader by
    agreement identity needs and no conversion beyond the uuid's. The
    registrar and the status are the row's own text, constrained by the CHECKs
    of 0005 and 0001; a caller that needs the enum makes it."""

    uuid: UUID
    version: int
    registrar: str
    status: str
    cancelled_effective_day: date | None
    home_uuid: UUID


def _latest_version_of(cursor: Any, tenant_id: UUID, agreement_id: str) -> _LatestVersion:
    """THE ONE LATEST-VERSION RULE for a read by agreement identity: the
    highest version the tenant holds under this id, or ``AgreementNotFound``.
    The registration door and the register read both pick their row here, so
    the two cannot disagree on which version is current. The tenant is stated
    in the predicate as well as by the policy: the read that shows a register
    proves its own scoping with the row policy off, and a ``LIMIT 1`` across
    tenants would otherwise pick another tenant's higher version."""
    cursor.execute(
        "SELECT id, version, registrar, status, cancelled_effective_day, garage_id "
        "FROM agreements WHERE tenant_id = %s AND external_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (tenant_id, agreement_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise AgreementNotFound(f"no agreement with id {agreement_id!r} in the store.")
    uuid, version, registrar, status, cancelled_day, home_uuid = row
    return _LatestVersion(
        uuid=as_uuid(uuid),
        version=version,
        registrar=registrar,
        status=status,
        cancelled_effective_day=cancelled_day,
        home_uuid=as_uuid(home_uuid),
    )


def _outside_registrars_covered_set(
    cursor: Any, tenant_id: UUID, agreement_id: str
) -> tuple[StoredGarage, ...]:
    """The covered set of the agreement's LATEST version, home first, for an
    agreement whose registrar is OUTSIDE -- and the two refusals on the way:
    no version in the store, and a registrar that is this module. The version
    is the one ``_latest_version_of`` picks; the door reads its uuid and its
    registrar from it and nothing else."""
    latest = _latest_version_of(cursor, tenant_id, agreement_id)
    _registrar_must_be(agreement_id, Registrar(latest.registrar), Registrar.OUTSIDE)
    out = []
    for garage_id in _covered_garage_ids(cursor, latest.uuid):
        stored = load_garage(cursor, garage_id)
        if stored is None:  # pragma: no cover - 0004's key makes this unreachable
            raise GarageNotFound(
                f"agreement {agreement_id!r} covers garage {garage_id!r}, which is not in "
                "the store."
            )
        out.append(stored)
    return tuple(out)


# ---------------------------------------------------------------------------
# The register read: an agreement's registrations, for ANY reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgreementRegister:
    """An agreement's register as the store holds it, and the set it is kept
    against: the latest version's registrar, status, cancellation day, home and
    covered garages; every ``vehicle_registrations`` row naming the agreement,
    at ANY garage, as {garage, identity as stored}; and the garages of rows the
    latest version does not cover. NOTHING ELSE TRAVELS -- not the price, the
    payer, the spots, the fees, the pauses, the mandate, the access hours, the
    start day, the version's own vehicle list or a registration's instant --
    and the field set is derived from this class, so a field added here is a
    field the test that says so sees the day it exists.

    Every field is a column's own value, backed by that column's constraint,
    or is derived in Python from two of them:

    * ``agreement`` -- ``agreements.external_id``: text NOT NULL,
      UNIQUE (tenant_id, external_id, version) (0001).
    * ``version`` -- ``agreements.version``: integer NOT NULL CHECK (version >= 1).
    * ``registrar`` -- ``agreements.registrar``: CHECK (registrar IN
      ('this_module', 'outside')) (0005).
    * ``status`` -- ``agreements.status``: CHECK (status IN ('active', 'cancelled')).
    * ``cancelled_effective_day`` -- ``agreements.cancelled_effective_day``: date,
      present iff cancelled (``agreements_cancellation_has_a_date``).
    * ``home_garage`` -- ``garages.external_id`` via ``agreements.garage_id``: text
      NOT NULL, UNIQUE (tenant_id, external_id); ``agreements_garage_in_tenant`` (0003).
    * ``covered_garages`` -- ``garages.external_id`` via ``agreement_garages``:
      ``agreement_garages_garage_in_tenant`` (0004).
    * ``registrations[].garage`` -- ``garages.external_id`` via
      ``vehicle_registrations.garage_id``: ``vehicle_registrations_garage_in_tenant`` (0003).
    * ``registrations[].identity_normalised`` -- ``vehicle_registrations.identity_normalised``:
      text NOT NULL CHECK (length(btrim(..)) > 0) (0003).
    * ``garages_not_covered`` -- derived: the registrations' garages minus the covered set.

    So the read validates nothing: no ``Agreement``, no ``Garage`` is built,
    and a version the loaders refuse or a garage stored with an unreadable
    option still shows its register in full -- the one reader that needs the
    register is the one reconciling, and a read that refused would hide it.
    """

    agreement: str
    version: int
    registrar: str
    status: str
    cancelled_effective_day: date | None
    home_garage: str
    covered_garages: tuple[str, ...]
    registrations: tuple[RegisteredIdentity, ...]
    garages_not_covered: tuple[str, ...]

    def as_document(self) -> dict[str, Any]:
        """The JSON shape: the nine keys above, every list sorted by code
        point in Python (never by the database's collation), the day as an
        ISO date or null. The command line prints exactly this."""
        document: dict[str, Any] = {
            "agreement": self.agreement,
            "version": self.version,
            "registrar": self.registrar,
            "status": self.status,
            "cancelled_effective_day": (
                None
                if self.cancelled_effective_day is None
                else self.cancelled_effective_day.isoformat()
            ),
            "home_garage": self.home_garage,
            "covered_garages": list(self.covered_garages),
            "registrations": [
                {"garage": entry.garage_id, "identity_normalised": entry.identity_normalised}
                for entry in self.registrations
            ],
            "garages_not_covered": list(self.garages_not_covered),
        }
        return document


def _garage_external_id(cursor: Any, garage_uuid: UUID) -> str:
    """The garage's id as the engine knows it, by the store's uuid -- the one
    column, and nothing of the garage that could fail to load."""
    cursor.execute("SELECT external_id FROM garages WHERE id = %s", (garage_uuid,))
    (external_id,) = cursor.fetchone()
    return external_id


def show_register(cursor: Any, tenant_id: Any, agreement_id: str) -> AgreementRegister:
    """An agreement's register, for ANY reader, from its LATEST version --
    the version ``_latest_version_of`` picks, the same rule the registration
    door applies. Answers for either registrar and says which in
    ``registrar``: the single-writer rule governs WRITES, and a read refuses
    nobody on it. Takes no instant and derives nothing: a cancelled agreement
    whose day has passed is shown ``cancelled`` with its day and with whatever
    rows are still stored. Writes nothing. An agreement the store does not
    hold -- or one only another tenant holds -- raises ``AgreementNotFound``;
    one with no rows answers an empty register, which is not a refusal.

    Every ``vehicle_registrations`` row naming the agreement is shown, at ANY
    garage -- including one the latest version no longer covers, which is
    named again in ``garages_not_covered``. The module releases such rows when
    the version that drops the garage is stored and nothing in the schema
    forbids one (there is no key from ``vehicle_registrations`` to
    ``agreement_garages``), so a row written past the module can sit there,
    and it is exactly the row a reconciliation exists to find.

    Sorted here, by code point: ``covered_garages`` and ``garages_not_covered``
    by garage id, ``registrations`` by (identity, garage). The database's
    ``ORDER BY`` on text is the machine's collation and differs between
    machines; a program reading this needs one order.
    """
    tenant_id = as_uuid(tenant_id)
    latest = _latest_version_of(cursor, tenant_id, agreement_id)
    home_garage = _garage_external_id(cursor, latest.home_uuid)
    covered = tuple(sorted(_covered_garage_ids(cursor, latest.uuid)))
    cursor.execute(
        "SELECT g.external_id, r.identity_normalised "
        "FROM vehicle_registrations r JOIN garages g ON g.id = r.garage_id "
        "WHERE r.tenant_id = %s AND r.agreement_external_id = %s",
        (tenant_id, agreement_id),
    )
    registrations = tuple(
        sorted(
            (RegisteredIdentity(garage_id=garage, identity_normalised=identity)
             for garage, identity in cursor.fetchall()),
            key=lambda entry: (entry.identity_normalised, entry.garage_id),
        )
    )
    not_covered = tuple(sorted({e.garage_id for e in registrations} - set(covered)))
    return AgreementRegister(
        agreement=agreement_id,
        version=latest.version,
        registrar=latest.registrar,
        status=latest.status,
        cancelled_effective_day=latest.cancelled_effective_day,
        home_garage=home_garage,
        covered_garages=covered,
        registrations=registrations,
        garages_not_covered=not_covered,
    )


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
    """(payer uuid, payer id) for every payer with an agreement BILLED at the
    garage -- judged on each agreement's LATEST version, like the loader above,
    so a payer whose agreement moved its home away is not reported here on the
    strength of the version that used to be."""
    cursor.execute(
        "SELECT DISTINCT p.id, p.external_id FROM ("
        "  SELECT DISTINCT ON (external_id) * FROM agreements ORDER BY external_id, version DESC"
        ") a JOIN payers p ON p.id = a.payer_id "
        "WHERE a.garage_id = %s ORDER BY p.external_id",
        (as_uuid(garage_uuid),),
    )
    return tuple((as_uuid(uuid), external) for uuid, external in cursor.fetchall())


def load_agreements_at_garage(
    cursor: Any, garage_uuid: Any, payer_uuid: Any = None
) -> tuple[StoredAgreement, ...]:
    """Every agreement BILLED at a garage -- or one payer's -- at its LATEST
    version, as engine values. THE MONEY DOOR.

    The latest version per external id is what the run prices -- the same rule
    ``is_covered`` applies when handed several versions. A row the engine's
    constructor refuses raises here, so a caller loading one payer at a time can
    refuse that payer and go on to the next.

    Keyed on ``agreements.garage_id``, the HOME, and on nothing wider: an
    agreement that covers this garage without being billed here is not
    returned, or the run would invoice it once per garage it is valid at. The
    access question is ``load_agreements_covering_garage``.

    THE LATEST VERSION IS CHOSEN FIRST, and the garage and payer filters are
    applied to THAT row -- never the other way round. Filtering on the garage
    first and then taking the latest survivor returned, at a garage an
    agreement's newer version had LEFT, the older version still homed there --
    and the run invoiced one agreement at two garages. ``payer_id`` lives per
    version too, so it is read off the latest row for the same reason.
    """
    cursor.execute(
        """
        SELECT a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,
               a.monthly_price_minor, a.start_day, a.status, a.cancelled_effective_day,
               a.access_entry_from, a.access_exit_by, g.external_id, a.registrar
        FROM (
            SELECT DISTINCT ON (external_id) *
            FROM agreements
            ORDER BY external_id, version DESC
        ) a
        JOIN payers p ON p.id = a.payer_id
        JOIN garages g ON g.id = a.garage_id
        WHERE a.garage_id = %s AND (%s::uuid IS NULL OR a.payer_id = %s::uuid)
        ORDER BY a.external_id
        """,
        (as_uuid(garage_uuid), None if payer_uuid is None else as_uuid(payer_uuid),
         None if payer_uuid is None else as_uuid(payer_uuid)),
    )
    return _as_stored(cursor, cursor.fetchall())


def load_agreements_covering_garage(cursor: Any, garage_uuid: Any) -> tuple[StoredAgreement, ...]:
    """Every agreement whose LATEST version COVERS a garage, as engine values.
    THE ACCESS DOOR -- the store-backed coverage call reads this and nothing
    else does.

    The latest version per external id is chosen FIRST and then filtered by
    coverage, so an agreement whose newer version dropped this garage is not
    returned on the strength of an older version that listed it.
    """
    cursor.execute(
        """
        SELECT a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,
               a.monthly_price_minor, a.start_day, a.status, a.cancelled_effective_day,
               a.access_entry_from, a.access_exit_by, g.external_id, a.registrar
        FROM (
            SELECT DISTINCT ON (external_id) *
            FROM agreements
            ORDER BY external_id, version DESC
        ) a
        JOIN agreement_garages ag ON ag.agreement_id = a.id AND ag.garage_id = %s
        JOIN payers p ON p.id = a.payer_id
        JOIN garages g ON g.id = a.garage_id
        ORDER BY a.external_id
        """,
        (as_uuid(garage_uuid),),
    )
    return _as_stored(cursor, cursor.fetchall())


def load_agreements_on_invoice(cursor: Any, invoice_uuid: Any) -> tuple[StoredAgreement, ...]:
    """Every agreement the invoice's own lines name, at its LATEST version, as
    engine values. THE CHARGE GATE'S DOOR -- and the garage does not enter it.

    An invoice line records the version that PRICED it (G5); what a charge
    needs is the agreement's mandate, and that is the LATEST version's -- a
    payer who agreed to a card after the invoice was issued is charged on it,
    and one who withdrew it is not. So the lines give the IDENTITIES and the
    store gives each identity's latest version. The home never moves through
    the store (``REFUSAL_AGREEMENT_HOME_MOVED``); for a row written past the
    module at another home, a garage-keyed read here returned nothing, and
    nothing is not a gate.
    """
    cursor.execute(
        """
        SELECT a.id, a.external_id, a.version, a.payer_id, p.external_id, a.spots,
               a.monthly_price_minor, a.start_day, a.status, a.cancelled_effective_day,
               a.access_entry_from, a.access_exit_by, g.external_id, a.registrar
        FROM (
            SELECT DISTINCT ON (external_id) *
            FROM agreements
            WHERE external_id IN (
                SELECT named.external_id
                FROM invoice_lines l
                JOIN agreements named ON named.id = l.agreement_id
                WHERE l.invoice_id = %s
            )
            ORDER BY external_id, version DESC
        ) a
        JOIN payers p ON p.id = a.payer_id
        JOIN garages g ON g.id = a.garage_id
        ORDER BY a.external_id
        """,
        (as_uuid(invoice_uuid),),
    )
    return _as_stored(cursor, cursor.fetchall())


def _as_stored(cursor: Any, heads: list[tuple]) -> tuple[StoredAgreement, ...]:
    """Rows of the agreement head shape, read back through the engine's own
    constructor -- the covered set, vehicles, pauses, fees and mandate fetched
    per version row. The registrar rides on the head row itself: it is a column
    of the row the loader already selected, and the constructor needs it to
    judge the vehicle list it is handed -- an outside registrar's version
    legitimately lists none."""
    out: list[StoredAgreement] = []
    for row in heads:
        (
            uuid, external_id, version, payer_uuid, payer_external, spots, price,
            start_day, status, cancelled_day, entry_from, exit_by, garage_external,
            registrar,
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
                    covered_garage_ids=_covered_garage_ids(cursor, uuid),
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
                    registrar=Registrar(registrar),
                ),
            )
        )
    return tuple(out)


def _as_time(value: Any) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def _covered_garage_ids(cursor: Any, agreement_uuid: Any) -> tuple[str, ...]:
    """The covered set of one version, as the engine's garage ids, home first
    -- ``Agreement`` refuses a set that does not hold the home, so a version
    row written past the module without its 0004 rows refuses to load."""
    cursor.execute(
        """
        SELECT g.external_id
        FROM agreement_garages ag
        JOIN garages g ON g.id = ag.garage_id
        JOIN agreements a ON a.id = ag.agreement_id
        WHERE ag.agreement_id = %s
        ORDER BY (g.id = a.garage_id) DESC, g.external_id
        """,
        (agreement_uuid,),
    )
    return tuple(row[0] for row in cursor.fetchall())


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
    "AgreementNotFound",
    "AgreementRegister",
    "GarageNotFound",
    "RegisteredIdentity",
    "StoredAgreement",
    "StoredGarage",
    "covered_garages_of",
    "load_agreements_at_garage",
    "load_agreements_covering_garage",
    "load_agreements_on_invoice",
    "load_garage",
    "load_payers_at_garage",
    "payer_uuid_for",
    "register_from_outside",
    "register_vehicles",
    "registration_for",
    "registrations_at_garage",
    "release_from_outside",
    "show_register",
    "store_agreement",
    "store_garage",
    "store_payer",
]
