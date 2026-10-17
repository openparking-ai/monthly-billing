"""G62 -- the folded identity rule reads O as 0 and I as 1.

The plate reader's alphabet has no I and no O, so it reads a plate registered
as GO4IT as G041T. ``folded_alphanumeric`` ignored only case and punctuation:
that holder never matched, and paid as a transient on every visit. The rule now
reads o as 0 and i as 1 after case folding, on both sides of the comparison,
and nothing else -- ``exact`` is untouched. Migration 0007 re-keys every
identity already stored in the folded form, refuses by name a pair the fold
would make one, and refuses a role that cannot see every row.

THE VECTORS are the shared ones: the same pairs hold for every consumer that
compares a registered plate with a read (the lane, garage-pass, the desk).

Controls, each with a plant in ``scripts/fail_controls.py``: the fold removed
from the rule; the migration's collision check removed (the colliding file
then fails on the table's UNIQUE instead of by name); the migration's re-key
removed (a stored GO4IT never matches the read again).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import first_of_month_garage, month_end_garage, multi_garage_agreement
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.garage import IdentityRule
from monthly_billing.store.postgres import set_tenant, tenant
from monthly_billing.store.records import (
    register_from_outside,
    store_agreement,
    store_garage,
    store_payer,
)
from store_harness import (
    BLIND_OWNER,
    DSN,
    app_connection,
    apply_migration,
    as_blind_owner,
    cluster_lock,
    migrate,
    needs_postgres,
    new_tenant,
)
from test_g48_the_register_can_be_read_by_garage import outside

FOLDED = month_end_garage()  # Denver, folded_alphanumeric
EXACT = first_of_month_garage()  # Phoenix, exact
NOW = datetime(2026, 5, 10, 9, 0, tzinfo=ZoneInfo(FOLDED.timezone))

#: (registered, read, the same car?) -- the shared vectors.
VECTORS = (
    ("GO4IT", "G041T", True),
    ("go4it", "G041T", True),
    ("GO-4 IT", "G041T", True),
    ("OI-IO", "0110", True),
    ("ABC-123", "abc 123", True),
    ("GO4IT", "GQ4IT", False),  # only O and I fold: Q stays Q
    ("L1", "11", False),
    ("B8", "88", False),
    ("S5", "55", False),
    ("Z2", "22", False),
)


def _store_backed(test):
    for mark in needs_postgres:
        test = mark(test)
    return test


@pytest.mark.guarantee("G62")
@pytest.mark.parametrize(("registered", "read", "same"), VECTORS)
def test_the_folded_rule_matches_the_shared_vectors(registered, read, same):
    assert FOLDED.identity_rule is IdentityRule.FOLDED_ALPHANUMERIC
    assert FOLDED.identities_match(registered, read) is same
    assert FOLDED.identities_match(read, registered) is same
    # Still idempotent: a stored form handed back to the door (a release by
    # stored form) normalises to itself.
    for text in (registered, read):
        once = FOLDED.normalise_identity(text)
        assert FOLDED.normalise_identity(once) == once


@pytest.mark.guarantee("G62")
def test_the_stored_form_holds_no_o_and_no_i_and_exact_is_untouched():
    assert FOLDED.normalise_identity("GO4IT") == FOLDED.normalise_identity("G041T") == "g041t"
    assert EXACT.identity_rule is IdentityRule.EXACT
    assert EXACT.normalise_identity("GO4IT") == "GO4IT"
    assert not EXACT.identities_match("GO4IT", "G041T")


@pytest.mark.guarantee("G62")
@_store_backed
def test_a_registered_go4it_is_covered_when_the_reader_says_g041t(app, tenant_id):
    """End to end through the store: the outside registrar puts GO4IT on at
    both garages; the folded garage stores g041t and answers the read G041T
    covered; the exact garage stores GO4IT as typed."""
    from store_harness import seed_garages

    seed_garages(app, tenant_id, (FOLDED, EXACT), (outside(),), now=NOW)
    with tenant(app, tenant_id) as cursor:
        stored = register_from_outside(cursor, tenant_id, "ag-outside", "GO4IT", now=NOW)
    app.commit()
    assert {(e.garage_id, e.identity_normalised) for e in stored} == {
        (FOLDED.id, "g041t"), (EXACT.id, "GO4IT")}
    answer = covered_from_store(app, tenant_id, FOLDED.id, "G041T", NOW)
    assert answer.covered, answer.reason


# ---------------------------------------------------------------------------
# Migration 0007, against rows stored in the old form.
# ---------------------------------------------------------------------------


@pytest.fixture()
def schema_0006():
    """A fresh schema built through 0006, per test -- 0007 is applied by the test."""
    owner = migrate(DSN, through="0006")
    yield owner
    owner.close()


def _seed_old_forms(owner, tenant_id, *, registrations, listed=()):
    """Both garages, one payer and one self-written agreement covering both,
    stored through the module; then rows in the OLD folded form, raw:
    ``registrations`` as (garage, form, agreement id) and ``listed`` as
    (identity, old normalised form) on the agreement's version."""
    app = app_connection(DSN)
    try:
        with app.cursor() as cursor:
            set_tenant(cursor, tenant_id)
            garages = {g.id: store_garage(cursor, tenant_id, g) for g in (FOLDED, EXACT)}
            payer = store_payer(cursor, tenant_id, "payer-acme", "Acme")
            agreement = multi_garage_agreement(start_day=date(2026, 1, 5), vehicles=("CAR001",))
            version = store_agreement(cursor, tenant_id, FOLDED, garages[FOLDED.id], payer,
                                      agreement, now=NOW)
            for garage_id, form, agreement_id in registrations:
                cursor.execute(
                    "INSERT INTO vehicle_registrations (tenant_id, garage_id, identity_normalised, "
                    "agreement_external_id, registered_at) VALUES (%s, %s, %s, %s, %s)",
                    (tenant_id, garages[garage_id], form, agreement_id, NOW),
                )
            for identity, form in listed:
                cursor.execute(
                    "INSERT INTO agreement_vehicles (tenant_id, agreement_id, identity, "
                    "identity_normalised) VALUES (%s, %s, %s, %s)",
                    (tenant_id, version, identity, form),
                )
        app.commit()
    finally:
        app.close()


