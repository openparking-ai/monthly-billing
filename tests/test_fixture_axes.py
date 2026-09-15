"""The fixtures are only fixtures if they hold the properties they claim.

Run as a test in their own right, before any number taken against them counts.
A fixture whose DST zone does not shift, or whose two identity rules agree, reads
as coverage while sampling one point on the axis that decides.
"""

from __future__ import annotations

import pytest

from fixtures import (
    assert_covered_garages_differ_on_every_axis,
    assert_currencies_span_the_exponents,
    assert_fixed_zone_really_does_not_shift,
    assert_identity_rules_disagree,
    assert_shifting_zone_really_shifts,
    assert_the_vehicle_list_exceeds_the_spots,
)


@pytest.mark.guarantee("G14")
@pytest.mark.parametrize(
    "control",
    [
        assert_shifting_zone_really_shifts,
        assert_fixed_zone_really_does_not_shift,
        assert_identity_rules_disagree,
        assert_currencies_span_the_exponents,
        assert_the_vehicle_list_exceeds_the_spots,
        assert_covered_garages_differ_on_every_axis,
    ],
    ids=lambda f: f.__name__,
)
def test_the_fixture_holds_the_property_it_claims(control):
    control()
