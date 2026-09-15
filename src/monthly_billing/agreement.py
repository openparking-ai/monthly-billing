"""The agreement: one document per account, versioned, refusing rather than assuming.

**The two numbers that are not the same number.** An agreement buys N SPOTS and
lists M VEHICLES, and M has nothing to do with N. Twenty cars registered against
ten spots is an ordinary, legal agreement: the household or the company may park
any ten of its twenty cars at once. The module states the entitlement; something
else counts what is inside and decides that the eleventh car is a transient.
Nothing here counts anything, because counting needs live session state and a
module that needed live session state would have stopped being standalone.

**Money is integer minor units, refused at load, at every leaf.** See money.py.

**ACCESS HOURS ARE A CONDITION ON A STAY, NOT ON AN INSTANT -- and that is the
one place this document's shape is decided by something outside it.** A garage
that sells entry and exit within stated hours is selling a condition with two
halves, and a stay meets it or does not. The sibling rate module settled the
disposition for exactly this shape: a special rate applies only if the stay meets
EVERY one of its conditions, entry and exit; miss one and it does not apply at
all. The same rule holds here, so `access_hours` states both halves and the
entitlement answer says which halves it was able to check. See entitlement.py --
this is the field that made the answer carry `unchecked`.

**AN AGREEMENT IS BILLED AT ONE HOME GARAGE AND MAY COVER OTHERS THE OWNER
LISTS.** ``garage_id`` is the HOME: the garage whose billing day, currency,
timezone, grace period and invoice govern this agreement -- every money decision
is keyed on it and nothing here changes that. ``covered_garage_ids`` is the set
of garages at which the agreement's vehicles are covered, the home included,
stated by listing them. There is no "everywhere" flag and no default: an implicit
"all" cannot be audited and cannot be refused, and a document that omits the set
is refused by name rather than read as the home alone. The entitlement is
ACROSS the covered set -- ten spots is ten cars inside across those garages, not
ten at each. His ruling, 2026-09-15.

**A PAUSE COVERS NOTHING AND BILLS NOTHING.** Both halves, deliberately: a pause
that suspended billing while still opening the barrier would be a free month, and
one that suspended coverage while still billing would be theft. It is one field
with one meaning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from enum import Enum
from typing import Any

from .money import as_non_negative_minor, refuse_non_integer_money


class InvalidAgreement(ValueError):
    """The document is not an agreement this module can read."""


class FeeCadence(Enum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"


class Status(Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AdditionalFee:
    """A line the owner named and priced. M4: "add a new line and add whatever".

    A guaranteed or reserved space is one of these, not a special mechanism --
    which is the point of the field being free-form. Every line appears on the
    invoice under its own label, so an owner who invents a fee gets it itemised
    without anybody adding a rule type for it.

    ``effective_from`` is what decides which invoice a ONE_TIME line lands on: the
    first invoice whose period contains it. Without it a one-time fee would land
    on whichever invoice happened to run next, which is a different answer
    depending on when somebody pressed a button.
    """

    label: str
    amount_minor: int
    cadence: FeeCadence
    effective_from: date

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise InvalidAgreement(
                "an additional fee needs a label. It is what the payer reads on the "
                "invoice, and an unlabelled amount is the thing this module exists "
                "to not produce."
            )
        as_non_negative_minor(self.amount_minor, f"additional_fee[{self.label}].amount_minor")
        if not isinstance(self.cadence, FeeCadence):
            raise InvalidAgreement(
                f"additional fee {self.label!r} has cadence {self.cadence!r}; it is "
                f"one of {[c.value for c in FeeCadence]}. There is no default: a "
                "one-time fee billed monthly, or a monthly fee billed once, is a "
                "wrong amount either way."
            )


@dataclass(frozen=True)
class AccessHours:
    """Entry no earlier than, exit no later than -- local time, both halves stated.

    Both are required. An agreement that stated only one half would be silently
    unbounded on the other, and "unbounded" is a thing an operator should have to
    write rather than something they get by omission.
    """

    entry_from: time
    exit_by: time

    def __post_init__(self) -> None:
        for name, value in (("entry_from", self.entry_from), ("exit_by", self.exit_by)):
            if not isinstance(value, time):
                raise InvalidAgreement(
                    f"access_hours.{name} is {value!r}; it is a local time of day."
                )
            if value.tzinfo is not None:
                raise InvalidAgreement(
                    f"access_hours.{name} carries a timezone. Access hours are the "
                    "garage's own local clock -- the garage states its zone once, and "
                    "an hour that carried its own would disagree with it twice a year."
                )


@dataclass(frozen=True)
class Pause:
    """A stated period during which nothing is billed and nothing is covered.

    Half-open: ``from_day`` is paused, ``until_day`` is not. Half-open because
    the alternative is an off-by-one that costs somebody a day of coverage or a
    day of money, and inclusive-inclusive ranges are where that off-by-one lives.
    """

    from_day: date
    until_day: date

    def __post_init__(self) -> None:
        for name, value in (("from_day", self.from_day), ("until_day", self.until_day)):
            if not isinstance(value, date):
                raise InvalidAgreement(f"pause.{name} is {value!r}; it is a date.")
        if self.until_day <= self.from_day:
            raise InvalidAgreement(
                f"a pause runs from {self.from_day} until {self.until_day}, which is "
                "not after it. The range is half-open: the first day is paused, the "
                "last is not."
            )

    def covers(self, day: date) -> bool:
        return self.from_day <= day < self.until_day


@dataclass(frozen=True)
class Mandate:
    """Who agreed, when, and to what. M6, and it is the reason a charge is allowed.

    **NO CARD NUMBER, NO BANK ACCOUNT NUMBER, AND NO TOKEN THAT COULD SUBSTITUTE
    FOR ONE.** Not in this record, not anywhere in this module's store, and not
    in its logs. The mandate records the AGREEMENT to be charged -- its terms --
    and nothing that could be used to charge. What can actually move money lives
    with the processor, in the module that talks to one, and that module is not
    this one.

    The fields are the terms a recurring off-session charge has to have been
    agreed to: what is charged, when, how often, how the amount is arrived at,
    and how the payer stops it. They are here in M1 with nothing reading them
    because they are STRUCTURAL -- retrofitting them later would rewrite the
    schema of every agreement already signed.
    """

    agreed_by: str
    agreed_at_iso: str
    #: What the payer agreed to, in the words they were shown.
    terms_shown: str
    #: "monthly, on the garage's billing day", in the words they were shown.
    frequency_shown: str
    #: How the figure is arrived at -- a fixed monthly price, plus stated fees.
    amount_basis_shown: str
    #: How the payer cancels, in the words they were shown.
    cancellation_shown: str

    def __post_init__(self) -> None:
        for name in (
            "agreed_by",
            "agreed_at_iso",
            "terms_shown",
            "frequency_shown",
            "amount_basis_shown",
            "cancellation_shown",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidAgreement(
                    f"mandate.{name} is empty. A mandate with a blank field is not a "
                    "record of what somebody agreed to; it is a record that a form "
                    "was submitted. Every field is what the payer was actually shown."
                )


@dataclass(frozen=True)
class Agreement:
    """One account's agreement, billed at one home garage.

    ``garage_id`` is the home garage -- the money key. ``covered_garage_ids``
    is every garage the agreement is good at, the home among them; see the
    module docstring. ``version`` is monotonic per agreement id and is what an entitlement answer
    cites, so a coverage decision can be traced to the exact document that made
    it. A price change writes a new version; it never edits one.
    """

    id: str
    version: int
    garage_id: str
    payer_id: str
    spots: int
    vehicles: tuple[str, ...]
    monthly_price_minor: int
    start_day: date
    #: Every garage this agreement covers, the home (``garage_id``) included.
    #: Required, non-empty, no duplicates, no default -- see the module docstring.
    covered_garage_ids: tuple[str, ...]
    mandate: Mandate | None = None
    status: Status = Status.ACTIVE
    cancelled_effective_day: date | None = None
    access_hours: AccessHours | None = None
    pauses: tuple[Pause, ...] = ()
    additional_fees: tuple[AdditionalFee, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise InvalidAgreement("an agreement needs a non-empty id.")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidAgreement(
                f"agreement {self.id!r} has version {self.version!r}. Versions start "
                "at 1 and count up; an entitlement answer cites one, so a coverage "
                "decision has to be traceable to the document that made it."
            )
        if isinstance(self.spots, bool) or not isinstance(self.spots, int) or self.spots < 1:
            raise InvalidAgreement(
                f"agreement {self.id!r} buys {self.spots!r} spots. It is a whole "
                "number of at least one -- this is the ENTITLEMENT, the number the "
                "platform counts against, and zero of them is a cancelled agreement "
                "rather than a live one."
            )
        as_non_negative_minor(self.monthly_price_minor, f"agreement[{self.id}].monthly_price_minor")

        if not isinstance(self.vehicles, tuple) or not self.vehicles:
            raise InvalidAgreement(
                f"agreement {self.id!r} lists no vehicles. The list size is "
                "independent of the spots bought -- twenty registered against ten "
                "bought is ordinary -- but an agreement covering no vehicle at all "
                "covers nothing, and would answer 'not covered' for every car "
                "forever without saying why."
            )
        for i, identity in enumerate(self.vehicles):
            if not isinstance(identity, str) or not identity.strip():
                raise InvalidAgreement(
                    f"agreement[{self.id}].vehicles[{i}] is {identity!r}. A vehicle "
                    "identity is a non-empty string, opaque to this module: it is "
                    "compared and never parsed, so nothing here assumes it came from "
                    "any particular reader or enrolment process."
                )
        if len(set(self.vehicles)) != len(self.vehicles):
            raise InvalidAgreement(
                f"agreement {self.id!r} lists the same vehicle identity twice. That "
                "is refused rather than de-duplicated: it usually means two different "
                "cars were enrolled under one identity, and silently collapsing them "
                "would cover whichever arrived first."
            )

        if not isinstance(self.start_day, date):
            raise InvalidAgreement(f"agreement {self.id!r} has start_day {self.start_day!r}.")

        if not isinstance(self.garage_id, str) or not self.garage_id.strip():
            raise InvalidAgreement(
                f"agreement {self.id!r} has garage_id {self.garage_id!r}. It is the HOME "
                "garage -- the one that bills this agreement -- and it is a non-empty "
                "string, opaque to this module."
            )
        if not isinstance(self.covered_garage_ids, tuple) or not self.covered_garage_ids:
            raise InvalidAgreement(
                f"agreement {self.id!r} lists no covered garages. The set is stated by "
                "listing it, the home garage included; there is no 'everywhere' and no "
                "default, because an implicit set cannot be audited and cannot be refused."
            )
        for i, covered in enumerate(self.covered_garage_ids):
            if not isinstance(covered, str) or not covered.strip():
                raise InvalidAgreement(
                    f"agreement[{self.id}].covered_garage_ids[{i}] is {covered!r}. A garage "
                    "id is a non-empty string, opaque to this module."
                )
        if len(set(self.covered_garage_ids)) != len(self.covered_garage_ids):
            raise InvalidAgreement(
                f"agreement {self.id!r} lists the same covered garage twice. Refused "
                "rather than de-duplicated, as a vehicle listed twice is: it usually "
                "means two garages were meant and one id was typed for both."
            )
        if self.garage_id not in self.covered_garage_ids:
            raise InvalidAgreement(
                f"agreement {self.id!r} is billed at garage {self.garage_id!r} but its "
                f"covered garages {list(self.covered_garage_ids)} do not include it. The "
                "home garage is where the invoice lives, and an agreement not covered "
                "where it is billed would be paid for and good nowhere it pays."
            )

        if self.status is Status.CANCELLED and self.cancelled_effective_day is None:
            raise InvalidAgreement(
                f"agreement {self.id!r} is cancelled with no effective day. "
                "Cancellation is use-it-or-lose-it and the paid period runs to its "
                "end, so WHEN it takes effect decides whether a car is covered "
                "tomorrow. It is not inferable from the status."
            )
        if self.status is Status.ACTIVE and self.cancelled_effective_day is not None:
            raise InvalidAgreement(
                f"agreement {self.id!r} is active and carries a cancellation date. "
                "One of the two is not what was meant."
            )

        for i, pause in enumerate(self.pauses):
            if not isinstance(pause, Pause):
                raise InvalidAgreement(f"agreement[{self.id}].pauses[{i}] is not a pause.")
        # Overlapping pauses are refused rather than merged: two overlapping
        # pauses usually mean one was entered twice, and merging them would make
        # a data-entry error invisible in exactly the field that decides whether
        # somebody is billed.
        ordered = sorted(self.pauses, key=lambda p: p.from_day)
        # strict=False deliberately: `ordered[1:]` is one shorter than `ordered`
        # by construction, which is what pairs each pause with its successor.
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.from_day < earlier.until_day:
                raise InvalidAgreement(
                    f"agreement {self.id!r} has overlapping pauses "
                    f"({earlier.from_day}..{earlier.until_day} and "
                    f"{later.from_day}..{later.until_day}). Refused rather than "
                    "merged: overlapping pauses usually mean one was entered twice."
                )

        labels = [f.label for f in self.additional_fees]
        if len(set(labels)) != len(labels):
            raise InvalidAgreement(
                f"agreement {self.id!r} has two additional fee lines with the same "
                "label. Every line appears on the invoice under its own label, and "
                "two identical labels are unreadable to whoever is paying."
            )

    # ------------------------------------------------------------------ queries

    def is_paused_on(self, day: date) -> bool:
        return any(pause.covers(day) for pause in self.pauses)

    def covers_garage(self, garage_id: str) -> bool:
        """Membership of the covered set -- the ACCESS question. Money asks
        ``garage_id == self.garage_id`` and nothing else; see invoice._same_garage."""
        return garage_id in self.covered_garage_ids

    def recurring_fees(self) -> tuple[AdditionalFee, ...]:
        return tuple(f for f in self.additional_fees if f.cadence is FeeCadence.RECURRING)

    def one_time_fees(self) -> tuple[AdditionalFee, ...]:
        return tuple(f for f in self.additional_fees if f.cadence is FeeCadence.ONE_TIME)


# --------------------------------------------------------------------------
# Loading a document
# --------------------------------------------------------------------------

#: Every key an agreement document may carry. An unknown key is REFUSED rather
#: than ignored: a typo in `monthly_price_minor` that is silently dropped bills
#: the account zero, and the operator's document looks correct to them.
KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "version",
        "garage_id",
        "covered_garage_ids",
        "payer_id",
        "spots",
        "vehicles",
        "monthly_price_minor",
        "start_day",
        "mandate",
        "status",
        "cancelled_effective_day",
        "access_hours",
        "pauses",
        "additional_fees",
    }
)


def _as_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise InvalidAgreement(f"{label} is {value!r}; it is an ISO date, 'YYYY-MM-DD'.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidAgreement(f"{label} is {value!r}, which is not an ISO date.") from exc


def _as_time(value: Any, label: str) -> time:
    if not isinstance(value, str):
        raise InvalidAgreement(f"{label} is {value!r}; it is a local time, 'HH:MM'.")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise InvalidAgreement(f"{label} is {value!r}, which is not a local time.") from exc


def load_agreement(document: dict[str, Any]) -> Agreement:
    """Read a document into an ``Agreement``, refusing anything it cannot type.

    **The money walk runs FIRST, over the whole document, before any field is
    read.** That ordering is the point: it covers the leaves this version does
    not read as well as the ones it does, so a float sitting in a field nobody
    consults today is refused today rather than on the round somebody starts
    consulting it.
    """
    if not isinstance(document, dict):
        raise InvalidAgreement(
            f"an agreement document is an object, not {type(document).__name__}."
        )

    refuse_non_integer_money(document, "agreement")

    unknown = sorted(set(document) - KNOWN_KEYS)
    if unknown:
        raise InvalidAgreement(
            f"the agreement carries keys this module does not know: {unknown}. They "
            "are refused rather than ignored -- a mistyped key that is silently "
            "dropped leaves the operator reading a document that says one thing "
            "while the module does another."
        )
    missing = sorted(
        {"id", "version", "garage_id", "covered_garage_ids", "payer_id", "spots",
         "vehicles", "monthly_price_minor", "start_day"} - set(document)
    )
    if missing:
        raise InvalidAgreement(
            f"the agreement is missing required fields: {missing}."
            + (
                " covered_garage_ids is the set of garages the agreement is good at, the "
                "home garage_id among them; it is listed, never defaulted to the home alone."
                if "covered_garage_ids" in missing
                else ""
            )
        )

    mandate_doc = document.get("mandate")
    mandate = None
    if mandate_doc is not None:
        if not isinstance(mandate_doc, dict):
            raise InvalidAgreement("agreement.mandate is an object, or absent.")
        try:
            mandate = Mandate(**mandate_doc)
        except TypeError as exc:
            raise InvalidAgreement(f"agreement.mandate: {exc}") from exc

    hours_doc = document.get("access_hours")
    access_hours = None
    if hours_doc is not None:
        if not isinstance(hours_doc, dict) or set(hours_doc) != {"entry_from", "exit_by"}:
            raise InvalidAgreement(
                "agreement.access_hours states entry_from and exit_by, both of them. "
                "Absent means all hours; one half alone would be silently unbounded "
                "on the other."
            )
        access_hours = AccessHours(
            entry_from=_as_time(hours_doc["entry_from"], "access_hours.entry_from"),
            exit_by=_as_time(hours_doc["exit_by"], "access_hours.exit_by"),
        )

    pauses = []
    for i, pause_doc in enumerate(document.get("pauses", ()) or ()):
        if not isinstance(pause_doc, dict) or set(pause_doc) != {"from_day", "until_day"}:
            raise InvalidAgreement(f"agreement.pauses[{i}] states from_day and until_day.")
        pauses.append(
            Pause(
                from_day=_as_date(pause_doc["from_day"], f"pauses[{i}].from_day"),
                until_day=_as_date(pause_doc["until_day"], f"pauses[{i}].until_day"),
            )
        )

    fees = []
    for i, fee_doc in enumerate(document.get("additional_fees", ()) or ()):
        if not isinstance(fee_doc, dict):
            raise InvalidAgreement(f"agreement.additional_fees[{i}] is an object.")
        expected = {"label", "amount_minor", "cadence", "effective_from"}
        if set(fee_doc) != expected:
            raise InvalidAgreement(
                f"agreement.additional_fees[{i}] states exactly {sorted(expected)}."
            )
        try:
            cadence = FeeCadence(fee_doc["cadence"])
        except ValueError as exc:
            raise InvalidAgreement(
                f"additional_fees[{i}].cadence is {fee_doc['cadence']!r}; it is one of "
                f"{[c.value for c in FeeCadence]}."
            ) from exc
        fees.append(
            AdditionalFee(
                label=fee_doc["label"],
                amount_minor=fee_doc["amount_minor"],
                cadence=cadence,
                effective_from=_as_date(
                    fee_doc["effective_from"], f"additional_fees[{i}].effective_from"
                ),
            )
        )

    try:
        status = Status(document.get("status", "active"))
    except ValueError as exc:
        raise InvalidAgreement(
            f"agreement.status is {document.get('status')!r}; it is one of "
            f"{[s.value for s in Status]}."
        ) from exc

    cancelled_day = document.get("cancelled_effective_day")
    return Agreement(
        id=document["id"],
        version=document["version"],
        garage_id=document["garage_id"],
        payer_id=document["payer_id"],
        spots=document["spots"],
        vehicles=(
            tuple(document["vehicles"])
            if isinstance(document["vehicles"], list)
            else document["vehicles"]
        ),
        monthly_price_minor=document["monthly_price_minor"],
        start_day=_as_date(document["start_day"], "agreement.start_day"),
        covered_garage_ids=(
            tuple(document["covered_garage_ids"])
            if isinstance(document["covered_garage_ids"], list)
            else document["covered_garage_ids"]
        ),
        mandate=mandate,
        status=status,
        cancelled_effective_day=(
            _as_date(cancelled_day, "agreement.cancelled_effective_day")
            if cancelled_day is not None
            else None
        ),
        access_hours=access_hours,
        pauses=tuple(pauses),
        additional_fees=tuple(fees),
    )


def load_agreement_file(path: str) -> Agreement:
    with open(path, encoding="utf-8") as handle:
        return load_agreement(json.load(handle))


__all__ = [
    "AccessHours",
    "AdditionalFee",
    "Agreement",
    "FeeCadence",
    "InvalidAgreement",
    "KNOWN_KEYS",
    "Mandate",
    "Pause",
    "Status",
    "load_agreement",
    "load_agreement_file",
]