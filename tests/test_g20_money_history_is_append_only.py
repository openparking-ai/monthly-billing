"""G20 -- payments, reversals and charge attempts are append-only BY GRANT, and
the store's instrument guard scans them like every other table (G9).

**THE CONTROL TRIES THE THING THE GRANT FORBIDS.** An UPDATE and a DELETE on
each of the three tables, as the application role, and the database must
refuse both. The fail-control plants a migration that grants UPDATE and DELETE
and requires these to go red -- because "append-only" written in a comment
above a table that grants everything is exactly the shape of a promise this
project does not accept.

**AND THE G9 GUARD RUNS ON THE NEW ROWS WITHOUT BEING TOLD ABOUT THEM.** The
chokepoint scans every column of every record from the record's own keys, so
``payments.processor_reference`` was scanned the day the column existed. The
test plants a card-shaped value there and requires the refusal; the control
plants a raw INSERT that bypasses the chokepoint and requires the test to go
red.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import attempt_charge
from monthly_billing.payment import ChargeResult, Outcome
from monthly_billing.payments import PaymentMethod, ReversalReason, record_payment, record_reversal
from monthly_billing.sensitive import luhn_ok
from monthly_billing.store import InstrumentRefusedAtTheStore
from monthly_billing.store.postgres import tenant
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)
MAY_8 = datetime(2026, 5, 8, 10, 0, tzinfo=TZ)

APPEND_ONLY = ("payments", "payment_reversals", "charge_attempts")


def card_shaped() -> str:
    """Built from the checksum, never written down -- G9's own rule."""
    body = "4" + "1" * 14
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


class _Succeeds:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="ok", reference="proc-ref-1")


def _one_of_each(app, tenant_id) -> None:
    """One payment, one reversal, one charge attempt -- so every table has a
    row to try to change."""
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    payment_id, _ = record_payment(
        app, tenant_id, line.reference, PaymentMethod.CHEQUE, 100, MAY_8, recorded_by="op",
    )
    record_reversal(
        app, tenant_id, payment_id, ReversalReason.BOUNCED_CHEQUE, MAY_8, recorded_by="op",
    )
    attempt_charge(app, tenant_id, _Succeeds(), line.reference, recorded_by="cron", now=MAY_8)


def _refused(app, tenant_id, statement: str) -> bool:
    try:
        with tenant(app, tenant_id) as cursor:
            cursor.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        app.rollback()
        return True
    app.rollback()
    return False


@pytest.mark.guarantee("G20")
@pytest.mark.parametrize("table", APPEND_ONLY)
def test_the_application_cannot_update_a_row(app, tenant_id, table):
    _one_of_each(app, tenant_id)
    assert query(app, tenant_id, f"SELECT count(*) FROM {table}")[0][0] >= 1
    assert _refused(app, tenant_id, f"UPDATE {table} SET recorded_by = 'edited'"), (
        f"UPDATE on {table} was allowed; money history is never edited"
    )


@pytest.mark.guarantee("G20")
@pytest.mark.parametrize("table", APPEND_ONLY)
def test_the_application_cannot_delete_a_row(app, tenant_id, table):
    _one_of_each(app, tenant_id)
    assert _refused(app, tenant_id, f"DELETE FROM {table}"), (
        f"DELETE on {table} was allowed; money history is never removed"
    )


@pytest.mark.guarantee("G20")
def test_the_refusal_is_the_grant_and_not_the_policy(app, tenant_id):
    """Positive control on the instrument: the same role CAN update a table it is
    granted UPDATE on, inside its tenant, so the refusals above are the grants
    and not a policy quietly hiding every row."""
    _one_of_each(app, tenant_id)
    assert not _refused(app, tenant_id, "UPDATE invoices SET paid_at = paid_at")


HISTORY_TOO = ("invoice_lines", "owner_exceptions")


@pytest.mark.guarantee("G20")
@pytest.mark.parametrize("table", HISTORY_TOO)
def test_a_priced_line_and_a_recorded_decision_cannot_be_edited_or_removed(app, tenant_id, table):
    """0001 granted the application DML on everything; 0002 takes UPDATE and
    DELETE back off the invoice's lines and the owner's decisions. A control
    plants the revoke away and requires red."""
    _one_of_each(app, tenant_id)
    assert query(app, tenant_id, f"SELECT count(*) FROM {table}")[0][0] >= 0
    assert _refused(app, tenant_id, f"UPDATE {table} SET tenant_id = tenant_id"), (
        f"UPDATE on {table} was allowed"
    )
    assert _refused(app, tenant_id, f"DELETE FROM {table}"), f"DELETE on {table} was allowed"


@pytest.mark.guarantee("G20")
def test_an_invoice_cannot_be_deleted_but_its_derived_column_can_be_written(app, tenant_id):
    _one_of_each(app, tenant_id)
    assert _refused(app, tenant_id, "DELETE FROM invoices")
    assert not _refused(app, tenant_id, "UPDATE invoices SET paid_at = paid_at")


