"""G22 -- the charge log is the truth for retries.

Three persisted non-success attempts since the last persisted method change
refuse the fourth by name; a persisted method change allows it; and the state
is REBUILT from rows, so a fresh caller (a restart) sees the same count as the
one that made the attempts.

The control plants a rebuild that ignores the ``payment_method_changed`` rows
and requires the fourth-attempt-after-a-change test to go red -- the reset
would then be a row nobody reads, which is the "lie on restart" the brief
named.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import (
    attempt_charge,
    record_payment_method_changed,
    retry_state_from_rows,
)
from monthly_billing.findings import Refused
from monthly_billing.payment import MAX_ATTEMPTS, ChargeResult, Outcome, StubProcessor
from monthly_billing.payments import PaymentMethod
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


class _Declines:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.DECLINE, detail="insufficient funds")


class _Succeeds:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference="auth-77")


def _issued(app, tenant_id, **overrides) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5), **overrides),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


@pytest.mark.guarantee("G22")
def test_three_persisted_declines_refuse_the_fourth_by_name(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        outcome = attempt_charge(
            app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n)
        )
        assert outcome.result.outcome is Outcome.DECLINE
        assert outcome.retry.attempts == n + 1
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(9))
    assert refused.value.code == "REFUSAL_RETRIES_EXHAUSTED"
    rows = query(app, tenant_id, "SELECT kind, outcome FROM charge_attempts ORDER BY occurred_at")
    assert rows == [("attempt", "decline")] * MAX_ATTEMPTS, "the refusal wrote no fourth row"


@pytest.mark.guarantee("G22")
def test_a_persisted_method_change_allows_the_fourth(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        attempt_charge(app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n))
    state = record_payment_method_changed(
        app, tenant_id, reference, recorded_by="portal", now=_tick(5)
    )
    assert state.attempts == 0
    fourth = attempt_charge(
        app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(6)
    )
    assert fourth.retry.attempts == 1
    rows = query(app, tenant_id, "SELECT kind FROM charge_attempts ORDER BY occurred_at")
    assert [r[0] for r in rows] == ["attempt"] * 3 + ["payment_method_changed", "attempt"]


@pytest.mark.guarantee("G22")
def test_the_state_survives_a_restart_because_it_is_rebuilt_from_rows(app, tenant_id):
    """A fresh rebuild from the rows equals what the attempting caller was told."""
    reference = _issued(app, tenant_id)
    told = None
    for n in range(2):
        told = attempt_charge(
            app, tenant_id, _Declines(), reference, recorded_by="cron", now=_tick(n)
        ).retry
    rows = query(
        app, tenant_id,
        "SELECT kind, outcome, detail FROM charge_attempts ORDER BY occurred_at, created_at",
    )
    rebuilt = retry_state_from_rows(reference, rows)
    assert rebuilt == told
    assert rebuilt.attempts == 2


@pytest.mark.guarantee("G22")
def test_a_success_records_the_card_payment_and_pays_the_invoice(app, tenant_id):
    """THE ONLY PATH THAT WRITES A CARD PAYMENT."""
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _Succeeds(), reference, recorded_by="cron", now=_tick(1)
    )
    assert outcome.result.outcome is Outcome.SUCCESS
    assert outcome.payment_id is not None and outcome.paid is not None and outcome.paid.paid
    assert query(
        app, tenant_id, "SELECT method, amount_minor, processor_reference FROM payments"
    ) == [(PaymentMethod.CARD.value, 12000, "auth-77")]
    assert query(app, tenant_id, "SELECT kind, outcome FROM charge_attempts") == [
        ("attempt", "success")
    ]


@pytest.mark.guarantee("G22")
def test_the_stub_processor_moves_no_money_and_its_attempt_is_still_recorded(app, tenant_id):
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, StubProcessor(), reference, recorded_by="cron", now=_tick(1)
    )
    assert outcome.result.outcome is Outcome.ERROR and outcome.payment_id is None
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]
    assert query(app, tenant_id, "SELECT outcome FROM charge_attempts") == [("error",)]


@pytest.mark.guarantee("G22")
def test_no_mandate_refuses_before_the_processor_and_writes_no_row(app, tenant_id):
    reference = _issued(app, tenant_id, with_mandate=False)
    processor = StubProcessor()
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert refused.value.code == "REFUSAL_NO_MANDATE"
    assert processor.requests == [], "the processor was called before the refusal"
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(0,)]
