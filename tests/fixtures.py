"""Fixtures, and the control that each one has the property it claims.

**A FIXTURE IS PART OF THE MEASUREMENT.** A garage fixture whose timezone never
observes daylight saving cannot exercise the boundary arithmetic, and a number
taken against it reads as evidence while measuring nothing. So every fixture here
carries an assertion that it holds the property it exists to represent, and
``test_fixture_axes.py`` runs those assertions as a test in their own right.

**AND A FIXTURE MUST VARY EVERY AXIS THE DECISION BRANCHES ON.** The axes this
module's decisions actually turn on, read from the code rather than imagined:

* the billing day option -- three of them, and month-end behaves differently
* the timezone -- one that shifts and one that does not
* the identity rule -- both, because they disagree about "ABC 123"
* the period -- one containing a spring-forward, one a fall-back, one neither
* the agreement -- with and without access hours, a pause, a mandate, fees
* the covered set -- one garage, and one agreement covering TWO garages that
  differ on every axis a door decides by: timezone, currency and identity rule.
  A multi-garage fixture whose garages agreed on those would let a test look
  like it crossed the axis while sampling one point on it.
"""

from __future__ import annotations

from datetime import date, datetime, time

from monthly_billing.agreement import (
    AccessHours,
    AdditionalFee,
    Agreement,
    FeeCadence,
    Mandate,
    Pause,
)
from monthly_billing.garage import BillingDay, Garage, IdentityRule
from monthly_billing.localday import day_start, hours_between, zone

#: A zone that observes daylight saving, and one that does not. The second is not
#: decoration: it is the control proving the first one's DST assertions are about
#: the zone rather than about the arithmetic being wrong everywhere.
SHIFTING_ZONE = "America/Denver"
FIXED_ZONE = "America/Phoenix"

#: 2026 US transitions. Named so a test asserts against a date rather than a
#: comment, and so the fixture control can prove they really are transitions.
SPRING_FORWARD_2026 = date(2026, 3, 8)
FALL_BACK_2026 = date(2026, 11, 1)


def month_end_garage(timezone: str = SHIFTING_ZONE, grace_days: int = 5) -> Garage:
    return Garage(
        id="garage-month-end",
        timezone=timezone,
        currency="USD",
        billing_day=BillingDay.LAST_DAY_OF_MONTH,
        payment_grace_days=grace_days,
        identity_rule=IdentityRule.FOLDED_ALPHANUMERIC,
    )


def tenth_of_month_garage(timezone: str = SHIFTING_ZONE) -> Garage:
    return Garage(
        id="garage-tenth",
        timezone=timezone,
        currency="USD",
        billing_day=BillingDay.NTH_DAY_OF_MONTH,
        billing_day_of_month=10,
        payment_grace_days=0,
        identity_rule=IdentityRule.EXACT,
    )


def first_of_month_garage(timezone: str = FIXED_ZONE) -> Garage:
    return Garage(
        id="garage-first",
        timezone=timezone,
        currency="JPY",  # zero-decimal, so rendering is exercised on both sides
        billing_day=BillingDay.FIRST_DAY_OF_MONTH,
        payment_grace_days=10,
        identity_rule=IdentityRule.EXACT,
    )


def mandate() -> Mandate:
    return Mandate(
        agreed_by="the account holder",
        agreed_at_iso="2026-02-20T14:03:00-07:00",
        terms_shown="A recurring monthly charge for monthly parking.",
        frequency_shown="Monthly, on the garage's billing day.",
        amount_basis_shown="The stated monthly price plus any stated additional fees.",
        cancellation_shown="Cancel in writing; the paid period runs to its end.",
    )


def simple_agreement(
    *,
    garage_id: str = "garage-month-end",
    start_day: date = date(2026, 3, 10),
    spots: int = 10,
    with_mandate: bool = True,
    **overrides: object,
) -> Agreement:
    """Twenty vehicles against ten spots -- the shape the specification names."""
    fields: dict[str, object] = {
        "id": "ag-0001",
        "version": 1,
        "garage_id": garage_id,
        "covered_garage_ids": (garage_id,),
        "payer_id": "payer-acme",
        "spots": spots,
        "vehicles": tuple(f"CAR{n:03d}" for n in range(20)),
        "monthly_price_minor": 12000,
        "start_day": start_day,
        "mandate": mandate() if with_mandate else None,
    }
    fields.update(overrides)
    return Agreement(**fields)  # type: ignore[arg-type]


#: The home and the second garage of the multi-garage fixture, by construction
#: the two garage fixtures that disagree on timezone, currency AND identity rule.
MULTI_HOME = month_end_garage
MULTI_OTHER = first_of_month_garage


def multi_garage_agreement(**overrides: object) -> Agreement:
    """One agreement billed at the month-end garage (Denver, USD, folded) and
    covering the first-of-month garage too (Phoenix, JPY, exact). Every door
    axis differs between the two, and ``assert_covered_garages_differ_on_every_axis``
    is the control that they do."""
    fields: dict[str, object] = {
        "garage_id": MULTI_HOME().id,
        "covered_garage_ids": (MULTI_HOME().id, MULTI_OTHER().id),
    }
    fields.update(overrides)
    return simple_agreement(**fields)  # type: ignore[arg-type]


