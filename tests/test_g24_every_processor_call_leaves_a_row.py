"""G24 -- every call to the processor is PRECEDED by its attempt row, and
followed by an outcome row recorded as it came.

The L3 found that a processor which RAISED left no row: the call ran outside
the insert, so a socket timeout after the request was sent -- the ordinary way
a card is charged with no record on our side -- recorded nothing and counted
toward nothing. The outside pass then found the row was written AFTER the
processor was called, so a crash between the two left nothing either. Now the
reservation is committed before the processor is asked -- a processor can see
it from another connection while it is being called -- and the outcome is a
second row. The controls: the wrapper planted away (the raise propagates again),
and the reservation's commit planted away (the processor sees no row).
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
    rows = query(
        app, tenant_id, "SELECT kind, outcome, detail FROM charge_attempts ORDER BY created_at"
    )
    assert rows == [("attempt", None, ""), ("outcome", "error", outcome.result.detail)]
    assert query(app, tenant_id, "SELECT count(*) FROM payments") == [(0,)]


@pytest.mark.guarantee("G24")
def test_a_raise_counts_toward_the_three(app, tenant_id):
    reference = _issued(app, tenant_id)
    for n in range(MAX_ATTEMPTS):
        attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(n))
    with pytest.raises(Refused) as refused:
        attempt_charge(app, tenant_id, _Raises(), reference, recorded_by="cron", now=_tick(9))
    assert refused.value.code == REFUSAL_RETRIES_EXHAUSTED
    assert query(
        app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'outcome'"
    ) == [(MAX_ATTEMPTS,)]
    assert query(
        app, tenant_id, "SELECT count(*) FROM charge_attempts WHERE kind = 'attempt'"
    ) == [(MAX_ATTEMPTS,)], "the refusal wrote a fourth reservation"


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
    rows = query(
        app, tenant_id, "SELECT outcome, detail FROM charge_attempts WHERE kind = 'outcome'"
    )
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
    rows = query(
        app, tenant_id, "SELECT outcome, detail FROM charge_attempts WHERE kind = 'outcome'"
    )
    assert rows == [("error", DETAIL_WITHHELD)]


@pytest.mark.guarantee("G24")
def test_the_reservation_is_committed_before_the_processor_is_asked(app, tenant_id):
    """The processor reads the log from ANOTHER connection while it is being
    called: the reservation is already there, committed, carrying the amount it
    is being asked for and the attempt id it was handed as its idempotency key.
    The control plants the reservation's commit away and requires red."""
    from store_harness import DSN, app_connection

    reference = _issued(app, tenant_id)

    class _LooksAtTheLog:
        def __init__(self) -> None:
            self.saw: list = []

        def charge(self, request):
            other = app_connection(DSN)
            try:
                self.saw = query(
                    other, tenant_id,
                    "SELECT kind, attempt_id, amount_minor, currency FROM charge_attempts",
                )
            finally:
                other.close()
            self.key = request.idempotency_key
            return ChargeResult(outcome=Outcome.DECLINE, detail="declined")

    processor = _LooksAtTheLog()
    outcome = attempt_charge(app, tenant_id, processor, reference, recorded_by="cron", now=_tick(1))
    assert processor.saw == [("attempt", outcome.attempt_id, 12000, "USD")], (
        "the processor was called before its reservation was committed"
    )
    assert processor.key == outcome.attempt_id
