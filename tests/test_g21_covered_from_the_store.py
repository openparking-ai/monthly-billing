"""G21 -- the store-backed entitlement call returns the pure answer, derives
"unpaid since" from the invoices, and honours the owner's grace extensions
and blocks.

**THE FIELD SET IS THE PURE ANSWER'S, READ FROM THE CLASS.** The store-backed
call returns ``entitlement.Answer`` -- G6 already derives that class's field
set and forbids money in it. The test here asserts the returned object IS that
class and carries exactly its fields, so a wrapper that added a balance would be
caught the day it existed.

Two controls: one plants a call that reads the garage's base grace and ignores
the owner's extension; the other plants a call that ignores a block. Both must
turn these tests red.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.entitlement import Answer, is_covered
from monthly_billing.entitlement_store import covered_from_store
from monthly_billing.exceptions_by_owner import ExceptionKind, OwnerException
from monthly_billing.exceptions_store import record_agreement_exception, record_invoice_exception
from monthly_billing.payments import PaymentMethod, record_payment
from store_harness import needs_postgres, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()  # grace 5 days; bills on the last day of the month
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _issued_unpaid(app, tenant_id, **overrides):
    """One agreement from January; its May invoice issued 2026-04-30, unpaid."""
    agreement = simple_agreement(start_day=date(2026, 1, 5), **overrides)
    seeded = seed(app, tenant_id, GARAGE, (agreement,))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return seeded, line.reference


@pytest.mark.guarantee("G21")
def test_the_answer_is_the_pure_answer_class_with_its_field_set(app, tenant_id):
    _issued_unpaid(app, tenant_id)
    answer = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 3)))
    assert type(answer) is Answer
    assert {f.name for f in dataclasses.fields(answer)} == {
        f.name for f in dataclasses.fields(Answer)
    }
    assert answer.covered and answer.entitlement == 10


@pytest.mark.guarantee("G21")
def test_unpaid_past_grace_reads_not_covered_and_a_cheque_reads_covered_again(app, tenant_id):
    """The second month, end to end: due 04-30, grace 5 days, transient from the
    6th, covered again once the cheque is recorded."""
    _, reference = _issued_unpaid(app, tenant_id)

    inside = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 5)))
    assert inside.covered, "within grace: 5 days after 04-30 is still covered"

    past = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 6)))
    assert not past.covered and past.reason_code == "UNPAID_PAST_GRACE"
    assert "ordinary transient" in past.reason

    record_payment(
        app, tenant_id, reference, PaymentMethod.CHEQUE, 12000, _at(date(2026, 5, 8)),
        recorded_by="operator",
    )
    again = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 9)))
    assert again.covered


@pytest.mark.guarantee("G21")
def test_unpaid_since_is_the_earliest_unpaid_due_date(app, tenant_id):
    """Two unpaid invoices: the answer follows the OLDER one's due date."""
    _, may = _issued_unpaid(app, tenant_id)
    (june_line,) = run_billing(
        app, tenant_id, GARAGE.id, date(2026, 6, 1), now=_at(date(2026, 5, 31), 0)
    ).lines
    # Pay June, leave May unpaid: still not covered, because May is past grace.
    record_payment(
        app, tenant_id, june_line.reference, PaymentMethod.ACH, 12000, _at(date(2026, 6, 1)),
        recorded_by="operator",
    )
    answer = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 6, 2)))
    assert not answer.covered and answer.reason_code == "UNPAID_PAST_GRACE"
    # The pure function, handed the same earliest date, says the same thing.
    pure = is_covered(
        garage=GARAGE, agreements=(simple_agreement(start_day=date(2026, 1, 5)),),
        vehicle_identity="CAR001", at=_at(date(2026, 6, 2)),
        has_unpaid_invoice_since=_at(date(2026, 4, 30), 0),
    )
    assert pure.reason_code == answer.reason_code