def agreement_with_everything(garage_id: str = "garage-month-end") -> Agreement:
    return simple_agreement(
        garage_id=garage_id,
        access_hours=AccessHours(entry_from=time(6, 0), exit_by=time(20, 0)),
        pauses=(Pause(from_day=date(2026, 7, 1), until_day=date(2026, 8, 1)),),
        additional_fees=(
            AdditionalFee(
                label="Reserved space 12",
                amount_minor=2500,
                cadence=FeeCadence.RECURRING,
                effective_from=date(2026, 3, 10),
            ),
            AdditionalFee(
                label="Access card replacement",
                amount_minor=1500,
                cadence=FeeCadence.ONE_TIME,
                effective_from=date(2026, 3, 12),
            ),
        ),
    )


def agreement_document(**overrides: object) -> dict:
    """The JSON shape, for the loader's leaf walk to be probed against.

    Deliberately carries a nested list and a nested object with a leaf the module
    does NOT read, because the leaves this version ignores are exactly the ones
    the money walk exists for.
    """
    document: dict = {
        "id": "ag-0001",
        "version": 1,
        "garage_id": "garage-month-end",
        "covered_garage_ids": ["garage-month-end"],
        "payer_id": "payer-acme",
        "spots": 10,
        "vehicles": ["CAR000", "CAR001"],
        "monthly_price_minor": 12000,
        "start_day": "2026-03-10",
        "pauses": [{"from_day": "2026-07-01", "until_day": "2026-08-01"}],
        "additional_fees": [
            {
                "label": "Reserved space 12",
                "amount_minor": 2500,
                "cadence": "recurring",
                "effective_from": "2026-03-10",
            }
        ],
    }
    document.update(overrides)
    return document


# --------------------------------------------------------------------------
# The fixture controls. Each asserts the property its fixture claims.
# --------------------------------------------------------------------------


def assert_shifting_zone_really_shifts() -> None:
    """The DST fixture is only a fixture if the zone actually moves.

    Asserted in real hours, through UTC, because same-zone subtraction in Python
    is wall-clock arithmetic and would report 24 for both -- which is the exact
    defect the guarantee this fixture serves exists to catch.
    """
    tz = zone(SHIFTING_ZONE)
    spring = hours_between(
        day_start(SPRING_FORWARD_2026, tz), day_start(date(2026, 3, 9), tz)
    )
    fall = hours_between(day_start(FALL_BACK_2026, tz), day_start(date(2026, 11, 2), tz))
    assert spring == 23.0, (
        f"{SHIFTING_ZONE} was chosen because it observes daylight saving, and "
        f"{SPRING_FORWARD_2026} came back {spring} hours long instead of 23. Either "
        "the zone data changed or the measurement is wrong, and until that is settled "
        "no DST number taken against this fixture means anything."
    )
    assert fall == 25.0, f"{FALL_BACK_2026} came back {fall} hours long instead of 25."


def assert_fixed_zone_really_does_not_shift() -> None:
    """The negative control for the one above.

    Without it, an arithmetic bug that made every day 23 or 25 hours long would
    satisfy the assertions above and read as a working DST measurement.
    """
    tz = zone(FIXED_ZONE)
    for day in (SPRING_FORWARD_2026, FALL_BACK_2026):
        hours = hours_between(day_start(day, tz), day_start(day.replace(day=day.day + 1), tz))
        assert hours == 24.0, (
            f"{FIXED_ZONE} does not observe daylight saving, so {day} must be 24 "
            f"hours; it came back {hours}. If this fires alongside the assertions "
            "above, the arithmetic is wrong everywhere rather than the zone being "
            "right anywhere."
        )


def assert_identity_rules_disagree() -> None:
    """The two identity rules are only two rules if they answer differently.

    A fixture pair that agreed on every input would let a test look like it
    covered both while sampling one point on the only axis that decides.
    """
    folded = month_end_garage()
    exact = tenth_of_month_garage()
    assert folded.identities_match("ABC-123", "abc 123")
    assert not exact.identities_match("ABC-123", "abc 123")
    assert exact.identities_match("ABC-123", "ABC-123")


def assert_currencies_span_the_exponents() -> None:
    """The garage fixtures cover a two-decimal and a zero-decimal currency.

    Without a zero-decimal one, every rendering assertion in the suite is made
    against the one exponent where dividing by 100 happens to be right.
    """
    exponents = {month_end_garage().currency, first_of_month_garage().currency}
    assert exponents == {"USD", "JPY"}


def assert_the_vehicle_list_exceeds_the_spots() -> None:
    """The fixture represents the case the specification names, not a tidy one."""
    agreement = simple_agreement()
    assert len(agreement.vehicles) == 20
    assert agreement.spots == 10
    assert len(agreement.vehicles) > agreement.spots


def assert_covered_garages_differ_on_every_axis() -> None:
    """The multi-garage fixture is only a multi-garage fixture if its two garages
    disagree on every axis a door decides by -- timezone, currency and identity
    rule -- and if the agreement really is homed at one and covers the other.

    A fixture pair that agreed on any of the three would let the coverage,
    grace and registration tests cross that axis in name while sampling one
    point on it.
    """
    home, other = MULTI_HOME(), MULTI_OTHER()
    agreement = multi_garage_agreement()
    assert agreement.garage_id == home.id
    assert set(agreement.covered_garage_ids) == {home.id, other.id}
    assert len(agreement.covered_garage_ids) == 2
    assert home.timezone != other.timezone, "the two covered garages share a timezone"
    assert home.currency != other.currency, "the two covered garages share a currency"
    assert home.identity_rule is not other.identity_rule, (
        "the two covered garages share an identity rule"
    )
    # The rules disagree on THIS fixture's plates, not merely in the abstract:
    # the same identity normalises to two different strings.
    plate = agreement.vehicles[0]
    assert home.normalise_identity(plate + " ") != other.normalise_identity(plate + " ")


def sometime_on(day: date, timezone: str, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=zone(timezone))