@pytest.mark.guarantee("G20")
def test_the_grants_read_from_the_catalogue_are_select_and_insert_only(app, tenant_id):
    rows = query(
        app, tenant_id,
        "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = 'monthly_billing_app' AND table_name = ANY(%s) "
        "GROUP BY table_name ORDER BY table_name",
        (list(APPEND_ONLY),),
    )
    assert rows == [(table, "INSERT,SELECT") for table in sorted(APPEND_ONLY)]
    rows = query(
        app, tenant_id,
        "SELECT table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) "
        "FROM information_schema.role_table_grants "
        "WHERE grantee = 'monthly_billing_app' AND table_name = ANY(%s) "
        "GROUP BY table_name ORDER BY table_name",
        (list(HISTORY_TOO) + ["invoices"],),
    )
    assert rows == [
        ("invoice_lines", "INSERT,SELECT"),
        ("invoices", "INSERT,SELECT,UPDATE"),
        ("owner_exceptions", "INSERT,SELECT"),
    ]


# ---------------------------------------------------------------------------
# G9, on the new tables
# ---------------------------------------------------------------------------


@pytest.mark.guarantee("G9")
def test_a_card_shaped_processor_reference_never_reaches_payments(app, tenant_id):
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    with pytest.raises(InstrumentRefusedAtTheStore) as refused:
        record_payment(
            app, tenant_id, line.reference, PaymentMethod.CHEQUE, 100, MAY_8,
            recorded_by="op", processor_reference=f"ref {card_shaped()}",
        )
    app.rollback()
    assert card_shaped() not in str(refused.value), "the refusal echoed the instrument"
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G9")
def test_a_card_shaped_processor_detail_never_reaches_charge_attempts(app, tenant_id):
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines

    class _Leaks:
        def charge(self, request):
            # ChargeResult refuses at construction; the store would refuse after.
            return ChargeResult(outcome=Outcome.DECLINE, detail=f"declined {card_shaped()}")

    from monthly_billing.payment import RESULT_UNKNOWN_DETAIL

    # The guard fires inside the processor's own return, so the module never
    # receives that result: the attempt is recorded as an ERROR whose detail says
    # the outcome is unknown, and the card-shaped text is in no row.
    outcome = attempt_charge(
        app, tenant_id, _Leaks(), line.reference, recorded_by="cron", now=MAY_8
    )
    assert outcome.result.outcome is Outcome.ERROR and outcome.payment_id is None
    rows = query(
        app, tenant_id, "SELECT kind, outcome, detail FROM charge_attempts ORDER BY created_at"
    )
    assert rows == [("attempt", None, ""), ("outcome", "error", RESULT_UNKNOWN_DETAIL)]
    assert all(card_shaped() not in detail for _, _, detail in rows)


def _uuid_that_reads_as_a_card():
    """A real uuid whose TEXT carries a digit-and-dash stretch the card check
    accepts. Searched for, not typed, so no card-shaped literal sits in the
    tree; the digits it settles on are whatever satisfy Luhn."""
    from uuid import UUID

    from monthly_billing.sensitive import find_instrument_like

    # The digit groups are BUILT, not written: the repository sweep (G9) reads
    # this file too, and a template carrying four groups of digits is a
    # card-shaped literal whatever it was meant to be -- it caught the first
    # draft of this test, which is exactly what it is for.
    group = "4" * 4
    for tail in range(100):
        # 4 + 4 + 4 + 5 digits between the first dash and the first letter:
        # seventeen, which is inside the card check's 13..19.
        candidate = UUID(f"0a0a0a0a-{group}-{group}-{group}-{group[:3]}{tail:02d}aaaaaaa")
        if find_instrument_like(str(candidate)) is not None:
            return candidate
    raise AssertionError("no candidate tripped the guard; the control cannot fire")


@pytest.mark.guarantee("G9")
def test_a_uuid_rendered_as_text_can_look_like_a_card_and_the_store_never_hands_the_guard_one():
    """THE FALSE POSITIVE THAT FOUND ITSELF. One store-backed test in roughly
    twenty failed with `agreements.payer_id contains a payment card number`: the
    uuid of a real row, rendered as text, had enough digits between its dashes
    to satisfy the card check. The guard was right to refuse -- it cannot tell a
    uuid from a card with dashes in it, and teaching it to would be a hole
    shaped like a card. So the store's ids are UUID objects, never text, and
    this proves both halves: the text form IS refused, the object form is not.
    """
    from monthly_billing.store import as_uuid, refuse_instrument_in_record

    trap = _uuid_that_reads_as_a_card()
    with pytest.raises(InstrumentRefusedAtTheStore):
        refuse_instrument_in_record("payments", {"invoice_id": str(trap)})
    refuse_instrument_in_record("payments", {"invoice_id": trap})  # the object passes
    assert as_uuid(str(trap)) == trap
