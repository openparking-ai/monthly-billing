"""G11 -- the vehicle list is independent of the spots bought.

His specification, in his words: "they can register 20 cars and collect only 10
cars and they can only park 10 cars at the same moment." Twenty registered
against ten bought is a legal agreement, and there is deliberately no constraint
tying the two -- in the dataclass or in the schema.

**AND THE MODULE STATES THE ENTITLEMENT RATHER THAN COUNTING.** Counting how many
of an account's cars are inside needs live session state, and a module that
needed live session state would have stopped being standalone. So the answer
carries N and something else decides the (N+1)th car is a transient.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from fixtures import month_end_garage, simple_agreement, sometime_on
from monthly_billing.entitlement import is_covered

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "0001_tenants_agreements_and_rls.sql"
)


@pytest.mark.guarantee("G11")
def test_twenty_vehicles_against_ten_spots_loads():
    agreement = simple_agreement()
    assert len(agreement.vehicles) == 20
    assert agreement.spots == 10


@pytest.mark.guarantee("G11")
def test_one_vehicle_against_ten_spots_also_loads():
    """The other end of the axis. A household with one car and ten spots is odd
    and legal, and refusing it would be the module having an opinion."""
    agreement = simple_agreement(vehicles=("ONLY-CAR",), spots=10)
    assert len(agreement.vehicles) < agreement.spots


@pytest.mark.guarantee("G11")
def test_every_listed_vehicle_gets_the_same_entitlement():
    """All twenty are covered; the entitlement each is told is ten."""
    garage = month_end_garage()
    agreement = simple_agreement(start_day=date(2026, 3, 1))
    at = sometime_on(date(2026, 4, 1), garage.timezone)

    for identity in agreement.vehicles:
        answer = is_covered(
            garage=garage, agreements=(agreement,), vehicle_identity=identity, at=at
        )
        assert answer.covered is True
        assert answer.entitlement == 10


@pytest.mark.guarantee("G11")
def test_the_schema_has_no_constraint_tying_the_list_to_the_spots():
    """An absence claim, with its query stated and a positive control beside it.

    QUERY: every CHECK constraint in the migration mentioning both `spots` and
    a vehicle table. CONTROL: the same search for a constraint that IS there.
    """
    sql = MIGRATION.read_text()

    control = re.findall(r"CONSTRAINT\s+agreements_cancellation_has_a_date", sql)
    assert control, (
        "the positive control did not fire, so this file is not being searched "
        "the way this test believes it is"
    )

    checks = re.findall(r"CHECK\s*\([^;]*?\)", sql, flags=re.DOTALL)
    tying = [c for c in checks if "spots" in c and "vehicle" in c]
    assert tying == [], f"a constraint ties the vehicle list to the spots: {tying}"


@pytest.mark.guarantee("G11")
def test_a_duplicate_identity_is_refused_rather_than_de_duplicated():
    """It usually means two different cars were enrolled under one identity, and
    collapsing them silently would cover whichever arrived first."""
    with pytest.raises(Exception, match="twice"):
        simple_agreement(vehicles=("SAME", "SAME"))


@pytest.mark.guarantee("G11")
def test_an_agreement_covering_no_vehicle_is_refused():
    """It would answer 'not covered' for every car forever without saying why."""
    with pytest.raises(Exception, match="lists no vehicles"):
        simple_agreement(vehicles=())