@pytest.mark.guarantee("G21")
def test_an_owners_grace_extension_on_the_invoice_is_honoured(app, tenant_id):
    _, reference = _issued_unpaid(app, tenant_id)
    when = _at(date(2026, 5, 7))
    assert not covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered

    record_invoice_exception(
        app, tenant_id,
        OwnerException(
            id="exc-grace", agreement_id=None, invoice_reference=reference,
            kind=ExceptionKind.EXTEND_GRACE, recorded_by="the owner",
            recorded_at=_at(date(2026, 5, 6)), note="another week", extra_grace_days=7,
        ),
    )
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered, (
        "the owner extended grace by 7 days and the lane ignored it"
    )
    # 5 + 7 = 12 days from 04-30: the 13th is the first uncovered day.
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 12))).covered
    assert not covered_from_store(
        app, tenant_id, GARAGE.id, "CAR001", _at(date(2026, 5, 13))
    ).covered


@pytest.mark.guarantee("G21")
def test_an_owners_grace_extension_on_the_agreement_is_honoured_too(app, tenant_id):
    seeded, _ = _issued_unpaid(app, tenant_id)
    when = _at(date(2026, 5, 7))
    assert not covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered
    record_agreement_exception(
        app, tenant_id, seeded.agreement_uuids["ag-0001"],
        OwnerException(
            id="exc-grace-2", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.EXTEND_GRACE, recorded_by="the owner",
            recorded_at=_at(date(2026, 5, 6)), extra_grace_days=3,
        ),
    )
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered


@pytest.mark.guarantee("G21")
def test_an_owners_block_is_honoured_and_an_unblock_lifts_it(app, tenant_id):
    seeded, _ = _issued_unpaid(app, tenant_id)
    when = _at(date(2026, 5, 3))
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered

    agreement_uuid = seeded.agreement_uuids["ag-0001"]
    record_agreement_exception(
        app, tenant_id, agreement_uuid,
        OwnerException(
            id="exc-block", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.BLOCK, recorded_by="the owner", recorded_at=_at(date(2026, 5, 1)),
        ),
    )
    blocked = covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when)
    assert not blocked.covered and blocked.reason_code == "BLOCKED_BY_OWNER"
    assert "never means refuse exit" in blocked.reason

    record_agreement_exception(
        app, tenant_id, agreement_uuid,
        OwnerException(
            id="exc-unblock", agreement_id="ag-0001", invoice_reference=None,
            kind=ExceptionKind.UNBLOCK, recorded_by="the owner", recorded_at=_at(date(2026, 5, 2)),
        ),
    )
    assert covered_from_store(app, tenant_id, GARAGE.id, "CAR001", when).covered


@pytest.mark.guarantee("G21")
def test_a_vehicle_on_no_agreement_reads_no_agreement(app, tenant_id):
    _issued_unpaid(app, tenant_id)
    answer = covered_from_store(app, tenant_id, GARAGE.id, "NOT-ENROLLED", _at(date(2026, 5, 3)))
    assert not answer.covered and answer.reason_code == "NO_AGREEMENT"


@pytest.mark.guarantee("G21")
def test_the_command_line_asks_the_store_and_exits_by_the_answer(app, tenant_id, capsys):
    from monthly_billing.cli import main
    from store_harness import APP_PASSWORD, DSN

    _issued_unpaid(app, tenant_id)
    store = ["--tenant", str(tenant_id), "--dsn",
             f"{DSN} user=monthly_billing_app password={APP_PASSWORD}"]
    assert main(["covered-in-store", *store, "--garage", GARAGE.id, "--vehicle", "CAR001",
                 "--at", _at(date(2026, 5, 3)).isoformat()]) == 0
    assert capsys.readouterr().out.startswith("COVERED")
    assert main(["covered-in-store", *store, "--garage", GARAGE.id, "--vehicle", "CAR001",
                 "--at", _at(date(2026, 5, 7)).isoformat()]) == 1
    out = capsys.readouterr().out
    assert out.startswith("NOT COVERED") and "never means refuse exit" in out
    assert main(["covered-in-store", *store, "--garage", "no-such-garage", "--vehicle", "CAR001",
                 "--at", _at(date(2026, 5, 7)).isoformat()]) == 2
    assert "NOT FOUND" in capsys.readouterr().err
