"""G57 -- a garage's tax sets come in through one door, and the engine judges them.

What the engine refuses is refused in its words and nothing is stored; what it
accepts is stored as it read it; the garage's whole list is loaded again before
the commit; a second set at one instant is refused by name; the table is
append-only by grant; and a card-shaped value inside a rule is refused at the
store. Controls plant the engine's judgement away, the write-back proof away,
and the chokepoint's walk into structured values away.
"""

from __future__ import annotations

from datetime import datetime

import psycopg
import pytest

from monthly_billing.findings import REFUSAL_TAX_SETS_INVALID, Refused
from monthly_billing.sensitive import luhn_ok
from monthly_billing.store.postgres import tenant
from monthly_billing.store.records import load_garage
from monthly_billing.store.tax_sets import state_tax_sets
from monthly_billing.store.writes import InstrumentRefusedAtTheStore
from store_harness import needs_postgres, query, seed
from tax_harness import GARAGE, PARKING, SALES

pytestmark = needs_postgres


def _state(app, tenant_id, sets):
    with tenant(app, tenant_id) as cursor:
        try:
            whole = state_tax_sets(
                cursor, tenant_id, load_garage(cursor, GARAGE.id).uuid, sets, stated_by="owner"
            )
        except BaseException:
            app.rollback()
            raise
    app.commit()
    return whole


def _stored(app, tenant_id):
    return query(app, tenant_id, "SELECT effective_from, rules FROM garage_tax_sets")


@pytest.mark.guarantee("G57")
def test_what_the_engine_refuses_is_refused_in_its_words_and_nothing_is_stored(app, tenant_id):
    seed(app, tenant_id, GARAGE, (), tax_sets=None)
    with pytest.raises(Refused) as refused:
        _state(app, tenant_id, [{"effective_from": "2026-01-01T00:00:00Z",
                                 "rules": [{**SALES, "base": "subtotal"}]}])
    assert refused.value.code == REFUSAL_TAX_SETS_INVALID
    assert "base" in str(refused.value)
    assert _stored(app, tenant_id) == []


@pytest.mark.guarantee("G57")
def test_what_is_stored_is_what_the_engine_read(app, tenant_id):
    seed(app, tenant_id, GARAGE, (), tax_sets=None)
    whole = _state(app, tenant_id, [{"effective_from": "2026-01-01T09:00:00+02:00",
                                     "rules": [PARKING, SALES]}])
    ((effective_from, rules),) = _stored(app, tenant_id)
    assert effective_from == datetime.fromisoformat("2026-01-01T07:00:00+00:00")
    assert [r["id"] for r in rules] == ["sales", "parking"], "not in the stated sequence"
    assert len(whole) == 1 and whole[0]["rules"] == rules


@pytest.mark.guarantee("G57")
def test_a_second_set_at_the_same_instant_is_refused_by_name(app, tenant_id):
    seed(app, tenant_id, GARAGE, (), tax_sets=None)
    _state(app, tenant_id, [{"effective_from": "2026-01-01T00:00:00Z", "rules": []}])
    with pytest.raises(Refused) as refused:
        _state(app, tenant_id, [{"effective_from": "2025-12-31T19:00:00-05:00",
                                 "rules": [SALES]}])
    assert refused.value.code == REFUSAL_TAX_SETS_INVALID
    assert len(_stored(app, tenant_id)) == 1


@pytest.mark.guarantee("G57")
def test_a_statement_after_which_the_whole_list_would_not_load_is_refused(
    app, owner, tenant_id
):
    """A row past the module that the engine would not load (a rule missing its
    fields). A new, valid set must then be refused: the list is loaded whole,
    so storing it would leave every later invoice unpriceable."""
    seeded = seed(app, tenant_id, GARAGE, (), tax_sets=None)
    with owner.cursor() as cursor:
        cursor.execute(
            "INSERT INTO garage_tax_sets (tenant_id, garage_id, effective_from, rules, "
            "stated_by) VALUES (%s, %s, '2025-01-01T00:00:00Z', '[{\"id\": \"x\"}]', 'past')",
            (tenant_id, seeded.garage_uuid),
        )
    with pytest.raises(Refused) as refused:
        _state(app, tenant_id, [{"effective_from": "2026-01-01T00:00:00Z", "rules": [SALES]}])
    assert refused.value.code == REFUSAL_TAX_SETS_INVALID
    assert len(_stored(app, tenant_id)) == 1, "the valid set was stored beside the bad one"


@pytest.mark.guarantee("G57")
@pytest.mark.parametrize("statement", [
    "UPDATE garage_tax_sets SET rules = '[]'",
    "DELETE FROM garage_tax_sets",
])
def test_the_sets_are_append_only_by_grant(app, tenant_id, statement):
    seed(app, tenant_id, GARAGE, (), tax_sets=None)
    _state(app, tenant_id, [{"effective_from": "2026-01-01T00:00:00Z", "rules": [SALES]}])
    with tenant(app, tenant_id) as cursor, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cursor.execute(statement)
    app.rollback()


def _card_shaped() -> str:
    body = "4" + "1" * 14
    return next(body + d for d in "0123456789" if luhn_ok(body + d))


@pytest.mark.guarantee("G57")
def test_a_card_shaped_value_inside_a_rule_is_refused_at_the_store(app, tenant_id):
    seed(app, tenant_id, GARAGE, (), tax_sets=None)
    with pytest.raises(InstrumentRefusedAtTheStore) as refused:
        _state(app, tenant_id, [{"effective_from": "2026-01-01T00:00:00Z",
                                 "rules": [{**SALES, "label": f"Tax {_card_shaped()}"}]}])
    assert _card_shaped() not in str(refused.value)
    assert _stored(app, tenant_id) == []
