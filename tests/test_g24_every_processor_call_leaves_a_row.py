"""G24 -- every call to the processor leaves an attempt row, whatever it did.

The L3 found that a processor which RAISED left no row: the call ran outside
the insert, so a socket timeout after the request was sent -- the ordinary way
a card is charged with no record on our side -- recorded nothing and counted
toward nothing. A result the instrument guard refused inside the processor's
own return did the same. The control plants the wrapper away (the raise
propagates again) and requires red.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fixtures import month_end_garage, simple_agreement
from monthly_billing.billing_run import run_billing
from monthly_billing.charging import attempt_charge
from monthly_billing.findings import REFUSAL_RETRIES_EXHAUSTED, Refused
from monthly_billing.payment import (
    DETAIL_WITHHELD,
    MAX_ATTEMPTS,
    RESULT_UNKNOWN_DETAIL,
    ChargeResult,
    Outcome,
)
from monthly_billing.sensitive import luhn_ok
from store_harness import needs_postgres, query, seed

pytestmark = needs_postgres

GARAGE = month_end_garage()
TZ = ZoneInfo(GARAGE.timezone)
NOW = datetime(2026, 4, 30, 0, 30, tzinfo=TZ)


def _card_shaped() -> str:
    body = "4" + "1" * 14
    for check in "0123456789":
        if luhn_ok(body + check):
            return body + check
    raise AssertionError("unreachable")


class _Raises:
    def __init__(self) -> None:
        self.calls = 0

    def charge(self, request):
        self.calls += 1
        raise TimeoutError("socket timed out after the request was sent")


class _RaisesWithAnInstrumentInTheMessage:
    def charge(self, request):
        raise RuntimeError(f"gateway said: {_card_shaped()}")


class _SucceedsWithAnInstrumentShapedReference:
    def charge(self, request):
        return ChargeResult(outcome=Outcome.SUCCESS, detail="approved", reference=_card_shaped())


def _issued(app, tenant_id) -> str:
    seed(app, tenant_id, GARAGE, (simple_agreement(start_day=date(2026, 1, 5)),))
    (line,) = run_billing(app, tenant_id, GARAGE.id, date(2026, 5, 1), now=NOW).lines
    return line.reference


def _tick(n: int) -> datetime:
    return NOW + timedelta(hours=n)


@pytest.mark.guarantee("G24")
def test_a_processor_that_raises_leaves_an_error_row_and_the_call_returns(app, tenant_id):
    reference = _issued(app, tenant_id)
    processor = _Raises()
    outcome = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert processor.calls == 1
    assert outcome.result.outcome is Outcome.ERROR and outcome.payment_id is None
    assert outcome.result.detail.startswith("TimeoutError: socket timed out")
    rows = query(app, tenant_id, "SELECT kind, outcome, detail FROM charge_attempts")
    assert rows == [("attempt", "error", outcome.result.detail)]
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G24")
def test_a_raise_counts_toward_the_three(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(n))
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(9))
    assert refused.value.code == REFUSAL_RETRIES_EXHAUSTED
    assert query(app, tenant_id, "SELECT count(*) FROM charge_attempts") == [(MAX_ATTEMPTS,)]


@pytest.mark.guarantee("G24")
def test_a_result_the_guard_refused_is_an_error_row_saying_unknown(app, tenant_id):
    """The guard fires inside the processor's own return, so the module never
    receives the SUCCESS. It records an error whose detail says the outcome is
    unknown, and no payment -- it does not know money moved."""
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _SucceedsWithAnInstrumentShapedReference(), reference,
        recorded_by="cron", now=_tick(1),
    )
    assert outcome.result.outcome is Outcome.ERROR and outcome.payment_id is None
    rows = query(app, tenant_id, "SELECT outcome, detail FROM charge_attempts")
    assert rows == [("error", RESULT_UNKNOWN_DETAIL)]
    assert _card_shaped() not in rows[0][1]
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G24")
def test_a_row_is_never_lost_to_its_own_message(app, tenant_id):
    reference = _issued(app, tenant_id)
    outcome = attempt_charge(
        app, tenant_id, _RaisesWithAnInstrumentInTheMessage(), reference,
        recorded_by="cron", now=_tick(1),
    )
    assert outcome.result.outcome is Outcome.ERROR
    rows = query(app, tenant_id, "SELECT outcome, detail FROM charge_attempts")
    assert rows == [("error", DETAIL_WITHHELD)]