def _rows(owner, statement):
    with owner.cursor() as cursor:
        cursor.execute(statement)
        return sorted(cursor.fetchall())


REGISTRATIONS = (
    "SELECT g.external_id, r.identity_normalised, r.agreement_external_id "
    "FROM vehicle_registrations r JOIN garages g ON g.id = r.garage_id"
)
LISTED = "SELECT identity, identity_normalised FROM agreement_vehicles"


@pytest.mark.guarantee("G62")
@_store_backed
def test_0007_rekeys_the_folded_forms_and_leaves_exact_ones(schema_0006):
    owner = schema_0006
    tenant_id = new_tenant(owner)
    _seed_old_forms(
        owner, tenant_id,
        registrations=((FOLDED.id, "go4it", "ag-0001"), (EXACT.id, "GO4IT", "ag-0001")),
        listed=(("GO4IT", "go4it"),),
    )
    apply_migration(owner, "0007")
    assert _rows(owner, REGISTRATIONS) == sorted([
        (FOLDED.id, "car001", "ag-0001"), (EXACT.id, "CAR001", "ag-0001"),
        (FOLDED.id, "g041t", "ag-0001"), (EXACT.id, "GO4IT", "ag-0001"),
    ])
    assert _rows(owner, LISTED) == [("CAR001", "car001"), ("GO4IT", "g041t")]
    # And the module answers the reader's G041T at the folded garage.
    app = app_connection(DSN)
    try:
        assert covered_from_store(app, tenant_id, FOLDED.id, "G041T", NOW).covered
    finally:
        app.close()


@pytest.mark.guarantee("G62")
@_store_backed
def test_0007_refuses_a_registration_collision_by_name_and_writes_nothing(schema_0006):
    """GO4IT and G04IT at one folded garage, on two agreements: one form once
    the fold applies. Refused by name, naming the garage and both forms, with
    every row as it was; once one is released the file applies."""
    import psycopg

    owner = schema_0006
    tenant_id = new_tenant(owner)
    _seed_old_forms(
        owner, tenant_id,
        registrations=((FOLDED.id, "go4it", "ag-0001"), (FOLDED.id, "g04it", "ag-other")),
    )
    before = _rows(owner, REGISTRATIONS)
    with pytest.raises(psycopg.errors.RaiseException) as refused:
        apply_migration(owner, "0007")
    message = str(refused.value)
    assert "vehicle_registrations" in message and f"garage {FOLDED.id}" in message
    assert "g04it (ag-other)" in message and "go4it (ag-0001)" in message
    assert "Nothing was written" in message
    assert _rows(owner, REGISTRATIONS) == before
    with owner.cursor() as cursor:
        cursor.execute("DELETE FROM vehicle_registrations WHERE agreement_external_id = 'ag-other'")
    apply_migration(owner, "0007")
    assert (FOLDED.id, "g041t", "ag-0001") in _rows(owner, REGISTRATIONS)


@pytest.mark.guarantee("G62")
@_store_backed
def test_0007_refuses_a_version_listing_two_plates_the_fold_makes_one(schema_0006):
    import psycopg

    owner = schema_0006
    tenant_id = new_tenant(owner)
    _seed_old_forms(owner, tenant_id, registrations=(),
                    listed=(("GO4IT", "go4it"), ("G04IT", "g04it")))
    before = _rows(owner, LISTED)
    with pytest.raises(psycopg.errors.RaiseException) as refused:
        apply_migration(owner, "0007")
    message = str(refused.value)
    assert "agreement_vehicles" in message and "ag-0001 version 1" in message
    assert "G04IT, GO4IT" in message and "Nothing was written" in message
    assert _rows(owner, LISTED) == before


@pytest.mark.guarantee("G62")
@_store_backed
def test_0007_refuses_a_role_that_cannot_see_every_row(schema_0006):
    import psycopg

    from store_harness import migration_path

    owner = schema_0006
    tenant_id = new_tenant(owner)
    _seed_old_forms(owner, tenant_id, registrations=((FOLDED.id, "go4it", "ag-0001"),))
    with as_blind_owner(owner) as blind:
        with cluster_lock(DSN), pytest.raises(psycopg.errors.RaiseException) as refused:
            try:
                blind.execute(migration_path("0007").read_text())
            except Exception:
                blind.execute("ROLLBACK")
                raise
    message = str(refused.value)
    assert f"running as role {BLIND_OWNER}" in message and "Nothing was written" in message
    assert (FOLDED.id, "go4it", "ag-0001") in _rows(owner, REGISTRATIONS)
