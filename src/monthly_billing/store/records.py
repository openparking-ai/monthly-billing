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
from ..findings import (
    REFUSAL_AGREEMENT_HOME_MOVED,
    REFUSAL_GARAGE_MISMATCH,
    REFUSAL_VEHICLE_ALREADY_REGISTERED,
    Refused,
)
from ..garage import BillingDay, Garage, IdentityRule
from ..localday import day_of, zone
from .writes import as_uuid, guarded_insert, guarded_update


class GarageNotFound(LookupError):
    """A garage id names no row in the store. The ONE class of that name: the
    billing run, the store-backed coverage call and the store itself all raise
    this one, so a caller catching it catches every garage-not-found."""


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
    home_already = _home_of_stored(cursor, agreement.id)
    if home_already is not None and home_already != garage_uuid:
        raise Refused(
            REFUSAL_AGREEMENT_HOME_MOVED,
            f"agreement {agreement.id!r} version {agreement.version} is billed at garage "
            f"{garage.id!r}, but the versions the store already holds for it are billed "
            "at another garage. The home never moves; a different home is a different "
            "agreement.",
        )
    covered = covered_garages_of(cursor, agreement, home=StoredGarage(garage_uuid, garage))
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


def _home_of_stored(cursor: Any, agreement_external_id: str) -> UUID | None:
    """The garage uuid every stored version of this agreement is billed at --
    None when the store holds no version yet. By identity alone; the tenant
    policy scopes the read. Every version has the same home by construction
    (the refusal above), so the latest one's is the agreement's."""
    cursor.execute(
        "SELECT garage_id FROM agreements WHERE external_id = %s ORDER BY version DESC LIMIT 1",
        (agreement_external_id,),
    )
    row = cursor.fetchone()
    return None if row is None else as_uuid(row[0])


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
    """
    import psycopg  # the store extra; the engine never imports this module

    tenant_id = as_uuid(tenant_id)
    plan: list[tuple[StoredGarage, datetime, set[str], dict[str, UUID]]] = []
    for stored in covered:
        tz = zone(stored.garage.timezone)
        at = now if now is not None else datetime.now(tz)
        today = day_of(at, tz)
        listed = {stored.garage.normalise_identity(v) for v in agreement.vehicles}
        held = _held_elsewhere(cursor, stored.garage, stored.uuid, agreement, listed, today)
        plan.append((stored, at, listed, held))

    # Every refusal has now had its chance. A garage this version no longer
    # covers releases every row of this agreement's there -- BEFORE the
    # per-garage release, and after every refusal, like it.
    cursor.execute(
        "DELETE FROM vehicle_registrations WHERE agreement_external_id = %s "
        "AND NOT (garage_id = ANY(%s))",
        (agreement.id, [stored.uuid for stored in covered]),
    )
    for stored, at, listed, held in plan:
        garage, garage_uuid = stored.garage, stored.uuid
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
        if row is None or row[1] == agreement.id:
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
               a.access_entry_from, a.access_exit_by, g.external_id
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
               a.access_entry_from, a.access_exit_by, g.external_id
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
               a.access_entry_from, a.access_exit_by, g.external_id
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
    per version row."""
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
    "GarageNotFound",
    "StoredAgreement",
    "StoredGarage",
    "covered_garages_of",
    "load_agreements_at_garage",
    "load_agreements_covering_garage",
    "load_agreements_on_invoice",
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
